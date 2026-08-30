"""Die semantische Schicht gegen eine echte PostgreSQL-Instanz (§13 bis §15, §22.1).

Was hier geprüft wird, kann kein Fake beantworten: ``pgvector`` rechnet die Kosinusdistanz selbst,
der HNSW-Index entscheidet über die Reihenfolge der Nachbarn, ``ON CONFLICT`` bestimmt, was ein
zweiter Lauf tut, und ``v_loose_concepts`` zählt Kanten in SQL. Die Unit-Tests zeigen, dass die
Verfahren stimmen; diese hier zeigen, dass die Datenbank sie auch so ausführt.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from conftest import TEST_EMBEDDING_DIM
from support.semantik import antwort_skript, konzept, korpus, models_config
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.hashing import content_hash
from wissensgraph.infrastructure.db import StoreRegistry, upgrade_all
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.ports.models import ModelCall
from wissensgraph.services.clustering import ClusterService
from wissensgraph.services.embeddings import EmbeddingService
from wissensgraph.services.orphans import OrphanRequest, OrphanService
from wissensgraph.services.relations import RelationService
from wissensgraph.services.router import ModelRouterService
from wissensgraph.testing.models import FakeClients

pytestmark = pytest.mark.integration


@pytest.fixture
def uow(postgres_settings: Settings, postgres_registry: StoreRegistry) -> UnitOfWorkFactory:
    """Eine Arbeitseinheiten-Fabrik auf frisch migrierten Testdatenbanken."""
    upgrade_all(postgres_settings, postgres_registry)
    return UnitOfWorkFactory(postgres_registry)


@pytest.fixture
def dienste(postgres_settings: Settings, uow: UnitOfWorkFactory) -> Any:
    """Router und alle vier Läufe gegen PostgreSQL — mit dem Fake-Provider statt eines Modells."""
    settings = Settings.model_validate(
        {
            **postgres_settings.model_dump(mode="json"),
            "clustering": {"neighbors_k": 4, "min_cluster_size": 3, "stability_runs": 2},
        }
    )
    clients = FakeClients(dim=TEST_EMBEDDING_DIM, chat=antwort_skript)
    router = ModelRouterService(
        settings,
        models_config(dim=TEST_EMBEDDING_DIM),
        clients,
        unit_of_work=uow,
        sleep=lambda _: None,
    )
    relations = RelationService(settings, uow, router)

    class Buendel:
        pass

    buendel = Buendel()
    buendel.settings = settings  # type: ignore[attr-defined]
    buendel.uow = uow  # type: ignore[attr-defined]
    buendel.router = router  # type: ignore[attr-defined]
    buendel.clients = clients  # type: ignore[attr-defined]
    buendel.embeddings = EmbeddingService(settings, uow, router)  # type: ignore[attr-defined]
    buendel.clusters = ClusterService(settings, uow, router)  # type: ignore[attr-defined]
    buendel.relations = relations  # type: ignore[attr-defined]
    buendel.orphans = OrphanService(  # type: ignore[attr-defined]
        settings, uow, router, relations=relations
    )
    return buendel


def _speichern(uow: UnitOfWorkFactory, konzepte: list[Concept]) -> None:
    with uow("shared") as einheit:
        for concept in konzepte:
            einheit.concepts.save(concept)


class TestVektoren:
    def test_ein_vektor_ueberlebt_die_runde_durch_die_datenbank(
        self, uow: UnitOfWorkFactory
    ) -> None:
        """pgvector speichert und liest ``vector(n)`` — ohne diesen Test merkt es niemand."""
        _speichern(uow, [konzept("confluence:1", title="Titel", description="Text")])
        vektor = tuple(float(i) / 10 for i in range(TEST_EMBEDDING_DIM))

        with uow("shared") as einheit:
            einheit.embeddings.save(
                concept_id="confluence:1", model_key="p:m", vector=vektor, source_hash="h"
            )
        with uow("shared") as einheit:
            gelesen = einheit.embeddings.get(concept_id="confluence:1", model_key="p:m")

        assert gelesen == pytest.approx(vektor)

    def test_ein_zweites_speichern_ersetzt_statt_zu_scheitern(self, uow: UnitOfWorkFactory) -> None:
        _speichern(uow, [konzept("confluence:1", title="Titel", description="Text")])
        with uow("shared") as einheit:
            for wert in (0.1, 0.2):
                einheit.embeddings.save(
                    concept_id="confluence:1",
                    model_key="p:m",
                    vector=[wert] * TEST_EMBEDDING_DIM,
                    source_hash=f"h{wert}",
                )

        with uow("shared") as einheit:
            gelesen = einheit.embeddings.get(concept_id="confluence:1", model_key="p:m")

        assert gelesen is not None
        assert gelesen[0] == pytest.approx(0.2)

    def test_veraltete_konzepte_werden_erkannt(self, uow: UnitOfWorkFactory) -> None:
        """§13.1: Verglichen wird ``source_hash`` mit ``content_hash`` — in SQL, nicht im Dienst."""
        concept = konzept("confluence:1", title="Titel", description="Text")
        _speichern(uow, [concept])
        with uow("shared") as einheit:
            einheit.embeddings.save(
                concept_id=concept.id,
                model_key="p:m",
                vector=[0.1] * TEST_EMBEDDING_DIM,
                source_hash=concept.content_hash,
            )

        with uow("shared") as einheit:
            aktuell = einheit.embeddings.outdated(model_key="p:m")
        _speichern(uow, [konzept("confluence:1", title="Anderer Titel", description="Text")])
        with uow("shared") as einheit:
            danach = einheit.embeddings.outdated(model_key="p:m")

        assert aktuell == ()
        assert danach == ("confluence:1",)

    def test_ein_anderer_modellschluessel_gilt_als_nicht_eingebettet(
        self, uow: UnitOfWorkFactory
    ) -> None:
        """§11.7: "Vektorsuchen filtern immer auf den aktiven ``model_key``"."""
        concept = konzept("confluence:1", title="Titel", description="Text")
        _speichern(uow, [concept])
        with uow("shared") as einheit:
            einheit.embeddings.save(
                concept_id=concept.id,
                model_key="alt:modell",
                vector=[0.1] * TEST_EMBEDDING_DIM,
                source_hash=concept.content_hash,
            )

        with uow("shared") as einheit:
            assert einheit.embeddings.outdated(model_key="neu:modell") == ("confluence:1",)
            assert einheit.embeddings.count(model_key="neu:modell") == 0

    def test_die_nachbarsuche_ordnet_nach_aehnlichkeit(self, uow: UnitOfWorkFactory) -> None:
        """Die Reihenfolge kommt aus pgvector — nicht aus Python."""
        _speichern(
            uow,
            [
                konzept("confluence:1", title="A", description="x"),
                konzept("confluence:2", title="B", description="x"),
                konzept("confluence:3", title="C", description="x"),
            ],
        )
        basis = [1.0] + [0.0] * (TEST_EMBEDDING_DIM - 1)
        nah = [0.9, 0.1] + [0.0] * (TEST_EMBEDDING_DIM - 2)
        fern = [0.0, 1.0] + [0.0] * (TEST_EMBEDDING_DIM - 2)
        with uow("shared") as einheit:
            for concept_id, vektor in (
                ("confluence:1", basis),
                ("confluence:2", nah),
                ("confluence:3", fern),
            ):
                einheit.embeddings.save(
                    concept_id=concept_id, model_key="p:m", vector=vektor, source_hash="h"
                )

        with uow("shared") as einheit:
            nachbarn = einheit.embeddings.neighbours(
                concept_id="confluence:1", model_key="p:m", k=2
            )

        assert [hit.concept_id for hit in nachbarn] == ["confluence:2", "confluence:3"]
        assert nachbarn[0].similarity > nachbarn[1].similarity

    def test_grabsteine_erscheinen_nicht_in_der_vektorsuche(self, uow: UnitOfWorkFactory) -> None:
        """Sie sind Erinnerung, kein Suchergebnis (§7.6)."""
        lebt = konzept("confluence:1", title="A", description="x")
        tot = konzept("confluence:2", title="B", description="x").model_copy(
            update={"status": ConceptStatus.TOMBSTONE}
        )
        _speichern(uow, [lebt, tot])
        with uow("shared") as einheit:
            for concept_id in ("confluence:1", "confluence:2"):
                einheit.embeddings.save(
                    concept_id=concept_id,
                    model_key="p:m",
                    vector=[1.0] + [0.0] * (TEST_EMBEDDING_DIM - 1),
                    source_hash="h",
                )

        with uow("shared") as einheit:
            treffer = einheit.embeddings.search(
                vector=[1.0] + [0.0] * (TEST_EMBEDDING_DIM - 1), model_key="p:m", limit=10
            )

        assert [hit.concept_id for hit in treffer] == ["confluence:1"]


class TestKandidaten:
    def test_derselbe_lauf_zaehlt_nicht_zweimal(self, uow: UnitOfWorkFactory) -> None:
        """§13.3 in SQL: Das ``CASE`` in ``ON CONFLICT DO UPDATE`` entscheidet darüber."""
        lauf = uuid4()
        with uow("shared") as einheit:
            erst = einheit.clusters.bump(
                concept_id="c:1", cluster_id="cluster:1", score=0.5, run_id=lauf
            )
            wieder = einheit.clusters.bump(
                concept_id="c:1", cluster_id="cluster:1", score=0.6, run_id=lauf
            )
            danach = einheit.clusters.bump(
                concept_id="c:1", cluster_id="cluster:1", score=0.7, run_id=uuid4()
            )

        assert (erst, wieder, danach) == (1, 1, 2)

    def test_ein_ausschluss_ueberlebt_das_verfallen(self, uow: UnitOfWorkFactory) -> None:
        """§13.4: Er ist eine Entscheidung eines Menschen, keine Beobachtung."""
        lauf = uuid4()
        with uow("shared") as einheit:
            einheit.clusters.bump(concept_id="c:1", cluster_id="cluster:1", score=0.5, run_id=lauf)
            einheit.clusters.exclude(concept_id="c:2", cluster_id="cluster:1")

        with uow("shared") as einheit:
            verfallen = einheit.clusters.expire(run_id=uuid4())
            uebrig = einheit.clusters.exclusions()

        assert verfallen == 1
        assert uebrig == frozenset({("c:2", "cluster:1")})

    def test_zentroide_lassen_sich_untereinander_vergleichen(self, uow: UnitOfWorkFactory) -> None:
        """§13.2 Schritt 6 — die ``related``-Kanten hängen an dieser Abfrage."""
        _speichern(
            uow,
            [
                konzept(f"cluster:{i}", title=f"C{i}", concept_type=defaults.CONCEPT_TYPE_CLUSTER)
                for i in (1, 2, 3)
            ],
        )
        with uow("shared") as einheit:
            einheit.clusters.save_centroid(
                cluster_id="cluster:1",
                model_key="p:m",
                vector=[1.0] + [0.0] * (TEST_EMBEDDING_DIM - 1),
                member_count=3,
            )
            einheit.clusters.save_centroid(
                cluster_id="cluster:2",
                model_key="p:m",
                vector=[0.9, 0.1] + [0.0] * (TEST_EMBEDDING_DIM - 2),
                member_count=3,
            )
            einheit.clusters.save_centroid(
                cluster_id="cluster:3",
                model_key="p:m",
                vector=[0.0, 1.0] + [0.0] * (TEST_EMBEDDING_DIM - 2),
                member_count=3,
            )

        with uow("shared") as einheit:
            aehnlich = einheit.clusters.similar_centroids(
                cluster_id="cluster:1", model_key="p:m", limit=2
            )

        assert [hit.concept_id for hit in aehnlich] == ["cluster:2", "cluster:3"]


class TestLoseKnoten:
    def test_die_sicht_zaehlt_member_kanten_nicht_mit(self, uow: UnitOfWorkFactory) -> None:
        """§7.7: Ein Konzept, das nur in einem Cluster hängt, ist thematisch unvernetzt."""
        _speichern(
            uow,
            [
                konzept("confluence:1", title="Im Cluster", description="x"),
                konzept("confluence:2", title="Verbunden", description="x"),
                konzept("cluster:1", title="Gruppe", concept_type=defaults.CONCEPT_TYPE_CLUSTER),
            ],
        )
        with uow("shared") as einheit:
            einheit.edges.add(
                EdgeDraft(
                    from_store="shared",
                    from_id="cluster:1",
                    to_store="shared",
                    to_id="confluence:1",
                    kind=defaults.EDGE_KIND_MEMBER,
                    resolved=True,
                )
            )
            einheit.edges.add(
                EdgeDraft(
                    from_store="shared",
                    from_id="confluence:2",
                    to_store="shared",
                    to_id="confluence:1",
                    kind="references",
                    resolved=True,
                )
            )

        with uow("shared") as einheit:
            lose = {item.id for item in einheit.concepts.loose(threshold=1)}

        # confluence:1 hat eine 'references'-Kante und ist damit nicht mehr lose;
        # das Cluster selbst hat nur eine 'member'-Kante und bleibt es.
        assert "confluence:1" not in lose
        assert {"cluster:1"} <= lose

    def test_eine_kante_hebt_einen_knoten_aus_der_sicht(self, uow: UnitOfWorkFactory) -> None:
        """Das Abbruchkriterium aus §15: Mit jedem Lauf schrumpft die Menge."""
        _speichern(
            uow,
            [
                konzept("confluence:1", title="A", description="x"),
                konzept("confluence:2", title="B", description="x"),
            ],
        )
        with uow("shared") as einheit:
            vorher = len(einheit.concepts.loose(threshold=1))
            einheit.edges.add(
                EdgeDraft(
                    from_store="shared",
                    from_id="confluence:1",
                    to_store="shared",
                    to_id="confluence:2",
                    kind="references",
                    resolved=True,
                )
            )

        with uow("shared") as einheit:
            nachher = len(einheit.concepts.loose(threshold=1))

        assert (vorher, nachher) == (2, 0)


class TestKantenAnlegen:
    def test_ein_zweites_anlegen_desselben_tripels_meldet_none(
        self, uow: UnitOfWorkFactory
    ) -> None:
        """``ON CONFLICT DO NOTHING`` statt einer Vorabfrage — sonst gäbe es ein Zeitfenster."""
        _speichern(
            uow,
            [
                konzept("confluence:1", title="A", description="x"),
                konzept("confluence:2", title="B", description="x"),
            ],
        )
        draft = EdgeDraft(
            from_store="shared",
            from_id="confluence:1",
            to_store="shared",
            to_id="confluence:2",
            kind="references",
            resolved=True,
        )

        with uow("shared") as einheit:
            erst = einheit.edges.add(draft)
            wieder = einheit.edges.add(draft)

        assert erst is not None
        assert wieder is None

    def test_kinds_between_sieht_beide_richtungen(self, uow: UnitOfWorkFactory) -> None:
        """§14.5: Ein Paar, dessen Beziehung schon steht, ist keine offene Frage mehr."""
        _speichern(
            uow,
            [
                konzept("confluence:1", title="A", description="x"),
                konzept("confluence:2", title="B", description="x"),
            ],
        )
        with uow("shared") as einheit:
            einheit.edges.add(
                EdgeDraft(
                    from_store="shared",
                    from_id="confluence:2",
                    to_store="shared",
                    to_id="confluence:1",
                    kind="depends_on",
                    resolved=True,
                )
            )

        with uow("shared") as einheit:
            arten = einheit.edges.kinds_between(from_id="confluence:1", to_id="confluence:2")

        assert arten == frozenset({"depends_on"})


class TestModellaufrufe:
    def test_ein_aufruf_wird_verbucht_und_ausgewertet(self, uow: UnitOfWorkFactory) -> None:
        lauf = uuid4()
        with uow("shared") as einheit:
            einheit.model_calls.record(
                ModelCall(
                    task=defaults.TASK_EMBEDDING,
                    provider="p",
                    model="m",
                    status=defaults.MODEL_CALL_OK,
                    store="shared",
                    run_id=lauf,
                    tokens_in=100,
                    tokens_out=20,
                    cost_estimate=0.5,
                )
            )
            einheit.model_calls.record(
                ModelCall(
                    task=defaults.TASK_EMBEDDING,
                    provider="p",
                    model="m",
                    status=defaults.MODEL_CALL_CACHE_HIT,
                    store="shared",
                    run_id=lauf,
                    cache_hit=True,
                )
            )

        with uow("shared") as einheit:
            zeilen = einheit.model_calls.usage(run_id=lauf)
            aufrufe, kosten = einheit.model_calls.spent(lauf)

        assert len(zeilen) == 1
        assert zeilen[0].calls == 2
        assert zeilen[0].cache_hits == 1
        assert zeilen[0].tokens_in == 100
        # Der Wächter zählt nur, was wirklich hinausging (§11.6).
        assert (aufrufe, kosten) == (1, 0.5)

    def test_ein_verhinderter_aufruf_zaehlt_als_fehlschlag(self, uow: UnitOfWorkFactory) -> None:
        """``wg models usage`` soll auch zeigen, was *nicht* passiert ist."""
        with uow("shared") as einheit:
            einheit.model_calls.record(
                ModelCall(
                    task=defaults.TASK_EMBEDDING,
                    provider="p",
                    model="m",
                    status=defaults.MODEL_CALL_BUDGET_DENIED,
                    store="shared",
                )
            )

        with uow("shared") as einheit:
            zeilen = einheit.model_calls.usage()

        assert zeilen[0].failures == 1


class TestVollstaendigerDurchlauf:
    def test_der_ganze_weg_von_embedding_bis_vernetzung(self, dienste: Any) -> None:
        """Die vier Läufe hintereinander gegen echte Datenbanken — der Betriebsfall."""
        _speichern(dienste.uow, korpus())

        eingebettet = dienste.embeddings.run(scope="engineering")
        dienste.clusters.run(scope="engineering")
        geclustert = dienste.clusters.run(scope="engineering")
        beziehungen = dienste.relations.run(scope="engineering")
        vernetzt = dienste.orphans.run(
            OrphanRequest(scope="engineering", use_llm=False, proximity_auto_commit=0.2)
        )

        assert eingebettet.embedded == 14
        assert geclustert.members_added > 0
        # Das Fake-Skript antwortet auf jede Beziehungsfrage mit "keine Beziehung" (§14.2).
        assert beziehungen.calls > 0
        assert beziehungen.edges_written == 0
        assert vernetzt.loose_after <= vernetzt.loose_before

    def test_die_suche_wird_mit_embeddings_hybrid(self, dienste: Any) -> None:
        """§12.4: Der Modus steht im Ergebnis und wechselt mit der Datenlage."""
        from wissensgraph.services.graph import GraphService

        graph = GraphService(dienste.settings, dienste.uow, router=dienste.router)
        _speichern(dienste.uow, korpus())

        vorher = graph.search("Faktentabellen", store="shared")
        dienste.embeddings.run(scope="engineering")
        nachher = graph.search("Kaffeemaschine entkalken", store="shared")

        assert vorher.mode == defaults.SEARCH_MODE_LEXICAL
        assert nachher.mode == defaults.SEARCH_MODE_HYBRID

    def test_ein_zweiter_embedding_lauf_kostet_nichts(self, dienste: Any) -> None:
        _speichern(dienste.uow, korpus())
        dienste.embeddings.run(scope="engineering")

        wieder = dienste.embeddings.run(scope="engineering")

        assert wieder.considered == 0

    def test_die_lauf_statistik_ist_serialisierbar(self, dienste: Any) -> None:
        """Sie geht als JSONB in ``runs.stats`` — was sich nicht serialisieren lässt, fällt aus."""
        _speichern(dienste.uow, korpus())

        bericht = dienste.embeddings.run(scope="engineering")

        assert json.loads(json.dumps(bericht.as_dict()))["embedded"] == 14


class TestClusterKonzepte:
    def test_ein_cluster_wird_als_konzept_gespeichert(self, dienste: Any) -> None:
        _speichern(dienste.uow, korpus())
        dienste.embeddings.run(scope="engineering")

        dienste.clusters.run(scope="engineering")

        with dienste.uow("shared") as einheit:
            cluster = einheit.concepts.in_scope(
                "engineering", concept_type=defaults.CONCEPT_TYPE_CLUSTER
            )
        assert len(cluster) == 3
        assert all(item.content_hash for item in cluster)

    def test_der_content_hash_eines_clusters_folgt_seinem_namen(self) -> None:
        """Ein Cluster hat keinen Quellinhalt — was sich ändern kann, ist sein Name."""
        assert content_hash(title="A", description="B", body=None) != content_hash(
            title="A", description="C", body=None
        )
