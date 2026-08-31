"""Was die Anbindung an echte Quellsysteme über den Mock hinaus verlangt (§8, §9.4).

Die Adapter liefen von Anfang an gegen den Mock-Server. Was hier geprüft wird, ist das, was
zwischen Mock und Betrieb liegt und deshalb bisher nie durchlaufen wurde: ein API-Gateway mit
eigenem Pfadpräfix und zweiter Kopfzeile, ein geteilter Nummernkreis über mehrere Quellblöcke,
eine Antwortstruktur mit tiefer verschachtelten Labels — und die Frage, welcher Host über den
Proxy muss und welcher an ihm vorbei.

Der Mock-Server bedient beide Welten. Genau das ist der Punkt: Was hier gegen ihn läuft, läuft
denselben Codepfad wie später gegen die echte Instanz (§9.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from support import quellen
from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import (
    SourceConnectionConfig,
    SourcesConfig,
    load_sources,
)
from wissensgraph.infrastructure.adapters.confluence import ConfluenceAdapter
from wissensgraph.infrastructure.adapters.jira import JiraAdapter
from wissensgraph.ports.sources import HealthState

pytestmark = pytest.mark.unit


def nicht_warten(_: float) -> None:
    """Backoff ohne Wartezeit — geprüft wird das Verhalten, nicht die Uhr."""


@pytest.fixture
def app() -> Any:
    """Eine frische Mock-Anwendung je Test."""
    return quellen.mock_app()


def confluence(app: Any, **verbindung: Any) -> ConfluenceAdapter:
    """Ein Confluence-Adapter auf dem Gateway-Zugang des Mock-Servers."""
    basis: dict[str, Any] = {
        "base_url": quellen.CONFLUENCE_GATEWAY_BASE,
        "web_base_url": "https://itdoc.example",
        "api_prefix": "",
        "extra_headers": {"x-apikey": "geheim"},
        "rate_limit_per_second": 0,
        "retries": 0,
    }
    basis.update(verbindung)
    cfg = quellen.quelle(
        "confluence-live", adapter="confluence", id_prefix="confluence", connection=basis
    )
    adapter = ConfluenceAdapter(client_factory=quellen.client_factory(app), sleep=nicht_warten)
    adapter.configure(cfg)
    return adapter


def jira(app: Any, **rest: Any) -> JiraAdapter:
    """Ein Jira-Adapter auf der Data-Center-Version des Mock-Servers."""
    verbindung: dict[str, Any] = {
        "base_url": quellen.JIRA_BASE,
        "web_base_url": "https://jira.example",
        "rate_limit_per_second": 0,
        "retries": 0,
    }
    verbindung.update(rest.pop("connection", {}))
    cfg = quellen.quelle(
        "jira-live",
        adapter="jira",
        id_prefix="jira",
        default_type="Jira Issue",
        connection=verbindung,
        **rest,
    )
    adapter = JiraAdapter(client_factory=quellen.client_factory(app), sleep=nicht_warten)
    adapter.configure(cfg)
    return adapter


class TestGateway:
    """Ein API-Gateway ändert zwei Dinge: den Pfad und die Kopfzeilen."""

    def test_ohne_praefix_antwortet_das_gateway(self, app: Any) -> None:
        """``api_prefix: ""`` ist der ganze Unterschied zur Standardinstallation."""
        assert confluence(app).health().state is HealthState.HEALTHY

    def test_ohne_den_schluessel_gibt_es_nichts(self, app: Any) -> None:
        """Ein 401 des Gateways sähe sonst wie ein Auth-Fehler des Quellsystems aus — und würde
        an der falschen Stelle gesucht."""
        zustand = confluence(app, extra_headers={}).health()

        assert zustand.state is HealthState.UNHEALTHY
        assert "401" in zustand.detail

    def test_die_standardinstallation_laeuft_unveraendert_weiter(self, app: Any) -> None:
        """Das Gateway ist eine Möglichkeit, keine Umstellung."""
        adapter = confluence(
            app,
            base_url=quellen.CONFLUENCE_BASE,
            api_prefix=None,
            extra_headers={},
        )

        assert adapter.health().state is HealthState.HEALTHY

    def test_eine_reservierte_kopfzeile_wird_abgewiesen(self) -> None:
        """Sonst verdrängte ein Eintrag in ``extra_headers`` still das Token aus ``token``."""
        with pytest.raises(ValueError, match="setzt der Adapter selbst"):
            SourceConnectionConfig(extra_headers={"Authorization": "Bearer x"})


class TestConfluenceInhalt:
    """Was aus einer echten Seite wird."""

    def test_der_body_ist_markdown_und_kein_storage_format(self, app: Any) -> None:
        seite = confluence(app).fetch("100001")

        assert seite is not None
        assert seite.body is not None
        assert "<ac:" not in seite.body
        assert "## Ablauf" in seite.body

    def test_labels_kommen_aus_beiden_antwortformen(self, app: Any) -> None:
        """``metadata.labels.results`` (Data Center) und ``metadata.labels`` (ältere Form).

        Nur eine zu lesen kostet im anderen Fall sämtliche Tags — lautlos.
        """
        adapter = confluence(app)
        flach = adapter.fetch("100001")
        verschachtelt = adapter.fetch("100002")

        assert flach is not None and verschachtelt is not None
        assert "datenpipeline" in flach.tags
        assert "datenpipeline" in verschachtelt.tags

    def test_die_ressource_zeigt_auf_die_weboberflaeche(self, app: Any) -> None:
        """Nicht auf die API: Ein Leser käme mit der Gateway-Adresse nicht weit."""
        seite = confluence(app).fetch("100001")

        assert seite is not None
        assert seite.resource is not None
        assert seite.resource.startswith("https://itdoc.example")

    def test_ein_seitenverweis_wird_zu_einer_referenz(self, app: Any) -> None:
        seite = confluence(app).fetch("100001")

        assert seite is not None
        assert "confluence:100002" in [verweis.target for verweis in seite.references]

    def test_die_titelsuche_wird_nur_einmal_gefragt(self, app: Any) -> None:
        """Der Zwischenspeicher ist keine Bequemlichkeit: Ohne ihn kostet jede Erwähnung
        derselben Zielseite eine eigene Anfrage — mal Rate-Limit."""
        adapter = confluence(app)
        gezaehlt: list[tuple[str, str]] = []
        echte_suche = adapter._titel_suchen

        def zaehlen(space: str, titel: str) -> str | None:
            gezaehlt.append((space, titel))
            return echte_suche(space, titel)

        adapter._titel_suchen = zaehlen  # type: ignore[method-assign]
        adapter.fetch("100001")
        adapter.fetch("100001")

        assert len(gezaehlt) == 2  # zweimal gefragt …
        assert len(adapter._titel_cache) == 1  # … aber nur einmal gesucht


class TestJiraBeziehungen:
    """Strukturierte Beziehungen sind Tatsachen aus der Quelle (§7.7, Leitprinzip 6)."""

    def test_der_body_ist_markdown_und_kein_wiki_markup(self, app: Any) -> None:
        vorgang = jira(app).fetch("TEAM-1")

        assert vorgang is not None
        assert vorgang.body is not None
        assert "{code:python}" not in vorgang.body
        assert "```python" in vorgang.body

    def test_eine_unteraufgabe_wird_ein_mitglied(self, app: Any) -> None:
        """Die Richtung ist die von ``member``: der Behälter zeigt auf den Inhalt."""
        vorgang = jira(app).fetch("TEAM-1")

        assert vorgang is not None
        assert ("jira:TEAM-2", defaults.EDGE_KIND_MEMBER) in _verweise(vorgang)

    def test_blockiert_wird_zur_abhaengigkeit(self, app: Any) -> None:
        """Notiert wird sie von der blockierten Seite — der einzigen, die sie schreiben kann."""
        vorgang = jira(app).fetch("TEAM-1")

        assert vorgang is not None
        assert ("jira:TEAM-3", defaults.EDGE_KIND_DEPENDS_ON) in _verweise(vorgang)

    def test_eine_lose_verknuepfung_wird_related(self, app: Any) -> None:
        vorgang = jira(app).fetch("TEAM-1")

        assert vorgang is not None
        assert ("jira:TEAM-5", defaults.EDGE_KIND_RELATED) in _verweise(vorgang)

    def test_der_elternvorgang_behauptet_kein_enthaltensein(self, app: Any) -> None:
        """Ein ``member`` mit vertauschten Enden wäre keine Notlösung, sondern falsch: Die
        Katalogschicht liest ``from_id`` als Behälter, das Kind erschiene als Cluster."""
        vorgang = jira(app).fetch("TEAM-2")

        assert vorgang is not None
        arten = {art for ziel, art in _verweise(vorgang) if ziel == "jira:TEAM-1"}
        assert defaults.EDGE_KIND_MEMBER not in arten
        assert defaults.EDGE_KIND_RELATED in arten

    def test_remote_links_sind_abschaltbar(self, app: Any) -> None:
        """Sie kosten eine Anfrage je Vorgang. Was Zeit kostet, ist eine Entscheidung (§8.4)."""
        ohne = jira(app).fetch("TEAM-1")
        mit = jira(app, selection={"remote_links": True}).fetch("TEAM-1")

        assert ohne is not None and mit is not None
        assert "confluence:100001" not in [v.target for v in ohne.references]
        assert "confluence:100001" in [v.target for v in mit.references]

    def test_beide_api_versionen_antworten(self, app: Any) -> None:
        """Data Center kennt nur ``2``; als Literal im Code wäre der Adapter darauf festgelegt."""
        for praefix in ("/rest/api/2", "/rest/api/3"):
            adapter = jira(app, connection={"api_prefix": praefix})

            assert adapter.fetch("TEAM-1") is not None

    def test_die_feldliste_wird_mitgeschickt(self, app: Any) -> None:
        """``fields`` verkleinert die Antwort — und damit alles, was danach über sie läuft."""
        adapter = jira(app, selection={"fields": ["summary", "description"]})

        assert list(adapter.iter_documents(None))


class TestGeteiltesPraefix:
    """Mehrere Ausschnitte einer Instanz teilen sich einen Nummernkreis (§7.5)."""

    def bloecke(self, **rest: Any) -> list[dict[str, Any]]:
        gemeinsam: dict[str, Any] = {"adapter": "confluence", "id_prefix": "confluence"}
        gemeinsam.update(rest)
        return [
            {
                "name": "confluence-eng",
                "target": {"scope": "engineering", "default_type": "Confluence Page"},
                **gemeinsam,
            },
            {
                "name": "confluence-fin",
                "target": {"scope": "finance", "default_type": "Confluence Page"},
                **gemeinsam,
            },
        ]

    def test_ohne_zustimmung_bleibt_es_ein_fehler(self) -> None:
        """Der Regelfall: Zwei Quellen mit einem Präfix überschreiben einander."""
        with pytest.raises(ValueError, match="shared_id_prefix"):
            SourcesConfig.model_validate({"sources": self.bloecke()})

    def test_mit_zustimmung_beider_seiten_ist_es_erlaubt(self) -> None:
        """Vier Spaces in vier Scopes brauchen vier Blöcke — und *einen* Nummernkreis, sonst
        ließe sich ein Verweis über die Space-Grenze gar nicht aufschreiben."""
        config = SourcesConfig.model_validate({"sources": self.bloecke(shared_id_prefix=True)})

        assert len(config.sources) == 2

    def test_eine_seite_allein_genuegt_nicht(self) -> None:
        eintraege = self.bloecke()
        eintraege[0]["shared_id_prefix"] = True

        with pytest.raises(ValueError, match="es fehlt bei: confluence-fin"):
            SourcesConfig.model_validate({"sources": eintraege})

    def test_zwei_systeme_haben_keinen_gemeinsamen_nummernkreis(self) -> None:
        eintraege = self.bloecke(shared_id_prefix=True)
        eintraege[1]["adapter"] = "jira"
        eintraege[1]["target"]["default_type"] = "Jira Issue"

        with pytest.raises(ValueError, match="verschiedene Adapter"):
            SourcesConfig.model_validate({"sources": eintraege})

    def test_ein_geteiltes_praefix_ueber_die_storegrenze_wird_abgewiesen(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Über diese Grenze läuft Leitprinzip 2.

        Dieselbe Konzept-ID gäbe es zweimal — einmal in ``shared``, einmal in ``personal``. Eine
        Referenz nennt nur die ID; welche gemeint ist, entschiede die Fundreihenfolge, und ein
        Verweis könnte persönliche Inhalte in einen Zusammenhang ziehen, in den sie nicht gehören.
        """
        eintraege = self.bloecke(shared_id_prefix=True)
        eintraege[1]["target"] = {"scope": "personal", "default_type": "Note"}
        pfad = tmp_path / "sources.yaml"
        pfad.write_text(yaml.safe_dump({"sources": eintraege}), encoding="utf-8")

        with pytest.raises(ConfigValidationError, match="verschiedene Stores"):
            load_sources(settings, path=pfad, env={})


