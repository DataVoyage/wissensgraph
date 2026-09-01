"""Semantische Kantenerkennung (§14).

Die Abnahme der Stufe 9 aus §24, Kriterium für Kriterium:

* "Im Testcluster entsteht mindestens eine typisierte Kante mit nachvollziehbarer Provenienz"
* "die Mehrheit der Paare liefert 'keine Beziehung'"
* "ein Wiederholungslauf erzeugt fast ausschließlich Cache-Treffer"
* "kein Konzept wird automatisch deprecated"
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from support.semantik import THEMEN, Umgebung, baue, befuellen, korpus, models_config
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeType
from wissensgraph.domain.concepts import ConceptStatus
from wissensgraph.infrastructure.models.cache import MemoryResponseCache
from wissensgraph.ports.models import PromptSpec
from wissensgraph.services.router import ModelRouterService

pytestmark = pytest.mark.unit


def antwortet(relationship: str | None, *, confidence: float = 0.9, direction: str = "a_to_b"):
    """Ein Skript, das für Beziehungsfragen immer dieselbe Antwort gibt."""

    def skript(prompt: PromptSpec) -> str:
        system = prompt.system or ""
        if "Beziehung" in system:
            return json.dumps(
                {
                    "relationship": relationship,
                    "direction": direction,
                    "confidence": confidence,
                    "reasoning": "Aus dem Testskript.",
                }
            )
        if "Themengruppen" in system:
            return json.dumps({"title": "Testgruppe", "description": "Vom Fake benannt."})
        return "Eine erzeugte Beschreibung."

    return skript


def geclustert(settings: Settings, **kwargs: Any) -> Umgebung:
    """Ein Korpus mit stabilen Clustern — der Ausgangspunkt jeder Kantenerkennung."""
    umgebung = baue(settings, **kwargs)
    befuellen(umgebung, korpus())
    umgebung.embeddings.run(scope="engineering")
    umgebung.clusters.run(scope="engineering")
    umgebung.clusters.run(scope="engineering")
    return umgebung


class TestKantenErkennung:
    def test_eine_typisierte_kante_mit_nachvollziehbarer_provenienz(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 9, erstes Kriterium."""
        umgebung = geclustert(semantik_settings, chat=antwortet("depends_on"))

        bericht = umgebung.relations.run(scope="engineering")

        kanten = umgebung.kanten(kind="depends_on")
        assert bericht.edges_written >= 1
        assert kanten
        assert kanten[0].confidence == pytest.approx(0.9)
        assert kanten[0].reasoning == "Aus dem Testskript."
        assert "/relation_extraction@v" in (kanten[0].generated_by or "")
        assert kanten[0].verified_by is None

    def test_keine_beziehung_ist_die_erwartete_mehrheitsantwort(
        self, semantik_settings: Settings
    ) -> None:
        """§14.2 Schritt 4 — und der Prompt sagt es dem Modell ausdrücklich."""
        umgebung = geclustert(semantik_settings)

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.calls > 0
        assert bericht.no_relation == bericht.calls
        assert bericht.edges_written == 0

    def test_der_prompt_nennt_die_erlaubten_beziehungsarten(
        self, semantik_settings: Settings
    ) -> None:
        """§14.3: Das Modell soll nichts vorschlagen, was der Graph nicht führt."""
        umgebung = geclustert(semantik_settings)
        umgebung.relations.run(scope="engineering")

        erster = umgebung.clients.chat_client.calls[-1]
        eingabe = json.loads(erster.user)

        assert eingabe["allowed_relationships"] == list(semantik_settings.edge_kinds.semantic)
        assert set(eingabe) == {"concept_a", "concept_b", "allowed_relationships"}

    def test_das_modell_sieht_nie_mehr_als_zwei_konzepte(self, semantik_settings: Settings) -> None:
        """§14.1: aufgeteilt "so, dass das Modell nie den Gesamtgraphen sieht"."""
        umgebung = geclustert(semantik_settings)
        umgebung.relations.run(scope="engineering")

        for aufruf in umgebung.clients.chat_client.calls:
            if "Beziehung" in (aufruf.system or ""):
                eingabe = json.loads(aufruf.user)
                assert set(eingabe) == {"concept_a", "concept_b", "allowed_relationships"}

    def test_eine_unbekannte_beziehungsart_wird_verworfen(
        self, semantik_settings: Settings
    ) -> None:
        """Eine neue Taxonomie zur Laufzeit nimmt §24 für diese Stufe ausdrücklich aus."""
        umgebung = geclustert(semantik_settings, chat=antwortet("erfunden_von_modell"))

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.edges_written == 0
        assert bericht.no_relation == bericht.calls

    def test_unter_der_confidence_schwelle_entsteht_keine_kante(
        self, semantik_settings: Settings
    ) -> None:
        """§14.2 Schritt 5: geschrieben wird ab ``relations.min_confidence``."""
        umgebung = geclustert(semantik_settings, chat=antwortet("depends_on", confidence=0.2))

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.below_confidence > 0
        assert bericht.edges_written == 0

    def test_die_richtung_kehrt_die_kante_um(self, semantik_settings: Settings) -> None:
        """§14.3: ``direction`` entscheidet, wohin die Kante zeigt."""
        hin = geclustert(semantik_settings, chat=antwortet("depends_on", direction="a_to_b"))
        her = geclustert(semantik_settings, chat=antwortet("depends_on", direction="b_to_a"))

        hin.relations.run(scope="engineering")
        her.relations.run(scope="engineering")

        vorwaerts = {(e.from_id, e.to_id) for e in hin.kanten(kind="depends_on")}
        rueckwaerts = {(e.to_id, e.from_id) for e in her.kanten(kind="depends_on")}
        assert vorwaerts == rueckwaerts


