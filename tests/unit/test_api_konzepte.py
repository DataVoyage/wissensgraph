"""Konzepte, Suche und Graph über HTTP (§16.2, §17.2).

Geprüft wird der ganze Weg: HTTP → Router → Katalogdienst → speicherresidenter Store. Was hier
grün ist, ist damit nicht nur die Route, sondern auch die Frage, ob der Dienst dahinter das
liefert, was §16.2 an dieser Stelle verspricht.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from support.api import AUTH, api, api_settings, befuellen
from support.semantik import konzept, korpus
from wissensgraph.config.schema import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    return api_settings(minimal_config_dict)


class TestListe:
    def test_liefert_eine_seite_mit_cursor(self, settings: Settings, tmp_path: Path) -> None:
        """§16.1: "Paginierung durchgängig cursor-basiert"."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            erste = client.get("/api/v1/concepts?limit=5", headers=AUTH).json()

            assert len(erste["items"]) == 5
            assert erste["next_cursor"] is not None
            zweite = client.get(
                f"/api/v1/concepts?limit=5&cursor={erste['next_cursor']}", headers=AUTH
            ).json()
            assert {item["id"] for item in erste["items"]} & {
                item["id"] for item in zweite["items"]
            } == set()

    def test_die_letzte_seite_hat_keinen_cursor(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/concepts?limit=100", headers=AUTH).json()

            assert payload["next_cursor"] is None

    def test_filtert_nach_typ(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/concepts?type=Confluence%20Page", headers=AUTH).json()

            assert payload["items"]
            assert {item["type"] for item in payload["items"]} == {"Confluence Page"}

    def test_liste_enthaelt_keinen_body(self, settings: Settings, tmp_path: Path) -> None:
        """Zweihundert Fließtexte für eine Tabelle, die keinen anzeigt, wären reine Last."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, [konzept("confluence:900", title="Mit Text", body="langer Text")])

            payload = client.get("/api/v1/concepts", headers=AUTH).json()

            assert "body" not in payload["items"][0]

    def test_unbekannter_store_ist_400(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.get("/api/v1/concepts?store=gibtsnicht", headers=AUTH)

            assert antwort.status_code == 400
            assert "gibtsnicht" in antwort.json()["detail"]


class TestDetail:
    def test_liefert_kanten_und_body(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, [konzept("confluence:901", title="Seite", body="Fließtext")])

            payload = client.get("/api/v1/concepts/confluence:901", headers=AUTH).json()

            assert payload["body"] == "Fließtext"
            assert payload["outgoing"] == []
            assert payload["incoming"] == []

    def test_gespiegelte_inhaltsfelder_sind_sichtbar_gesperrt(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§17.3: "sichtbar gesperrt, nicht nur schreibgeschützt"."""
        with api(settings, tmp_path) as (client, runtime):
            aus_quelle = konzept("confluence:902", title="Aus Confluence").model_copy(
                update={"source_name": "confluence-eng", "external_id": "902"}
            )
            befuellen(runtime, [aus_quelle])

            payload = client.get("/api/v1/concepts/confluence:902", headers=AUTH).json()

            assert set(payload["locked_fields"]) == {"title", "description", "body", "resource"}

    def test_lokales_konzept_hat_keine_sperren(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(
                runtime,
                [
                    konzept(
                        "note:frei",
                        title="Eigene Notiz",
                        scope="personal",
                        store="personal",
                        concept_type="Note",
                    )
                ],
                store="personal",
            )

            payload = client.get("/api/v1/concepts/note:frei?store=personal", headers=AUTH).json()

            assert payload["locked_fields"] == []

    def test_unbekanntes_konzept_ist_404(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            assert client.get("/api/v1/concepts/confluence:0", headers=AUTH).status_code == 404


class TestAnlegenUndAendern:
    def test_legt_eine_notiz_im_persoenlichen_store_an(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.post(
                "/api/v1/concepts",
                json={"scope": "personal", "type": "Note", "title": "Neue Notiz"},
                headers=AUTH,
            )

            assert antwort.status_code == 201
            assert antwort.headers["Location"].startswith("/api/v1/concepts/note:")
            assert antwort.json()["concept"]["curated"] is True

    def test_im_geteilten_store_anlegen_ist_400(self, settings: Settings, tmp_path: Path) -> None:
        """§17.4: Der geteilte Store bekommt seine Inhalte aus den Quellen."""
        with api(settings, tmp_path) as (client, _):
            antwort = client.post(
                "/api/v1/concepts",
                json={"scope": "engineering", "type": "Confluence Page", "title": "Von Hand"},
                headers=AUTH,
            )

            assert antwort.status_code == 400
            assert "personal" in antwort.json()["detail"]

    def test_aendert_den_status_eines_gespiegelten_konzepts(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§17.4: ``status`` gehört dem Menschen, auch an gespiegelten Inhalten."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(
                runtime,
                [
                    konzept("confluence:903", title="Alt").model_copy(
                        update={"source_name": "confluence-eng", "external_id": "903"}
                    )
                ],
            )

            antwort = client.patch(
                "/api/v1/concepts/confluence:903", json={"status": "deprecated"}, headers=AUTH
            )

            assert antwort.status_code == 200
            assert antwort.json()["concept"]["status"] == "deprecated"

    def test_gesperrtes_feld_ist_409_mit_begruendung(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(
                runtime,
                [
                    konzept("confluence:904", title="Alt").model_copy(
                        update={"source_name": "confluence-eng", "external_id": "904"}
                    )
                ],
            )

            antwort = client.patch(
                "/api/v1/concepts/confluence:904", json={"title": "Neu"}, headers=AUTH
            )

            assert antwort.status_code == 409
            assert "confluence-eng" in antwort.json()["detail"]

    def test_eine_aenderung_steht_im_journal(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, [konzept("confluence:905", title="Alt")])
            client.patch(
                "/api/v1/concepts/confluence:905", json={"status": "deprecated"}, headers=AUTH
            )

            payload = client.get("/api/v1/concepts/confluence:905/history", headers=AUTH).json()

            assert payload["items"][0]["change_type"] == "status_changed"
            assert payload["items"][0]["actor"] == "user:token"


class TestStatistik:
    def test_zaehlt_je_store_und_scope(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/stats", headers=AUTH).json()

            geteilt = next(item for item in payload["stores"] if item["store"] == "shared")
            assert geteilt["concepts"] == 14
            assert geteilt["by_scope"]["engineering"] == 14


class TestGraph:
    def test_suche_nennt_ihren_modus(self, settings: Settings, tmp_path: Path) -> None:
        """§12.4: Der Modus steht im Ergebnis und nicht in einer Fußnote."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.post(
                "/api/v1/graph/search", json={"query": "Warehouse"}, headers=AUTH
            ).json()

            assert payload["mode"] in {"lexical", "cluster", "hybrid"}

    def test_traversierung_ueber_einen_unbekannten_start_ist_404(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.post(
                "/api/v1/graph/traverse", json={"start_id": "confluence:0"}, headers=AUTH
            )

            assert antwort.status_code == 404

    def test_nachbarn_liefern_genau_einen_hop(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/graph/neighbors/confluence:100", headers=AUTH).json()

            assert payload["hops"] == 1

    def test_lose_knoten_nennen_ihre_schwelle(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())

            payload = client.get("/api/v1/graph/loose", headers=AUTH).json()

            assert payload["threshold"] == settings.orphans.loose_threshold
            assert any(item["id"] == "note:isoliert" for item in payload["items"])


class TestAbsicherung:
    def test_ohne_token_kein_zugriff(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            assert client.get("/api/v1/concepts").status_code == 401

    def test_das_openapi_schema_kennt_die_neuen_endpunkte(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Aus ihm entsteht der TypeScript-Client (§24, Stufe 11)."""
        with api(settings, tmp_path) as (client, _):
            pfade = client.get("/api/v1/openapi.json").json()["paths"]

            for pfad in (
                "/api/v1/concepts",
                "/api/v1/graph/traverse",
                "/api/v1/graph/search",
                "/api/v1/curation/queue",
                "/api/v1/clusters",
                "/api/v1/runs/sync",
                "/api/v1/models",
            ):
                assert pfad in pfade, pfad
