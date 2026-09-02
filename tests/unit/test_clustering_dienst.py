"""Cluster-Bildung, Stabilitätsschwelle und Kurationsschutz (§13.2 bis §13.4).

Die Abnahme der Stufe 8 aus §24 steht hier Kriterium für Kriterium:

* "Die drei Themenfelder des Korpus ergeben mindestens drei Cluster" — :class:`TestClusterBildung`
* "das Grenzdokument landet stabil" — :class:`TestClusterBildung`
* "mindestens zwei Cluster sind über ``related`` verbunden" — :class:`TestVerwandteCluster`
* "eine Zuordnung entsteht erst im zweiten Lauf" — :class:`TestStabilitaet`
* "eine kuratierte Zuordnung überlebt zwei Läufe" — :class:`TestKurationsschutz`
* "ohne verfügbares Embedding-Modell degradiert die Suche sichtbar" — :class:`TestSuche`
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from support.semantik import (
    GRENZDOKUMENT,
    ISOLIERT,
    JETZT,
    THEMEN,
    Umgebung,
    antwort_skript,
    baue,
    befuellen,
    konzept,
    korpus,
    models_config,
)
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeType
from wissensgraph.domain.edges import EdgeDraft

pytestmark = pytest.mark.unit


def vorbereitet(settings: Settings, **kwargs: Any) -> Umgebung:
    """Ein eingebetteter Korpus — der Ausgangspunkt jedes Clustering-Tests."""
    umgebung = baue(settings, **kwargs)
    befuellen(umgebung, korpus())
    umgebung.embeddings.run(scope="engineering")
    return umgebung


class TestClusterBildung:
    def test_drei_themenfelder_ergeben_drei_cluster(self, semantik_settings: Settings) -> None:
        """§24, Stufe 8, erstes Kriterium."""
        umgebung = vorbereitet(semantik_settings)

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.components == 3
        assert bericht.clusters_created == 3

    def test_das_grenzdokument_landet_stabil(self, semantik_settings: Settings) -> None:
        """Es berührt zwei Themen und muss trotzdem über drei Läufe hinweg dort bleiben."""
        umgebung = vorbereitet(semantik_settings)
        for _ in range(3):
            umgebung.clusters.run(scope="engineering")

        zugehoerig = [
            cluster_id
            for cluster_id in umgebung.cluster_ids()
            if GRENZDOKUMENT[0] in umgebung.mitglieder(cluster_id)
        ]

        assert len(zugehoerig) == 1
        # Es gehört zu den Faktentabellen, nicht zum Incident: Dort ist sein Wortschatz zu Hause.
        assert THEMEN["warehouse"][0][0] in umgebung.mitglieder(zugehoerig[0])

    def test_ein_isolierter_knoten_bildet_kein_cluster(self, semantik_settings: Settings) -> None:
        """§13.2 Schritt 3: Komponenten unter ``min_cluster_size`` bleiben ungeclustert."""
        umgebung = vorbereitet(semantik_settings)

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.too_small >= 1
        for cluster_id in umgebung.cluster_ids():
            assert ISOLIERT[0] not in umgebung.mitglieder(cluster_id)

    def test_ein_cluster_ist_ein_konzept_mit_titel(self, semantik_settings: Settings) -> None:
        """Kein neues Schema: Cluster sind Konzepte und damit traversierbar und kuratierbar."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        cluster = umgebung.state().concepts[umgebung.cluster_ids()[0]]

        assert cluster.type == defaults.CONCEPT_TYPE_CLUSTER
        assert cluster.title == "Testgruppe"
        assert cluster.generated_by is not None

    def test_probelauf_zaehlt_und_schreibt_nichts(self, semantik_settings: Settings) -> None:
        """§16.2/§19: ``dry_run`` sagt, was entstünde — und hinterlässt keinen Bestand."""
        umgebung = vorbereitet(semantik_settings)

        bericht = umgebung.clusters.run(scope="engineering", dry_run=True)

        assert bericht.components == 3
        assert bericht.clusters_created == 3
        assert bericht.members_added > 0
        # Nichts geschrieben: keine Cluster, keine Mitgliedschaften, kein Journal.
        assert not umgebung.cluster_ids()
        assert not [
            eintrag
            for eintrag in umgebung.state().changes
            if eintrag.change_type is ChangeType.CLUSTER_ASSIGNED
        ]

    def test_probelauf_erkennt_bestehende_cluster_wieder(self, semantik_settings: Settings) -> None:
        """Nach einem echten Lauf meldet der Probelauf Wiedererkennung statt Neuanlage."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        umgebung.embeddings.run(scope="engineering")

        bericht = umgebung.clusters.run(scope="engineering", dry_run=True)

        assert bericht.clusters_created == 0
        assert bericht.clusters_matched == 3

    def test_ohne_embeddings_passiert_nichts(self, semantik_settings: Settings) -> None:
        """Die semantische Schicht ist eine Voraussetzung, kein Versprechen (§11.5)."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.components == 0
        assert bericht.clusters_created == 0

    def test_cluster_sind_selbst_keine_mitglieder(self, semantik_settings: Settings) -> None:
        """Ein Cluster im Cluster wäre die Hierarchie, die §24 für diese Stufe ausnimmt."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        umgebung.embeddings.run(scope="engineering")

        umgebung.clusters.run(scope="engineering")

        cluster = set(umgebung.cluster_ids())
        for cluster_id in cluster:
            assert not (umgebung.mitglieder(cluster_id) & cluster)

    def test_ein_zentroid_entsteht_je_cluster(self, semantik_settings: Settings) -> None:
        """§13.2 Schritt 5 — der Mittelwert der Mitgliedsvektoren."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        assert len(umgebung.state().centroids) == 3
        einer = next(iter(umgebung.state().centroids.values()))
        assert einer.member_count >= 3