class TestVorfilter:
    def test_paare_unter_der_mindestaehnlichkeit_kosten_keinen_aufruf(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """§14.5: Der Vorfilter wirkt *vor* dem Aufruf — das ist der Kostenhebel."""
        streng = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {"neighbors_k": 4},
                "relations": {"min_pair_similarity": 0.99},
            }
        )
        umgebung = geclustert(streng, chat=antwortet("depends_on"))

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.pairs_filtered > 0
        assert bericht.calls == 0

    def test_ein_bereits_verbundenes_paar_wird_nicht_erneut_gefragt(
        self, semantik_settings: Settings
    ) -> None:
        """§14.5: "Verarbeitung nur neuer/geänderter Paare"."""
        umgebung = geclustert(semantik_settings, chat=antwortet("depends_on"))
        erst = umgebung.relations.run(scope="engineering")

        zweit = umgebung.relations.run(scope="engineering")

        assert erst.edges_written > 0
        assert zweit.pairs_known >= erst.edges_written
        assert zweit.calls < erst.calls

    def test_paare_ohne_embedding_werden_uebersprungen(self, semantik_settings: Settings) -> None:
        """Ohne Vektoren gibt es keine Ähnlichkeit — und ohne sie keinen Vorfilter."""
        umgebung = baue(semantik_settings, chat=antwortet("depends_on"))
        befuellen(umgebung, korpus())

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.calls == 0


class TestCache:
    def test_ein_wiederholungslauf_besteht_fast_nur_aus_cache_treffern(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 9: "ein Wiederholungslauf erzeugt fast ausschließlich Cache-Treffer"."""
        umgebung = geclustert(semantik_settings)
        cache = MemoryResponseCache()
        umgebung.relations._router = ModelRouterService(
            semantik_settings,
            models_config(),
            umgebung.clients,
            unit_of_work=umgebung.uow,
            cache=cache,
            sleep=lambda _: None,
        )

        erst = umgebung.relations.run(scope="engineering")
        wieder = umgebung.relations.run(scope="engineering")

        assert erst.cached == 0
        assert wieder.calls == erst.calls
        assert wieder.cached == wieder.calls


class TestSupersedes:
    def test_supersedes_erzeugt_eine_aufgabe_und_keine_statusaenderung(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 9: "kein Konzept wird automatisch deprecated" (§14.4)."""
        umgebung = geclustert(semantik_settings, chat=antwortet(defaults.EDGE_KIND_SUPERSEDES))

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.supersedes_tasks >= 1
        assert all(
            concept.status is not ConceptStatus.DEPRECATED
            for concept in umgebung.state().concepts.values()
        )
        vorschlaege = [
            entry
            for entry in umgebung.state().changes
            if entry.change_type is ChangeType.STATUS_CHANGED
            and (entry.detail or {}).get("vorschlag") == "deprecate"
        ]
        assert vorschlaege

    def test_die_kante_selbst_entsteht_trotzdem(self, semantik_settings: Settings) -> None:
        """§14.2 Schritt 5 schreibt die Kante; §14.4 verhindert nur ihre Folge."""
        umgebung = geclustert(semantik_settings, chat=antwortet(defaults.EDGE_KIND_SUPERSEDES))

        umgebung.relations.run(scope="engineering")

        assert umgebung.kanten(kind=defaults.EDGE_KIND_SUPERSEDES)


class TestClusteruebergreifend:
    def test_die_zentralsten_mitglieder_verwandter_cluster_werden_geprueft(
        self, semantik_settings: Settings
    ) -> None:
        """§14.2 Schritt 6 — sonst blieben Beziehungen zwischen Themen unentdeckt."""
        umgebung = geclustert(semantik_settings, chat=antwortet(None))

        bericht = umgebung.relations.run(scope="engineering")

        # Innerhalb der drei Cluster gäbe es 4+5+4 Mitglieder, also 6+10+6 = 22 Paare. Jedes
        # zusätzliche Paar stammt aus dem Schritt über die Clustergrenze.
        innerhalb = 6 + 10 + 6
        assert bericht.pairs_considered > innerhalb

    def test_ohne_verwandte_cluster_bleibt_es_bei_den_eigenen_paaren(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        ohne = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {"neighbors_k": 4, "related_cluster_top_n": 0},
                "relations": {"cross_cluster_members": 0},
            }
        )
        umgebung = geclustert(ohne, chat=antwortet(None))

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.pairs_considered == 6 + 10 + 6


