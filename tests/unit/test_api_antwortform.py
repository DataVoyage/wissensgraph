"""Die Antwortformen der API als Vertrag (§16.2).

Diese Datei gibt es wegen zweier Fehler, die alle anderen Tests durchgelassen haben:

* ``/graph/traverse`` und ``/graph/neighbors`` gaben ``edges`` als **Zahl** aus statt als Liste.
  §16.2 verlangt "Knoten + Kanten + Scores"; die Graph-Ansicht bekam Punkte ohne Verbindungen und
  stürzte beim Versuch ab, über eine Zahl zu iterieren.
* ``/graph/search`` liefert seine Treffer unter ``hits``; die Oberfläche las ``nodes``.

Beide Male lag es nicht an einer fehlenden Prüfung, sondern an der *Art* der Prüfung: Die UI-Tests
bilden die API nach, und die Nachbildung hatte dieselbe falsche Annahme wie der Code. Zwei
Beschreibungen desselben Vertrags bestätigten einander, ohne dass eine davon stimmte.

Deshalb prüft diese Datei die **echte** Antwort gegen die Feldnamen, auf die sich die Oberfläche
verlässt (``ui/src/api/types.ts``). Sie ist damit die eine Seite des Vertrags, die nicht raten
muss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from support.api import AUTH, api, api_settings, befuellen
from support.semantik import korpus
from wissensgraph.config.schema import Settings

pytestmark = pytest.mark.unit

#: Die Felder einer Kante, die §17.2 in ein sichtbares Merkmal übersetzt.
KANTENFELDER = {
    "id",
    "from_store",
    "from_id",
    "to_store",
    "to_id",
    "kind",
    "weight",
    "confidence",
    "reasoning",
    "resolved",
    "generated_by",
    "verified_by",
    "verified_at",
    "curated",
    "created_at",
}

#: Die Felder eines Knotens, die das Ranking sichtbar machen (§12.3).
KNOTENFELDER = {"id", "store", "scope", "type", "title", "status", "hops", "score", "density"}


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    return api_settings(
        {
            **minimal_config_dict,
            "clustering": {"neighbors_k": 4, "min_cluster_size": 3, "stability_runs": 1},
        }
    )


def _mit_graph(settings: Settings, tmp_path: Path) -> Any:
    """Ein Kontextmanager mit Konzepten, Embeddings, Clustern und damit auch Kanten."""
    return api(settings, tmp_path)


class TestTraversierung:
    def test_edges_ist_eine_liste_von_kanten_und_keine_zahl(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§16.2: "Knoten + Kanten + Scores" — eine Zahl ist keine Kante."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")
            cluster = next(
                concept.id
                for concept in runtime.catalog.concepts(store="shared", limit=200).items
                if concept.type == "Cluster"
            )

            payload = client.get(f"/api/v1/graph/neighbors/{cluster}", headers=AUTH).json()

            assert isinstance(payload["edges"], list)
            assert payload["edges"], "Ein Cluster ohne Kanten würde nichts prüfen."
            assert set(payload["edges"][0]) >= KANTENFELDER

    def test_die_kantenzahl_steht_daneben(self, settings: Settings, tmp_path: Path) -> None:
        """Die CLI zeigt sie in ihrer Zusammenfassung — sie darf nicht verloren gehen."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/graph/neighbors/confluence:100", headers=AUTH).json()

            assert payload["edge_count"] == len(payload["edges"])

    def test_knoten_tragen_ihre_bewertung(self, settings: Settings, tmp_path: Path) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.post(
                "/api/v1/graph/traverse", json={"start_id": "confluence:100"}, headers=AUTH
            ).json()

            assert set(payload["nodes"][0]) >= KNOTENFELDER

    def test_start_ist_eine_liste_von_zeichenketten(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.post(
                "/api/v1/graph/traverse", json={"start_id": "confluence:100"}, headers=AUTH
            ).json()

            assert payload["start"] == ["shared:confluence:100"]


class TestSuche:
    def test_treffer_stehen_unter_hits(self, settings: Settings, tmp_path: Path) -> None:
        """Die Oberfläche las lange ``nodes`` — und bekam nie ein Ergebnis zu sehen."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.post(
                "/api/v1/graph/search", json={"query": "Faktentabellen"}, headers=AUTH
            ).json()

            assert "hits" in payload
            assert "nodes" not in payload
            assert set(payload["hits"][0]) >= KNOTENFELDER


class TestKonzepte:
    def test_die_detailansicht_liefert_kanten_als_objekte(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.curation.add_edge(
                store="shared",
                from_id="confluence:100",
                to_id="confluence:200",
                actor="user:test",
            )

            payload = client.get("/api/v1/concepts/confluence:100", headers=AUTH).json()

            assert set(payload["outgoing"][0]) >= KANTENFELDER

    def test_ein_journaleintrag_sagt_ob_er_umkehrbar_ist(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Ohne dieses Feld müsste die Oberfläche die Regel aus §17.3 selbst kennen."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.curation.add_edge(
                store="shared",
                from_id="confluence:100",
                to_id="confluence:200",
                actor="user:test",
            )

            payload = client.get("/api/v1/concepts/confluence:100/history", headers=AUTH).json()

            assert {"id", "change_type", "actor", "undoable"} <= set(payload["items"][0])


class TestCluster:
    def test_die_uebersicht_traegt_die_mitgliederzahl(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")
            runtime.clusters.run(scope="engineering")

            payload = client.get("/api/v1/graph/overview", headers=AUTH).json()

            assert {"id", "title", "member_count"} <= set(payload["items"][0])


class TestKarte:
    """``/graph/map`` — die Übersicht ohne Startknoten (§17.2, Ansicht 1)."""

    def test_die_karte_liefert_knoten_und_kanten(self, settings: Settings, tmp_path: Path) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/graph/map", headers=AUTH).json()

            assert payload["nodes"], "Ein leerer Bestand würde nichts prüfen."
            assert isinstance(payload["edges"], list)
            assert payload["edge_count"] == len(payload["edges"])

    def test_ein_kartenknoten_traegt_seinen_grad_statt_einer_bewertung(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Eine Karte hat keinen Ausgangspunkt — ``hops`` und ``score`` wären erfunden."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            knoten = client.get("/api/v1/graph/map", headers=AUTH).json()["nodes"][0]

            assert "degree" in knoten
            assert "score" not in knoten
            assert "hops" not in knoten

    def test_die_deckelung_ist_der_antwort_anzusehen(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/graph/map", params={"limit": 1}, headers=AUTH).json()

            assert len(payload["nodes"]) == 1
            assert payload["truncated"] is True
            assert payload["next_cursor"] is not None

    def test_die_facetten_wirken_wie_im_dokumentenbrowser(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Derselbe Filter muss in beiden Ansichten denselben Bestand meinen."""
        with _mit_graph(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            karte = client.get(
                "/api/v1/graph/map", params={"type": "Confluence Page"}, headers=AUTH
            ).json()
            liste = client.get(
                "/api/v1/concepts", params={"type": "Confluence Page", "limit": 200}, headers=AUTH
            ).json()

            assert {knoten["id"] for knoten in karte["nodes"]} == {
                eintrag["id"] for eintrag in liste["items"]
            }