class TestStabilitaet:
    def test_eine_zuordnung_entsteht_erst_im_zweiten_lauf(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 8: "eine Zuordnung entsteht erst im zweiten Lauf" (§13.3)."""
        umgebung = vorbereitet(semantik_settings)

        erst = umgebung.clusters.run(scope="engineering")
        assert erst.members_added == 0
        assert erst.candidates == 13

        zweit = umgebung.clusters.run(scope="engineering")
        assert zweit.members_added == 13

    def test_das_cluster_ueberlebt_die_wartezeit(self, semantik_settings: Settings) -> None:
        """Ohne diese Zuordnung begänne jeder Lauf von vorn und die Schwelle wäre nie erreichbar."""
        umgebung = vorbereitet(semantik_settings)

        umgebung.clusters.run(scope="engineering")
        vorher = umgebung.cluster_ids()
        umgebung.clusters.run(scope="engineering")

        assert umgebung.cluster_ids() == vorher

    def test_derselbe_lauf_zaehlt_nicht_zweimal(self, semantik_settings: Settings) -> None:
        """Sonst brächte ein Lauf die Schwelle im Alleingang zum Auslösen."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        kandidaten = {
            (eintrag.concept_id, eintrag.cluster_id): eintrag.seen_count
            for eintrag in _kandidaten(umgebung)
        }

        assert set(kandidaten.values()) == {1}

    def test_ein_nicht_bestaetigter_kandidat_verfaellt(self, semantik_settings: Settings) -> None:
        """§13.3: "Kandidaten, die in einem Lauf nicht wieder bestätigt werden, verfallen"."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        # Der isolierte Knoten war nie Kandidat; ein von Hand gesetzter verfällt beim nächsten Lauf.
        with umgebung.uow("shared") as uow:
            uow.clusters.bump(
                concept_id=ISOLIERT[0],
                cluster_id=umgebung.cluster_ids()[0],
                score=0.1,
                run_id=JETZT and __import__("uuid").uuid4(),
            )

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.expired >= 1
        assert all(eintrag.concept_id != ISOLIERT[0] for eintrag in _kandidaten(umgebung))

    def test_eine_geschriebene_mitgliedschaft_erzeugt_einen_journaleintrag(
        self, semantik_settings: Settings
    ) -> None:
        """§13.3: "wird die ``member``-Kante geschrieben und ein ``change_log``-Eintrag erzeugt"."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        umgebung.clusters.run(scope="engineering")

        arten = [entry.change_type for entry in umgebung.state().changes]

        assert ChangeType.CLUSTER_ASSIGNED in arten


