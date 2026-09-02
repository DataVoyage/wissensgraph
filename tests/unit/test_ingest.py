"""Der Fixture-Korpus als Konzepte — die Abnahme der Stufe 3 ohne Datenbank (§24, §10.1).

Zwei der vier Abnahmekriterien stehen hier: "der Fixture-Korpus ist vollständig als Konzepte
abgebildet" und "das Änderungsszenario führt beim zweiten Lauf nur zu den erwarteten
Aktualisierungen". Gegen die speicherresidenten Ports und den Mock-Server im selben Prozess —
also auf jedem Rechner, auch ohne Docker. Dieselben Kriterien laufen in
``tests/integration/test_quellen_postgres.py`` noch einmal gegen echtes PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from support import quellen
from support.memory import MemoryUnitOfWorkFactory
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourceConfig
from wissensgraph.infrastructure.adapters import ConfluenceAdapter, JiraAdapter
from wissensgraph.ports.sources import SourceError
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.sources import SourceIngestService

pytestmark = pytest.mark.unit

#: Der Umfang des Korpus aus §9.2.
SEITEN = 120
VORGAENGE = 80

#: Die Verweise aus ``links.json`` — je einer entlang der Themenketten, plus die beiden
#: Sonderfälle (Brücke und depends_on-Paar) mit je zwei Zielen.
CONFLUENCE_KANTEN = 118


def nicht_warten(_seconds: float) -> None:
    """Der Backoff soll im Test keine Zeit kosten."""


class Umgebung:
    """Ein vollständiger Lauf-Aufbau: Mock-Server, Adapter, Dienste, Speicher."""

    def __init__(self, settings: Settings) -> None:
        self.app = quellen.mock_app()
        self.factory = MemoryUnitOfWorkFactory(["shared", "personal"])
        self.concepts = ConceptService(settings, self.factory)
        self.ingest = SourceIngestService(self.concepts, known_prefixes=("confluence", "jira"))
        self.confluence_cfg = quellen.quelle(
            "confluence-eng",
            adapter="confluence",
            id_prefix="confluence",
            base_url=quellen.CONFLUENCE_BASE,
            selection={"spaces": ["ENG", "ARCH"]},
        )
        self.jira_cfg = quellen.quelle(
            "jira-team",
            adapter="jira",
            id_prefix="jira",
            base_url=quellen.JIRA_BASE,
            default_type="Jira Issue",
        )
        self.confluence = self._bauen(ConfluenceAdapter, self.confluence_cfg)
        self.jira = self._bauen(JiraAdapter, self.jira_cfg)

    def _bauen(self, klasse: type, cfg: SourceConfig) -> Any:
        adapter = klasse(client_factory=quellen.client_factory(self.app), sleep=nicht_warten)
        adapter.configure(cfg)
        return adapter

    @property
    def steuerung(self) -> TestClient:
        return quellen.control(self.app)

    @property
    def shared(self) -> Any:
        return self.factory.state("shared")


@pytest.fixture
def umgebung(settings: Settings) -> Umgebung:
    return Umgebung(settings)


class TestAbnahmeKorpusVollstaendig:
    def test_alle_seiten_werden_konzepte(self, umgebung: Umgebung) -> None:
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert bericht.documents == SEITEN
        assert bericht.created == SEITEN
        assert len(umgebung.shared.concepts) == SEITEN

    def test_alle_vorgaenge_werden_konzepte(self, umgebung: Umgebung) -> None:
        bericht = umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)

        assert bericht.documents == VORGAENGE
        assert bericht.created == VORGAENGE

    def test_beide_quellen_zusammen(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)

        assert len(umgebung.shared.concepts) == SEITEN + VORGAENGE

    def test_die_felder_kommen_richtig_an(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        concept = umgebung.shared.concepts["confluence:100001"]

        assert concept.title == "Nächtlicher ETL-Lauf"
        assert concept.description.startswith("Der Lauf verarbeitet")
        # Absolut und nicht relativ: Die Quelle liefert '/spaces/ENG/pages/100001', und ein
        # solcher Pfad zeigt aus der UI heraus auf die UI selbst statt auf Confluence. Der
        # Adapter setzt deshalb die Weboberflächen-Adresse davor.
        assert concept.resource == "http://mock-sources/confluence/spaces/ENG/pages/100001"
        assert concept.tags == ("daten", "datenpipeline")
        assert concept.source_name == "confluence-eng"
        assert concept.external_id == "100001"
        assert concept.source_updated_at is not None

    def test_der_typ_kommt_aus_der_zielkonfiguration(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)

        assert umgebung.shared.concepts["confluence:100001"].type == "Confluence Page"
        assert umgebung.shared.concepts["jira:TEAM-1"].type == "Jira Issue"

    def test_alles_landet_im_store_des_scopes(self, umgebung: Umgebung) -> None:
        """§20.1: Der Store kommt aus dem Scope, nicht aus der Quelle."""
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert umgebung.factory.state("personal").concepts == {}
        assert all(item.store == "shared" for item in umgebung.shared.concepts.values())


class TestReferenzen:
    def test_quellverweise_werden_kanten(self, umgebung: Umgebung) -> None:
        """§8.5: Referenzen aus der Quelle, übersetzt über das Präfix."""
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert bericht.edges_added == CONFLUENCE_KANTEN
        assert len(umgebung.shared.edges) == CONFLUENCE_KANTEN

    def test_quellverweise_tragen_die_kennung_aus_85(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        erzeuger = {edge.generated_by for edge in umgebung.shared.edges}

        assert erzeuger == {defaults.GENERATED_BY_SOURCE_REFERENCE}

    def test_textverweise_tragen_die_andere_kennung(self, umgebung: Umgebung) -> None:
        """Jeder Vorgang nennt eine Confluence-Seite im Text (§9.2)."""
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)

        aus_dem_text = [
            edge
            for edge in umgebung.shared.edges
            if edge.generated_by == defaults.GENERATED_BY_BODY_REFERENCE
        ]

        assert len(aus_dem_text) == VORGAENGE
        assert all(edge.to_id.startswith("confluence:") for edge in aus_dem_text)

    def test_ein_noch_unbekanntes_ziel_bleibt_unaufgeloest(self, umgebung: Umgebung) -> None:
        """§8.5: "Kaputte Referenzen sind kein Fehler"."""
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)

        auf_confluence = [
            edge for edge in umgebung.shared.edges if edge.to_id.startswith("confluence:")
        ]

        assert auf_confluence
        assert not any(edge.resolved for edge in auf_confluence)

    def test_ein_spaeterer_lauf_loest_sie_auf(self, umgebung: Umgebung) -> None:
        """§8.5: "…und bei jedem Lauf erneut geprüft"."""
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        auf_confluence = [
            edge for edge in umgebung.shared.edges if edge.to_id.startswith("confluence:")
        ]

        assert bericht.edges_resolved >= VORGAENGE
        assert all(edge.resolved for edge in auf_confluence)

    def test_verweise_innerhalb_der_quelle_sind_sofort_aufgeloest(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert all(edge.resolved for edge in umgebung.shared.edges)


class TestAbnahmeZweiterLauf:
    def test_ohne_quelaenderung_passiert_nichts(self, umgebung: Umgebung) -> None:
        """§22.2 Punkt 1: "Ein zweiter Sync ohne Quelländerung erzeugt null change_log-Einträge"."""
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)
        vorher = len(umgebung.shared.changes)

        bericht = umgebung.ingest.ingest(
            umgebung.confluence, umgebung.confluence_cfg, cursor=umgebung.confluence.next_cursor()
        )

        assert bericht.documents == 0
        assert len(umgebung.shared.changes) == vorher

    def test_ein_vollabgleich_ohne_aenderung_schreibt_ebenfalls_nichts(
        self, umgebung: Umgebung
    ) -> None:
        """Der Hash entscheidet, nicht der Cursor (§10.2 Regel 3)."""
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)
        vorher = len(umgebung.shared.changes)

        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert bericht.documents == SEITEN
        assert bericht.unchanged == SEITEN
        assert bericht.created == 0
        assert bericht.edges_added == 0
        assert bericht.edges_removed == 0
        assert len(umgebung.shared.changes) == vorher

    def test_das_aenderungsszenario_wirkt_genau_auf_ein_konzept(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)
        cursor = umgebung.confluence.next_cursor()
        umgebung.steuerung.post("/_control/scenario/incremental_update")

        bericht = umgebung.ingest.ingest(
            umgebung.confluence, umgebung.confluence_cfg, cursor=cursor
        )

        assert bericht.documents == 1
        assert bericht.updated == 1
        assert bericht.created == 0
        assert umgebung.shared.concepts["confluence:100001"].title == (
            "Nächtlicher ETL-Lauf (überarbeitet)"
        )

    def test_das_szenario_wirkt_auch_auf_die_zweite_quelle(self, umgebung: Umgebung) -> None:
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)
        cursor = umgebung.jira.next_cursor()
        umgebung.steuerung.post("/_control/scenario/incremental_update")

        bericht = umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg, cursor=cursor)

        assert bericht.documents == 1
        assert bericht.updated == 1

    def test_der_geaenderte_textverweis_verschiebt_die_kante(self, umgebung: Umgebung) -> None:
        """Der Vorgang verlinkt danach eine andere Seite — die alte Kante muss verschwinden."""
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)
        cursor = umgebung.jira.next_cursor()
        umgebung.steuerung.post("/_control/scenario/incremental_update")

        bericht = umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg, cursor=cursor)
        kanten = [edge for edge in umgebung.shared.edges if edge.from_id == "jira:TEAM-1"]
        aus_dem_text = {
            edge.to_id
            for edge in kanten
            if edge.generated_by == defaults.GENERATED_BY_BODY_REFERENCE
        }

        assert bericht.edges_added == 1
        assert bericht.edges_removed == 1
        assert aus_dem_text == {"confluence:100010"}

    def test_die_strukturierten_beziehungen_ueberdauern_die_textaenderung(
        self, umgebung: Umgebung
    ) -> None:
        """Der Gegenpol zum Test darüber.

        Ein geänderter Beschreibungstext sagt nichts darüber, ob ein Vorgang noch zu seinem Epic
        gehört oder noch blockiert ist — das steht in den Feldern und nicht im Text. Würde die
        Kantenerneuerung beides in einen Topf werfen, verschwänden bei jeder Textänderung die
        Beziehungen, die die Quelle als Tatsache meldet (§7.7, Leitprinzip 6).
        """
        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg)
        cursor = umgebung.jira.next_cursor()
        umgebung.steuerung.post("/_control/scenario/incremental_update")

        umgebung.ingest.ingest(umgebung.jira, umgebung.jira_cfg, cursor=cursor)
        arten = {
            (edge.to_id, edge.kind)
            for edge in umgebung.shared.edges
            if edge.from_id == "jira:TEAM-1"
        }

        assert ("jira:TEAM-2", defaults.EDGE_KIND_MEMBER) in arten
        assert ("jira:TEAM-3", defaults.EDGE_KIND_DEPENDS_ON) in arten
        assert ("jira:TEAM-5", defaults.EDGE_KIND_RELATED) in arten


class TestBericht:
    def test_der_bericht_enthaelt_keine_inhalte(self, umgebung: Umgebung) -> None:
        """§21.1: Ein Logeintrag trägt IDs und Zahlen, keinen Text aus der Quelle."""
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        werte = " ".join(str(item) for item in bericht.as_dict().values())

        assert "Nächtlicher" not in werte
        assert bericht.as_dict()["source"] == "confluence-eng"

    def test_changed_zaehlt_die_schreibvorgaenge(self, umgebung: Umgebung) -> None:
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert bericht.changed == SEITEN

    def test_der_cursor_kommt_mit_dem_bericht_zurueck(self, umgebung: Umgebung) -> None:
        """Wo er zwischen zwei Läufen liegt, entscheidet Stufe 4 — hier ist er ein Wert."""
        bericht = umgebung.ingest.ingest(umgebung.confluence, umgebung.confluence_cfg)

        assert not bericht.cursor.is_empty
        assert bericht.cursor == umgebung.confluence.next_cursor()


class TestVorauslesen:
    """``connection.read_ahead``: Die Quelle darf holen, während der Kern schreibt (§8.2).

    Geprüft wird nicht die Uhr, sondern die Gleichheit: Ein Lauf mit Vorlauf muss dasselbe
    Ergebnis liefern wie einer ohne. Alles andere wäre kein schnellerer Lauf, sondern ein
    anderer — und ein Zeitgewinn, den man mit einem abweichenden Bestand bezahlt, ist keiner.
    """

    def test_liefert_mit_vorlauf_dasselbe_wie_ohne(self, settings: Settings) -> None:
        ohne = Umgebung(settings)
        bericht_ohne = ohne.ingest.ingest(ohne.confluence, ohne.confluence_cfg)

        mit = Umgebung(settings)
        cfg = mit.confluence_cfg.model_copy(
            update={"connection": mit.confluence_cfg.connection.model_copy(
                update={"read_ahead": 40}
            )}
        )
        bericht_mit = mit.ingest.ingest(mit.confluence, cfg)

        assert bericht_mit.as_dict() == bericht_ohne.as_dict()
        assert bericht_mit.documents == SEITEN
        # Und der Bestand selbst, nicht nur die Zahlen darüber.
        assert sorted(c.id for c in mit.shared.concepts.values()) == sorted(
            c.id for c in ohne.shared.concepts.values()
        )

    def test_ein_ausfall_der_quelle_kommt_beim_aufrufer_an(self, settings: Settings) -> None:
        """§22.3: Der Cursor darf bei einem Abbruch nicht fortschreiten — dafür muss die
        Ausnahme durch den Vorlauf hindurch dort ankommen, wo sie ohne ihn entstanden wäre."""
        umgebung = Umgebung(settings)
        cfg = umgebung.confluence_cfg.model_copy(
            update={"connection": umgebung.confluence_cfg.connection.model_copy(
                update={"read_ahead": 40, "base_url": "http://127.0.0.1:9/nicht-da"}
            )}
        )
        umgebung.confluence.configure(cfg)

        with pytest.raises(SourceError):
            umgebung.ingest.ingest(umgebung.confluence, cfg)