class TestProxyZugehoerigkeit:
    """Welcher Host über den Proxy muss und welcher an ihm vorbei (§5.2)."""

    @pytest.mark.parametrize(
        ("url", "intern"),
        [
            ("http://mock-sources:8090/confluence", True),
            ("http://broker:6379", True),
            ("http://localhost:8090", True),
            ("http://host.docker.internal:8090", True),
            ("https://jira.schwarz", False),
            ("https://live.api.schwarz/sit/itdoc/v1", False),
        ],
    )
    def test_die_form_des_namens_entscheidet(self, url: str, intern: bool) -> None:
        """Ein Compose-Dienst hat keinen Punkt im Namen, ein Host im Netz hat einen."""
        assert SourceConnectionConfig(base_url=url).is_internal is intern

    def test_die_angabe_schlaegt_die_ableitung(self) -> None:
        """Für den internen Dienst, der unter seinem FQDN erreichbar ist."""
        verbindung = SourceConnectionConfig(base_url="https://wiki.intern.example", internal=True)

        assert verbindung.is_internal is True

    def test_ohne_adresse_gilt_nichts_als_intern(self) -> None:
        assert SourceConnectionConfig().is_internal is False


class TestLaeufeUndStoerungen:
    """Die Wege, die ein Lauf nimmt — und die, auf denen er nicht abbrechen darf."""

    def test_ein_vollstaendiger_confluence_lauf(self, app: Any) -> None:
        dokumente = list(confluence(app).iter_documents(None))

        assert len(dokumente) == 120

    def test_ausgeschlossene_labels_werden_uebergangen(self, app: Any) -> None:
        adapter = confluence(app)
        alle = len(list(adapter.iter_documents(None)))
        gefiltert = confluence(app)
        gefiltert.config.selection["exclude_labels"] = ["datenpipeline"]

        assert len(list(gefiltert.iter_documents(None))) < alle

    def test_geloeschte_seiten_werden_gemeldet(self, app: Any) -> None:
        quellen.control(app).post("/_control/scenario/deletion")

        assert list(confluence(app).list_deleted(None)) == ["100003"]

    def test_geloeschte_vorgaenge_werden_gemeldet(self, app: Any) -> None:
        quellen.control(app).post("/_control/scenario/deletion")

        assert list(jira(app).list_deleted(None)) == ["TEAM-2"]

    def test_eine_unbekannte_seite_ergibt_nichts(self, app: Any) -> None:
        assert confluence(app).fetch("999999") is None

    def test_ein_unbekannter_vorgang_ergibt_nichts(self, app: Any) -> None:
        assert jira(app).fetch("TEAM-9999") is None

    def test_eine_gescheiterte_titelsuche_kostet_kein_dokument(self, app: Any) -> None:
        """§8.5: Eine Titelsuche entscheidet über eine Kante, nicht über den Inhalt.

        Erzwungen wird der Fehler nur auf dem Suchpfad — der Seitenabruf selbst gelingt. Ohne die
        Absicherung im Adapter risse die Störung den ganzen Lauf mit.
        """
        quellen.control(app).post(
            "/_control/fail",
            json={"status": 500, "count": 10, "path_prefix": "/gateway/confluence/content/search"},
        )

        seite = confluence(app).fetch("100001")

        assert seite is not None
        assert seite.body is not None
        assert "confluence:100002" not in [verweis.target for verweis in seite.references]

    def test_gescheiterte_remote_links_kosten_kein_dokument(self, app: Any) -> None:
        quellen.control(app).post(
            "/_control/fail",
            json={"status": 500, "count": 10, "path_prefix": "/jira/rest/api/2/issue/TEAM-1/"},
        )

        vorgang = jira(app, selection={"remote_links": True}).fetch("TEAM-1")

        assert vorgang is not None
        assert "confluence:100001" not in [verweis.target for verweis in vorgang.references]


def _verweise(dokument: Any) -> set[tuple[str, str]]:
    """Die Verweise eines Dokuments als Menge aus Ziel und Art."""
    return {(verweis.target, verweis.kind) for verweis in dokument.references}
