"""Kuration über HTTP: Kanten, Cluster, Undo (§16.2, §17.2 bis §17.4).

Zwei Tests hier sind Abnahmekriterien aus §24, Stufe 11, und keine Beispiele:

* Ein Mitglied wird verschoben und **überlebt einen erneuten Clustering-Lauf**.
* Ein Modellvorschlag wird verworfen und **entsteht im Folgelauf nicht neu**.

Beide prüfen deshalb nicht die HTTP-Antwort, sondern was danach passiert, wenn der Lauf wieder
über denselben Bestand geht.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from support.api import AUTH, api, api_settings, befuellen, state
from support.semantik import konzept, korpus
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    return api_settings(
        {
            **minimal_config_dict,
            "clustering": {"neighbors_k": 4, "min_cluster_size": 3, "stability_runs": 1},
        }
    )


class TestKanten:
    def test_legt_eine_kuratierte_kante_an(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            antwort = client.post(
                "/api/v1/edges",
                json={
                    "from_id": "confluence:100",
                    "to_id": "confluence:200",
                    "kind": "references",
                },
                headers=AUTH,
            )

            assert antwort.status_code == 201
            kante = antwort.json()["edge"]
            assert kante["curated"] is True
            assert kante["verified_by"] == "user:token"

    def test_unbekannte_kantenart_ist_409(self, settings: Settings, tmp_path: Path) -> None:
        """§7.7: Kantenarten stehen in der Konfiguration, nicht im Code."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            antwort = client.post(
                "/api/v1/edges",
                json={"from_id": "confluence:100", "to_id": "confluence:200", "kind": "erfunden"},
                headers=AUTH,
            )

            assert antwort.status_code == 409
            assert "erfunden" in antwort.json()["detail"]

    def test_dieselbe_kante_zweimal_ist_409(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            koerper = {"from_id": "confluence:100", "to_id": "confluence:200"}
            client.post("/api/v1/edges", json=koerper, headers=AUTH)

            assert client.post("/api/v1/edges", json=koerper, headers=AUTH).status_code == 409

    def test_bestaetigen_setzt_verifikation_und_kuration(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            kante = _generierte_kante(runtime)

            payload = client.post(f"/api/v1/edges/{kante.id}/verify", headers=AUTH).json()["edge"]

            assert payload["verified_by"] == "user:token"
            assert payload["curated"] is True

    def test_loeschen_hinterlaesst_keinen_negativvermerk(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Löschen heißt "hier nicht", Verwerfen heißt "gibt es nicht" — nur eines bindet."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            kante = _generierte_kante(runtime)

            client.delete(f"/api/v1/edges/{kante.id}", headers=AUTH)

            assert state(runtime).rejections == {}

    def test_verwerfen_vermerkt_das_tripel(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            kante = _generierte_kante(runtime)

            client.post(f"/api/v1/edges/{kante.id}/reject", json={"reason": "falsch"}, headers=AUTH)

            assert kante.triple in state(runtime).rejections


class TestVerworfeneKantenEntstehenNichtNeu:
    def test_der_folgelauf_schreibt_sie_nicht_wieder(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§24, Stufe 11: "der verworfene entsteht im Folgelauf nicht neu"."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            kante = _generierte_kante(runtime)
            tripel = kante.triple

            client.post(f"/api/v1/edges/{kante.id}/reject", json={}, headers=AUTH)
            runtime.orphans.run(_orphan_request(scope="engineering", use_llm=False, threshold=2))

            assert all(vorhanden.triple != tripel for vorhanden in state(runtime).edges)

    def test_die_paarpruefung_ueberspringt_verworfene_paare(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§14.5: Ein verworfenes Paar kostet keinen weiteren Modellaufruf.

        Verworfen wird eine *semantische* Kante innerhalb eines Clusters — nur solche Paare
        betrachtet §14.2 überhaupt, und nur dort spart der Vermerk etwas ein.
        """
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            kante = _generierte_kante(
                runtime,
                from_id="confluence:100",
                to_id="confluence:101",
                kind=defaults.EDGE_KIND_REFERENCES,
            )
            client.post(f"/api/v1/edges/{kante.id}/reject", json={}, headers=AUTH)

            bericht = runtime.relations.run(scope="engineering")

            assert bericht.pairs_rejected >= 1


class TestClusterArbeitsplatz:
    def test_ein_verschobenes_mitglied_ueberlebt_den_naechsten_lauf(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§24, Stufe 11: das zentrale Kriterium des Cluster-Arbeitsplatzes."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            cluster = _cluster_von(runtime, "confluence:100")
            ziel = _anderes_cluster(runtime, cluster)

            client.delete(f"/api/v1/clusters/{cluster}/members/confluence:100", headers=AUTH)
            client.post(
                f"/api/v1/clusters/{ziel}/members",
                json={"concept_ids": ["confluence:100"]},
                headers=AUTH,
            )
            runtime.clusters.run(scope="engineering")

            assert _cluster_von(runtime, "confluence:100") == ziel

    def test_ein_von_hand_benanntes_cluster_wird_nicht_umbenannt(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§13.2 Schritt 4: Wer selbst benennt, will nicht umbenannt werden."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            cluster = _cluster_von(runtime, "confluence:100")

            client.patch(f"/api/v1/clusters/{cluster}", json={"title": "Mein Titel"}, headers=AUTH)
            runtime.clusters.run(scope="engineering")

            assert state(runtime).concepts[cluster].title == "Mein Titel"

    def test_ausgliedern_legt_ein_neues_cluster_an(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            cluster = _cluster_von(runtime, "confluence:100")

            antwort = client.post(
                f"/api/v1/clusters/{cluster}/split",
                json={"concept_ids": ["confluence:100"], "title": "Ausgegliedert"},
                headers=AUTH,
            )

            assert antwort.status_code == 200
            neu = antwort.json()["concept"]["id"]
            assert _cluster_von(runtime, "confluence:100") == neu

    def test_verschmelzen_haengt_die_kanten_um(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            quelle = _cluster_von(runtime, "confluence:100")
            ziel = _anderes_cluster(runtime, quelle)

            antwort = client.post(
                "/api/v1/clusters/merge",
                json={"source_id": quelle, "target_id": ziel},
                headers=AUTH,
            )

            assert antwort.status_code == 200
            assert quelle not in state(runtime).concepts
            assert _cluster_von(runtime, "confluence:100") == ziel

    def test_cluster_detail_nennt_mitglieder_und_zentroid_alter(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            cluster = _cluster_von(runtime, "confluence:100")

            payload = client.get(f"/api/v1/clusters/{cluster}", headers=AUTH).json()

            assert payload["members"]
            assert payload["centroid_age_seconds"] is not None

    def test_unbekanntes_cluster_ist_404(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            assert client.get("/api/v1/clusters/cluster:0", headers=AUTH).status_code == 404


class TestWarteschlange:
    def test_zeigt_unbestaetigte_kanten_nach_confidence(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            _generierte_kante(runtime, confidence=0.4)
            _generierte_kante(runtime, confidence=0.9, to_id="confluence:201")

            payload = client.get("/api/v1/curation/queue", headers=AUTH).json()

            werte = [item["confidence"] for item in payload["items"]]
            assert werte == sorted(werte, reverse=True)

    def test_eine_bestaetigte_kante_verlaesst_die_warteschlange(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            kante = _generierte_kante(runtime)
            client.post(f"/api/v1/edges/{kante.id}/verify", headers=AUTH)

            payload = client.get("/api/v1/curation/queue", headers=AUTH).json()

            assert payload["items"] == []


class TestUndo:
    def test_nimmt_eine_angelegte_kante_zurueck(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            angelegt = client.post(
                "/api/v1/edges",
                json={"from_id": "confluence:100", "to_id": "confluence:200"},
                headers=AUTH,
            ).json()

            antwort = client.post(
                "/api/v1/curation/undo",
                json={"entry_id": angelegt["entry"]["id"]},
                headers=AUTH,
            )

            assert antwort.status_code == 200
            assert not [edge for edge in state(runtime).edges if edge.from_id == "confluence:100"]

    def test_nimmt_ein_verwerfen_samt_negativvermerk_zurueck(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            kante = _generierte_kante(runtime)
            verworfen = client.post(
                f"/api/v1/edges/{kante.id}/reject", json={}, headers=AUTH
            ).json()

            client.post(
                "/api/v1/curation/undo",
                json={"entry_id": verworfen["entry"]["id"]},
                headers=AUTH,
            )

            assert state(runtime).rejections == {}
            assert any(edge.triple == kante.triple for edge in state(runtime).edges)

    def test_eine_inhaltliche_aenderung_ist_nicht_umkehrbar(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§7.4: Das Journal hält Feldnamen fest, keine Werte — und sagt das offen."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(
                runtime,
                [
                    konzept(
                        "note:frei",
                        title="Alt",
                        scope="personal",
                        store="personal",
                        concept_type="Note",
                    )
                ],
                store="personal",
            )
            geaendert = client.patch(
                "/api/v1/concepts/note:frei?store=personal",
                json={"title": "Neu"},
                headers=AUTH,
            ).json()

            antwort = client.post(
                "/api/v1/curation/undo",
                json={"entry_id": geaendert["entry"]["id"], "store": "personal"},
                headers=AUTH,
            )

            assert antwort.status_code == 409
            assert "Feldnamen" in antwort.json()["detail"]

    def test_unbekannter_eintrag_ist_404(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.post("/api/v1/curation/undo", json={"entry_id": 9999}, headers=AUTH)

            assert antwort.status_code == 404

    def test_das_journal_zeigt_was_umkehrbar_ist(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            client.post(
                "/api/v1/edges",
                json={"from_id": "confluence:100", "to_id": "confluence:200"},
                headers=AUTH,
            )

            payload = client.get("/api/v1/curation/journal", headers=AUTH).json()

            assert payload["items"][0]["undoable"] is True


# -- Hilfen -------------------------------------------------------------------


def _generierte_kante(
    runtime: Any,
    *,
    confidence: float = 0.7,
    from_id: str = "confluence:100",
    to_id: str = "confluence:200",
    kind: str = defaults.EDGE_KIND_RELATED,
) -> Any:
    """Eine Kante, wie ein Lauf sie erzeugt: mit Provenienz, ohne Kuration."""
    from wissensgraph.domain.edges import EdgeDraft

    with runtime._uow("shared") as uow:
        return uow.edges.add(
            EdgeDraft(
                from_store="shared",
                from_id=from_id,
                to_store="shared",
                to_id=to_id,
                kind=kind,
                confidence=confidence,
                resolved=True,
                generated_by="gemini:m/relation_extraction@v1",
            )
        )


def _orphan_request(*, scope: str, use_llm: bool, threshold: int) -> Any:
    from wissensgraph.services.orphans import OrphanRequest

    return OrphanRequest(scope=scope, use_llm=use_llm, loose_threshold=threshold)


def _cluster_von(runtime: Any, concept_id: str) -> str:
    """Das Cluster, in dem ein Konzept gerade Mitglied ist."""
    treffer = [
        edge.from_id
        for edge in state(runtime).edges
        if edge.kind == defaults.EDGE_KIND_MEMBER and edge.to_id == concept_id
    ]
    assert len(treffer) == 1, f"{concept_id} hängt in {treffer}"
    return treffer[0]


def _anderes_cluster(runtime: Any, nicht: str) -> str:
    """Irgendein anderes Cluster desselben Stores."""
    kandidaten = sorted(
        concept.id
        for concept in state(runtime).concepts.values()
        if concept.type == defaults.CONCEPT_TYPE_CLUSTER and concept.id != nicht
    )
    assert kandidaten
    return kandidaten[0]