class TestKurationsschutz:
    def test_eine_kuratierte_zuordnung_ueberlebt_zwei_laeufe(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 8: "eine kuratierte Zuordnung überlebt zwei Läufe" (§13.4)."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        cluster_id = umgebung.cluster_ids()[0]

        # Ein Mensch ordnet den isolierten Knoten von Hand zu.
        with umgebung.uow("shared") as uow:
            uow.edges.add(
                EdgeDraft(
                    from_store="shared",
                    from_id=cluster_id,
                    to_store="shared",
                    to_id=ISOLIERT[0],
                    kind=defaults.EDGE_KIND_MEMBER,
                    resolved=True,
                    curated=True,
                    generated_at=JETZT,
                )
            )

        umgebung.clusters.run(scope="engineering")
        umgebung.clusters.run(scope="engineering")

        assert ISOLIERT[0] in umgebung.mitglieder(cluster_id)

    def test_ein_von_hand_entferntes_mitglied_kommt_nicht_zurueck(
        self, semantik_settings: Settings
    ) -> None:
        """§13.4: "wird nicht erneut zugeordnet; Ausschluss … vermerkt" (Leitprinzip 15)."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        umgebung.clusters.run(scope="engineering")
        cluster_id = next(
            item
            for item in umgebung.cluster_ids()
            if THEMEN["urlaub"][0][0] in umgebung.mitglieder(item)
        )
        opfer = THEMEN["urlaub"][0][0]

        entfernt = umgebung.clusters.exclude_member(
            concept_id=opfer, cluster_id=cluster_id, store="shared"
        )
        umgebung.clusters.run(scope="engineering")
        umgebung.clusters.run(scope="engineering")

        assert entfernt is True
        assert opfer not in umgebung.mitglieder(cluster_id)

    def test_ein_ausschluss_ist_auch_ohne_bestehende_kante_moeglich(
        self, semantik_settings: Settings
    ) -> None:
        """Man kann etwas verbieten, das gerade nicht der Fall ist."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        entfernt = umgebung.clusters.exclude_member(
            concept_id=ISOLIERT[0], cluster_id=umgebung.cluster_ids()[0], store="shared"
        )

        assert entfernt is False
        with umgebung.uow("shared") as uow:
            assert (ISOLIERT[0], umgebung.cluster_ids()[0]) in uow.clusters.exclusions()

    def test_ein_umbenanntes_cluster_wird_nicht_ueberschrieben(
        self, semantik_settings: Settings
    ) -> None:
        """§13.4: "``cluster_labeling`` überschreibt den Titel nicht mehr"."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        cluster_id = umgebung.cluster_ids()[0]
        with umgebung.uow("shared") as uow:
            vorhanden = uow.concepts.get(cluster_id)
            assert vorhanden is not None
            uow.concepts.save(
                vorhanden.model_copy(update={"title": "Von Hand benannt", "curated": True})
            )

        umgebung.clusters.run(scope="engineering")

        assert umgebung.state().concepts[cluster_id].title == "Von Hand benannt"

    def test_eine_starke_mitgliederaenderung_wird_vorgeschlagen_nicht_angewandt(
        self, semantik_settings: Settings
    ) -> None:
        """§13.4: "Neubetitelung wird vorgeschlagen, nicht angewandt"."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")
        umgebung.clusters.run(scope="engineering")
        vorher = {
            cluster_id: umgebung.state().concepts[cluster_id].title
            for cluster_id in umgebung.cluster_ids()
        }

        # Ein Mitglied geht, zwei kommen dazu: Der Bestand ändert sich um weit über 20 Prozent,
        # ohne dass das Cluster unter ``min_cluster_size`` fällt.
        del umgebung.state().concepts[THEMEN["urlaub"][0][0]]
        befuellen(
            umgebung,
            [
                konzept(
                    f"confluence:31{nummer}",
                    title="Urlaubsantrag Personalportal Vertretung",
                    description="Urlaubsantrag Personalportal Vertretung Genehmigung "
                    "Abwesenheit Resturlaub.",
                )
                for nummer in (1, 2)
            ],
        )
        umgebung.embeddings.run(scope="engineering")
        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.relabel_proposed >= 1
        assert {
            cluster_id: umgebung.state().concepts[cluster_id].title for cluster_id in vorher
        } == vorher