class TestTrockenlauf:
    def test_ein_trockenlauf_schreibt_nichts(self, semantik_settings: Settings) -> None:
        umgebung = geclustert(semantik_settings, chat=antwortet("depends_on"))

        bericht = umgebung.relations.run(scope="engineering", dry_run=True)

        assert bericht.edges_written > 0
        assert umgebung.kanten(kind="depends_on") == []


class TestNebenlaeufigkeit:
    """Die Modellfragen laufen gleichzeitig, das Verbuchen der Reihe nach (§14.2).

    Der Anlass ist gemessen: 3.688 Paare bei 824 ms Antwortzeit ergaben sequenziell knapp
    fünfzig Minuten, in denen der Prozess zu achtundneunzig Prozent auf das Netz wartete.
    """

    def test_die_fragen_laufen_wirklich_gleichzeitig(self, semantik_settings: Settings) -> None:
        """Ohne diesen Nachweis wäre die Nebenläufigkeit nur behauptet.

        Jede Antwort wartet kurz. Sequenziell summieren sich die Wartezeiten, gleichzeitig
        überlappen sie — gezählt wird deshalb, wie viele Aufrufe sich zeitlich überschneiden.
        """
        import threading
        import time

        gleichzeitig = 0
        hoechststand = 0
        sperre = threading.Lock()

        def langsam(prompt: PromptSpec) -> str:
            nonlocal gleichzeitig, hoechststand
            system = prompt.system or ""
            if "Beziehung" not in system:
                return antwortet(None)(prompt)
            with sperre:
                gleichzeitig += 1
                hoechststand = max(hoechststand, gleichzeitig)
            time.sleep(0.05)
            with sperre:
                gleichzeitig -= 1
            return json.dumps(
                {
                    "relationship": None,
                    "direction": "a_to_b",
                    "confidence": 0.1,
                    "reasoning": "Aus dem Testskript.",
                }
            )

        umgebung = geclustert(
            semantik_settings,
            chat=langsam,
            models=models_config(max_concurrency=4),
        )

        umgebung.relations.run(scope="engineering")

        assert hoechststand > 1, "Die Fragen liefen nacheinander statt gleichzeitig."

    def test_ohne_konfiguration_bleibt_es_beim_alten_ablauf(
        self, semantik_settings: Settings
    ) -> None:
        """`max_concurrency: 1` ist die Vorgabe — wer nichts einstellt, ändert nichts."""
        umgebung = geclustert(semantik_settings, chat=antwortet(None))

        assert umgebung.relations._gleichzeitig() == 1

    def test_das_mass_kommt_aus_der_anbieterkonfiguration(
        self, semantik_settings: Settings
    ) -> None:
        """Kein Literal im Dienst: Das Rate-Limit ist eine Eigenschaft des Anbieters (§6.1)."""
        umgebung = geclustert(
            semantik_settings, chat=antwortet(None), models=models_config(max_concurrency=6)
        )

        assert umgebung.relations._gleichzeitig() == 6


class TestGrenzen:
    def test_ein_erschoepftes_budget_endet_mit_teilergebnis(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        umgebung = geclustert(
            Settings.model_validate({**minimal_config_dict, "clustering": {"neighbors_k": 4}}),
            chat=antwortet("depends_on"),
        )
        knapp = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {"neighbors_k": 4},
                "budget": {"max_model_calls_per_run": 0},
            }
        )
        umgebung.relations._settings = knapp
        umgebung.relations._router = ModelRouterService(
            knapp,
            models_config(),
            umgebung.clients,
            unit_of_work=umgebung.uow,
            sleep=lambda _: None,
        )

        bericht = umgebung.relations.run(scope="engineering")

        assert bericht.budget_exceeded is True

    def test_ein_bericht_enthaelt_keine_inhalte(self, semantik_settings: Settings) -> None:
        """§21.1: Zahlen und Namen, nie Inhalte."""
        umgebung = geclustert(semantik_settings, chat=antwortet("depends_on"))

        serialisiert = json.dumps(
            umgebung.relations.run(scope="engineering").as_dict(), ensure_ascii=False
        )

        assert THEMEN["warehouse"][0][2] not in serialisiert
