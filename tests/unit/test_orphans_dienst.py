"""Verwaiste-Knoten-Vernetzung (§15) und die Musterdateien aus §15.2a.

Die Abnahme der Stufe 10 aus §24, Kriterium für Kriterium:

* "Der isoliert angelegte Knoten wird gefunden"
* "mit ``--use-llm false`` entstehen nur Stufe-1-Kanten"
* "mit ``true`` mindestens eine Modellkante über der Schwelle"
* "ein Knoten ohne passendes Cluster erzeugt nachvollziehbar ein neues Cluster"
* "die Zahl loser Knoten sinkt über aufeinanderfolgende Läufe"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from support.semantik import ISOLIERT, Umgebung, baue, befuellen, konzept, korpus
from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.patterns import load_patterns
from wissensgraph.config.schema import Settings
from wissensgraph.ports.models import PromptSpec
from wissensgraph.services.orphans import OrphanRequest

pytestmark = pytest.mark.unit


@pytest.fixture
def musterdatei(tmp_path: Path) -> Path:
    """Eine Musterdatei mit dem Vorgangsschlüssel-Muster aus §15.2a."""
    pfad = tmp_path / "bezeichner.yaml"
    pfad.write_text(
        yaml.safe_dump(
            {
                "patterns": [
                    {
                        "name": "jira-key",
                        "regex": r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b",
                        "description": "Vorgangsschlüssel wie PROJ-4711.",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return pfad


def skript(
    *,
    relationship: str | None = None,
    neues_cluster: dict[str, str] | None = None,
    vorschlaege: list[str] | None = None,
    confidence: float = 0.9,
) -> Any:
    """Ein Antwortskript für alle drei Aufgaben, die in §15 vorkommen."""

    def antwort(prompt: PromptSpec) -> str:
        system = prompt.system or ""
        if "Themengruppe zu" in system:
            return json.dumps(
                {
                    "suggested_cluster_ids": vorschlaege or [],
                    "propose_new_cluster": neues_cluster,
                    "confidence": confidence,
                    "reasoning": "Aus dem Testskript.",
                }
            )
        if "Beziehung" in system:
            return json.dumps(
                {
                    "relationship": relationship,
                    "direction": "a_to_b",
                    "confidence": confidence,
                    "reasoning": "Aus dem Testskript.",
                }
            )
        if "Themengruppen" in system:
            return json.dumps({"title": "Testgruppe", "description": "Vom Fake benannt."})
        return "Eine erzeugte Beschreibung."

    return antwort


def vernetzbar(settings: Settings, **kwargs: Any) -> Umgebung:
    """Ein geclusterter Korpus, in dem genau ein Knoten lose ist."""
    umgebung = baue(settings, **kwargs)
    befuellen(umgebung, korpus())
    # Ein Vorgangsschlüssel, den es wirklich auch anderswo gibt — die Grundlage von §15.2a.
    befuellen(
        umgebung,
        [
            konzept(
                "confluence:500",
                title="Wartungsvertrag Küchengeräte",
                description="Wartung und Entkalkung, abgestimmt im Vorgang PROJ-4711.",
            )
        ],
    )
    umgebung.embeddings.run(scope="engineering")
    umgebung.clusters.run(scope="engineering")
    umgebung.clusters.run(scope="engineering")
    return umgebung


class TestMusterdateien:
    def test_ein_muster_wird_geladen_und_uebersetzt(
        self, settings: Settings, musterdatei: Path
    ) -> None:
        muster = load_patterns(settings, paths=(musterdatei,))

        assert len(muster) == 1
        assert muster[0].compiled().search("Siehe PROJ-4711 dazu")

    def test_ein_ungueltiger_regulaerer_ausdruck_ist_ein_startfehler(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§6.5: Er soll auffallen, bevor er mitten in einer nächtlichen Vernetzung auffällt."""
        pfad = tmp_path / "kaputt.yaml"
        pfad.write_text(
            yaml.safe_dump({"patterns": [{"name": "x", "regex": "([unvollständig"}]}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigValidationError, match="regul"):
            load_patterns(settings, paths=(pfad,))

    def test_doppelte_musternamen_werden_abgewiesen(
        self, settings: Settings, musterdatei: Path, tmp_path: Path
    ) -> None:
        zweite = tmp_path / "zweite.yaml"
        zweite.write_text(
            yaml.safe_dump({"patterns": [{"name": "jira-key", "regex": "X-1"}]}), encoding="utf-8"
        )

        with pytest.raises(ConfigValidationError, match="jira-key"):
            load_patterns(settings, paths=(musterdatei, zweite))

    def test_eine_fehlende_datei_ist_kein_fehler(self, settings: Settings, tmp_path: Path) -> None:
        assert load_patterns(settings, paths=(tmp_path / "gibtsnicht.yaml",)) == ()

    def test_grossschreibung_zaehlt_bei_bezeichnern(
        self, settings: Settings, musterdatei: Path
    ) -> None:
        """'proj-4711' im Text ist meist ein Tippfehler; eine Kante auf Verdacht wäre falsch."""
        muster = load_patterns(settings, paths=(musterdatei,))[0]

        assert muster.compiled().search("proj-4711") is None

    def test_die_ausgelieferten_muster_laden(self, settings: Settings) -> None:
        pfad = Path(__file__).resolve().parents[2] / "config" / "patterns"

        muster = load_patterns(settings, paths=tuple(sorted(pfad.glob("*.yaml"))))

        assert {item.name for item in muster} >= {"jira-key"}


class TestStufeEins:
    def test_der_isolierte_knoten_wird_gefunden(self, semantik_settings: Settings) -> None:
        """§24, Stufe 10, erstes Kriterium."""
        umgebung = vernetzbar(semantik_settings, chat=skript())

        bericht = umgebung.orphans.run(OrphanRequest(scope="engineering", use_llm=False))

        assert bericht.loose_before >= 1

    def test_ein_woertlicher_treffer_wird_zur_kante_ohne_modellaufruf(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        """§15.2a: "Die Übereinstimmung ist der Beleg; ein Modell wäre hier reine Verschwendung"."""
        umgebung = vernetzbar(semantik_settings, chat=skript())
        vorher = len(umgebung.clients.chat_client.calls)

        bericht = umgebung.orphans.run(
            OrphanRequest(scope="engineering", use_llm=False, pattern_files=(str(musterdatei),))
        )

        kanten = [
            edge
            for edge in umgebung.kanten()
            if edge.generated_by == defaults.GENERATED_BY_TEXT_MATCH
        ]
        assert bericht.text_matches >= 1
        assert kanten and kanten[0].confidence == 1.0
        assert len(umgebung.clients.chat_client.calls) == vorher

    def test_mit_use_llm_false_entstehen_nur_stufe_eins_kanten(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        """§24, Stufe 10, zweites Kriterium."""
        umgebung = vernetzbar(semantik_settings, chat=skript(relationship="depends_on"))
        vorher = len(umgebung.clients.chat_client.calls)

        bericht = umgebung.orphans.run(
            OrphanRequest(scope="engineering", use_llm=False, pattern_files=(str(musterdatei),))
        )

        assert bericht.model_edges == 0
        assert bericht.calls == 0
        assert len(umgebung.clients.chat_client.calls) == vorher
        erzeuger = {edge.generated_by for edge in umgebung.kanten()}
        assert defaults.GENERATED_BY_TEXT_MATCH in erzeuger

    def test_hohe_naehe_wird_direkt_geschrieben(self, semantik_settings: Settings) -> None:
        """§15.2b: oberhalb von ``proximity_auto_commit`` entsteht die Kante ohne Rückfrage."""
        umgebung = vernetzbar(semantik_settings, chat=skript())

        bericht = umgebung.orphans.run(
            OrphanRequest(scope="engineering", use_llm=False, proximity_auto_commit=0.1)
        )

        kanten = [
            edge
            for edge in umgebung.kanten()
            if edge.generated_by == defaults.GENERATED_BY_PROXIMITY
        ]
        assert bericht.proximity_committed >= 1
        assert kanten and kanten[0].kind == defaults.EDGE_KIND_RELATED
        assert all(edge.weight is not None for edge in kanten)

    def test_geringe_naehe_wird_verworfen(self, semantik_settings: Settings) -> None:
        """§15.2b: unterhalb von ``proximity_candidate_band`` — verwerfen."""
        umgebung = vernetzbar(semantik_settings, chat=skript())

        bericht = umgebung.orphans.run(
            OrphanRequest(
                scope="engineering",
                use_llm=False,
                proximity_auto_commit=0.99,
                proximity_candidate_band=0.98,
            )
        )

        assert bericht.proximity_committed == 0
        assert bericht.proximity_candidates == 0


class TestStufeZwei:
    def test_eine_modellkante_ueber_der_schwelle_entsteht(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 10, drittes Kriterium."""
        umgebung = vernetzbar(
            semantik_settings, chat=skript(relationship="references", vorschlaege=[])
        )

        bericht = umgebung.orphans.run(
            OrphanRequest(
                scope="engineering",
                use_llm=True,
                proximity_auto_commit=0.99,
                proximity_candidate_band=0.01,
            )
        )

        assert bericht.model_edges >= 1

    def test_ein_knoten_ohne_passendes_cluster_erzeugt_ein_neues(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 10, viertes Kriterium (§15.3)."""
        umgebung = vernetzbar(
            semantik_settings,
            chat=skript(
                neues_cluster={"title": "Haustechnik", "description": "Geräte und Wartung."}
            ),
        )
        vorher = set(umgebung.cluster_ids())

        bericht = umgebung.orphans.run(OrphanRequest(scope="engineering", use_llm=True))

        neu = set(umgebung.cluster_ids()) - vorher
        assert bericht.clusters_created >= 1
        assert neu
        cluster = umgebung.state().concepts[next(iter(neu))]
        assert cluster.title == "Haustechnik"
        assert cluster.generated_by is not None
        assert cluster.verified_by is None
        assert umgebung.mitglieder(cluster.id)

    def test_ein_leeres_ergebnis_ohne_vorschlag_ist_gueltig(
        self, semantik_settings: Settings
    ) -> None:
        """§15.3 ausdrücklich: "ein leeres Ergebnis ohne jeden Vorschlag ist gültig"."""
        umgebung = vernetzbar(semantik_settings, chat=skript())
        vorher = set(umgebung.cluster_ids())

        bericht = umgebung.orphans.run(OrphanRequest(scope="engineering", use_llm=True))

        assert bericht.clusters_created == 0
        assert set(umgebung.cluster_ids()) == vorher

    def test_ein_vorschlag_unter_der_confidence_wird_verworfen(
        self, semantik_settings: Settings
    ) -> None:
        umgebung = vernetzbar(
            semantik_settings,
            chat=skript(neues_cluster={"title": "Haustechnik", "description": "x"}, confidence=0.1),
        )

        bericht = umgebung.orphans.run(
            OrphanRequest(scope="engineering", use_llm=True, min_confidence=0.6)
        )

        assert bericht.clusters_created == 0

    def test_das_modell_sieht_nur_den_knoten_und_die_uebersicht(
        self, semantik_settings: Settings
    ) -> None:
        """§15.3: "An keiner Stelle sieht das Modell mehr als einen Knoten plus eine Liste"."""
        umgebung = vernetzbar(semantik_settings, chat=skript())
        umgebung.orphans.run(OrphanRequest(scope="engineering", use_llm=True))

        zuordnung = [
            call
            for call in umgebung.clients.chat_client.calls
            if "Themengruppe zu" in (call.system or "")
        ]

        assert zuordnung
        eingabe = json.loads(zuordnung[0].user)
        assert set(eingabe) == {"concept", "clusters"}
        assert all(
            set(item) == {"id", "title", "description", "members"} for item in eingabe["clusters"]
        )

    def test_die_mitgliederliste_je_cluster_ist_gedeckelt(
        self, semantik_settings: Settings
    ) -> None:
        """``cluster_preview_members`` begrenzt, was in den Prompt geht (§15.4)."""
        umgebung = vernetzbar(semantik_settings, chat=skript())

        umgebung.orphans.run(
            OrphanRequest(scope="engineering", use_llm=True, cluster_preview_members=2)
        )

        zuordnung = next(
            call
            for call in umgebung.clients.chat_client.calls
            if "Themengruppe zu" in (call.system or "")
        )
        eingabe = json.loads(zuordnung.user)
        assert all(len(item["members"]) <= 2 for item in eingabe["clusters"])


class TestParameter:
    def test_jeder_wert_kommt_ohne_angabe_aus_der_konfiguration(
        self, semantik_settings: Settings
    ) -> None:
        """§15.4: "Jeder Wert hat einen Default in der Config und ist überschreibbar" (§6.2)."""
        aufgefuellt = OrphanRequest(scope="engineering").gegen(semantik_settings)

        assert aufgefuellt.loose_threshold == semantik_settings.orphans.loose_threshold
        assert aufgefuellt.proximity_top_n == semantik_settings.orphans.proximity_top_n
        assert aufgefuellt.use_llm == semantik_settings.orphans.use_llm

    def test_ein_uebergebener_wert_schlaegt_die_konfiguration(
        self, semantik_settings: Settings
    ) -> None:
        aufgefuellt = OrphanRequest(scope="engineering", proximity_top_n=3).gegen(semantik_settings)

        assert aufgefuellt.proximity_top_n == 3

    def test_use_llm_false_ist_nicht_dasselbe_wie_nicht_gesetzt(
        self, semantik_settings: Settings
    ) -> None:
        """``False`` ist ein Wert und darf nicht als "nichts angegeben" durchgehen."""
        assert (
            OrphanRequest(scope="engineering", use_llm=False).gegen(semantik_settings).use_llm
            is False
        )


class TestTrockenlauf:
    def test_ein_trockenlauf_berichtet_und_schreibt_nichts(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        """§15.4: ``--dry-run`` — nur berichten, nichts schreiben."""
        umgebung = vernetzbar(semantik_settings, chat=skript(relationship="references"))
        vorher = len(umgebung.kanten())

        bericht = umgebung.orphans.run(
            OrphanRequest(
                scope="engineering",
                use_llm=False,
                proximity_auto_commit=0.1,
                pattern_files=(str(musterdatei),),
                dry_run=True,
            )
        )

        assert bericht.text_matches + bericht.proximity_committed > 0
        assert len(umgebung.kanten()) == vorher


class TestFolgelaeufe:
    def test_die_zahl_loser_knoten_sinkt(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        """§24, Stufe 10, fünftes Kriterium — und das Abbruchkriterium des Verfahrens."""
        umgebung = vernetzbar(semantik_settings, chat=skript(relationship="references"))

        bericht = umgebung.orphans.run(
            OrphanRequest(
                scope="engineering",
                use_llm=False,
                proximity_auto_commit=0.1,
                pattern_files=(str(musterdatei),),
            )
        )

        assert bericht.loose_after < bericht.loose_before

    def test_ein_zweiter_lauf_findet_weniger_zu_tun(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        umgebung = vernetzbar(semantik_settings, chat=skript())
        anfrage = OrphanRequest(
            scope="engineering",
            use_llm=False,
            proximity_auto_commit=0.1,
            pattern_files=(str(musterdatei),),
        )
        erst = umgebung.orphans.run(anfrage)

        zweit = umgebung.orphans.run(anfrage)

        assert zweit.loose_before < erst.loose_before

    def test_ohne_lose_knoten_passiert_nichts(self, semantik_settings: Settings) -> None:
        umgebung = baue(semantik_settings, chat=skript())

        bericht = umgebung.orphans.run(OrphanRequest(scope="engineering"))

        assert bericht.loose_before == 0
        assert umgebung.clients.chat_client.calls == []


class TestBericht:
    def test_der_bericht_enthaelt_keine_inhalte(
        self, semantik_settings: Settings, musterdatei: Path
    ) -> None:
        umgebung = vernetzbar(semantik_settings, chat=skript())

        serialisiert = json.dumps(
            umgebung.orphans.run(
                OrphanRequest(scope="engineering", use_llm=False, pattern_files=(str(musterdatei),))
            ).as_dict(),
            ensure_ascii=False,
        )

        assert ISOLIERT[2] not in serialisiert