class TestVerwandteCluster:
    def test_mindestens_zwei_cluster_sind_ueber_related_verbunden(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 8: "mindestens zwei Cluster sind über ``related`` verbunden" (§13.2)."""
        umgebung = vorbereitet(semantik_settings)

        bericht = umgebung.clusters.run(scope="engineering")

        kanten = umgebung.kanten(kind=defaults.EDGE_KIND_RELATED)
        assert bericht.related_edges >= 2
        assert len({edge.from_id for edge in kanten}) >= 2

    def test_eine_related_kante_traegt_die_gemessene_aehnlichkeit(
        self, semantik_settings: Settings
    ) -> None:
        """Eine Zahl, die sich nachrechnen lässt — kein Urteil eines Modells."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        kanten = umgebung.kanten(kind=defaults.EDGE_KIND_RELATED)

        assert kanten
        assert all(edge.weight is not None for edge in kanten)
        assert all(edge.generated_by == defaults.GENERATED_BY_CLUSTER_SIMILARITY for edge in kanten)

    def test_ohne_related_top_n_entstehen_keine(self, minimal_config_dict: dict[str, Any]) -> None:
        ohne = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {"neighbors_k": 4, "related_cluster_top_n": 0},
            }
        )
        umgebung = vorbereitet(ohne)

        assert umgebung.clusters.run(scope="engineering").related_edges == 0


class TestGrossenGrenzen:
    def test_eine_zu_grosse_komponente_wird_geteilt(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """§13.2 Schritt 3: "Komponenten über ``max_cluster_size`` werden rekursiv geteilt"."""
        eng = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {
                    "neighbors_k": 4,
                    "min_cluster_size": 2,
                    "max_cluster_size": 3,
                },
            }
        )
        umgebung = vorbereitet(eng)

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.split >= 1
        assert bericht.components > 3


class TestSuche:
    def test_ohne_embeddings_bleibt_die_suche_lexikalisch(
        self, semantik_settings: Settings
    ) -> None:
        """§24, Stufe 8: "ohne verfügbares Embedding-Modell degradiert die Suche sichtbar"."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        ergebnis = umgebung.graph.search("Faktentabellen", store="shared")

        assert ergebnis.mode == defaults.SEARCH_MODE_LEXICAL

    def test_mit_clustern_antwortet_die_suche_auf_cluster_ebene(
        self, semantik_settings: Settings
    ) -> None:
        """§12.4 Stufe 1: Trifft ein Cluster über der Schwelle, wird *es* geliefert."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        ergebnis = umgebung.graph.search(
            "Faktentabellen Warehouse Partitionen Ladestrecke Archivierung Sternschema",
            store="shared",
        )

        assert ergebnis.mode == defaults.SEARCH_MODE_CLUSTER
        assert all(hit.concept.type == defaults.CONCEPT_TYPE_CLUSTER for hit in ergebnis.hits)

    def test_ohne_cluster_treffer_faellt_sie_auf_die_dokumentebene(
        self, semantik_settings: Settings
    ) -> None:
        """§12.4 Stufe 2: hybride Suche aus Vektorähnlichkeit und Volltext."""
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        ergebnis = umgebung.graph.search("Kaffeemaschine entkalken", store="shared")

        assert ergebnis.mode == defaults.SEARCH_MODE_HYBRID

    def test_granularity_document_erzwingt_die_dokumentebene(
        self, semantik_settings: Settings
    ) -> None:
        umgebung = vorbereitet(semantik_settings)
        umgebung.clusters.run(scope="engineering")

        ergebnis = umgebung.graph.search(
            "Faktentabellen Warehouse Partitionen",
            store="shared",
            granularity=defaults.SEARCH_GRANULARITY_DOCUMENT,
        )

        assert ergebnis.mode == defaults.SEARCH_MODE_HYBRID

    def test_der_modus_steht_im_serialisierten_ergebnis(self, semantik_settings: Settings) -> None:
        """Ein stiller Qualitätsverlust ohne Hinweis wäre die schlechtere Variante (§12.4)."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        als_dict = umgebung.graph.search("Faktentabellen", store="shared").as_dict()

        assert json.loads(json.dumps(als_dict))["mode"] == defaults.SEARCH_MODE_LEXICAL


def _kandidaten(umgebung: Umgebung) -> list[Any]:
    """Die vorgemerkten Zuordnungen eines Stores."""
    with umgebung.uow("shared") as uow:
        return list(uow.clusters.candidates())


class TestBetitelungGleichzeitig:
    """§11.2 für die Cluster-Betitelung — der letzte Modellaufruf, der noch anstand.

    Eine Frage je neuem Cluster, keine hängt an einer anderen, und jede wartet fast nur auf das
    Netz. Bei 141 neuen Clustern und knapp einer Sekunde Antwortzeit sind das über zwei Minuten
    reines Warten in einem Lauf, der sonst rechnet.
    """

    @staticmethod
    def _messender_chat() -> tuple[Any, dict[str, int]]:
        """Ein Chat, der mitschreibt, wie viele Fragen sich zeitlich überschneiden."""
        import threading
        import time

        stand = {"jetzt": 0, "hoechst": 0}
        sperre = threading.Lock()

        def chat(prompt: Any) -> str:
            system = prompt.system or ""
            if "Themengruppen" not in system:
                return antwort_skript(prompt)
            with sperre:
                stand["jetzt"] += 1
                stand["hoechst"] = max(stand["hoechst"], stand["jetzt"])
            time.sleep(0.05)
            with sperre:
                stand["jetzt"] -= 1
            return json.dumps({"title": "Ein Thema", "description": "Ein Satz."})

        return chat, stand

    def test_die_titel_werden_gleichzeitig_geholt(self, semantik_settings: Settings) -> None:
        chat, stand = self._messender_chat()
        umgebung = vorbereitet(
            semantik_settings, chat=chat, models=models_config(max_concurrency=4)
        )

        umgebung.clusters.run(scope="engineering")

        assert stand["hoechst"] > 1, "Die Betitelungen liefen nacheinander statt gleichzeitig."

    def test_ohne_konfiguration_bleibt_es_beim_alten_ablauf(
        self, semantik_settings: Settings
    ) -> None:
        """``max_concurrency: 1`` ist die Vorgabe — wer nichts einstellt, ändert nichts."""
        chat, stand = self._messender_chat()
        umgebung = vorbereitet(semantik_settings, chat=chat)

        umgebung.clusters.run(scope="engineering")

        assert stand["hoechst"] == 1

    def test_dasselbe_ergebnis_wie_nacheinander(self, semantik_settings: Settings) -> None:
        # Der eigentliche Prüfstein: Nebenläufigkeit darf den Bestand nicht verändern. Zähler,
        # Titel und Mitgliedschaften müssen gleich herauskommen.
        einer = vorbereitet(semantik_settings)
        viele = vorbereitet(semantik_settings, models=models_config(max_concurrency=4))

        a = einer.clusters.run(scope="engineering")
        b = viele.clusters.run(scope="engineering")

        assert b.as_dict() == a.as_dict()

    def test_ein_gescheiterter_titel_steht_im_bericht(self, semantik_settings: Settings) -> None:
        """Verbucht wird der Reihe nach: Der Fehler kommt aus dem Thread zurück, gezählt wird
        er beim Aufrufer — sonst wäre der Zähler ein Wettlauf."""

        def kaputt(prompt: Any) -> str:
            if "Themengruppen" in (prompt.system or ""):
                return "kein JSON"
            return antwort_skript(prompt)

        umgebung = vorbereitet(
            semantik_settings, chat=kaputt, models=models_config(max_concurrency=4)
        )

        bericht = umgebung.clusters.run(scope="engineering")

        assert bericht.errors, "Ein misslungener Titel muss im Bericht stehen."
        assert bericht.clusters_created == 3, "Der Lauf darf daran nicht scheitern."
