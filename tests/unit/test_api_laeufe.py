"""Läufe, Fortschritt und Modellnutzung über HTTP (§16.2, §16.3).

Der Schwerpunkt liegt auf dem Versprechen aus §16.3: Ein angestoßener Lauf antwortet sofort mit
einer ID, und der Zustand liegt in ``runs`` — nicht in der Warteschlange. Nur deshalb kann die
Oberfläche einen Lauf verfolgen, den der Worker noch gar nicht entnommen hat.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from support.api import AUTH, api, api_settings, befuellen
from support.semantik import korpus
from wissensgraph.config.schema import Settings
from wissensgraph.domain.runs import RunStatus

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    return api_settings(minimal_config_dict)


class TestAnstossen:
    @pytest.mark.parametrize(
        ("pfad", "koerper"),
        [
            ("/api/v1/runs/embed", {"scope": "engineering"}),
            ("/api/v1/runs/cluster", {"scope": "engineering"}),
            ("/api/v1/runs/relations", {"scope": "engineering"}),
            ("/api/v1/runs/link-orphans", {"scope": "engineering"}),
        ],
    )
    def test_antwortet_202_mit_location(
        self, settings: Settings, tmp_path: Path, pfad: str, koerper: dict[str, Any]
    ) -> None:
        """§16.3: "antworten mit 202 Accepted samt Run-ID und Location-Header"."""
        with api(settings, tmp_path) as (client, _):
            antwort = client.post(pfad, json=koerper, headers=AUTH)

            assert antwort.status_code == 202
            run_id = antwort.json()["id"]
            assert antwort.headers["Location"] == f"/api/v1/runs/{run_id}"
            assert antwort.json()["status"] == RunStatus.QUEUED

    def test_der_lauf_ist_sofort_abrufbar(self, settings: Settings, tmp_path: Path) -> None:
        """Der Zustand liegt in ``runs``, nicht in der Queue — deshalb geht das (§16.3)."""
        with api(settings, tmp_path) as (client, _):
            run_id = client.post(
                "/api/v1/runs/embed", json={"scope": "engineering"}, headers=AUTH
            ).json()["id"]

            payload = client.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()

            assert payload["id"] == run_id
            assert payload["store"] == "shared"

    def test_unbekannter_scope_ist_400(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.post(
                "/api/v1/runs/cluster", json={"scope": "gibtsnicht"}, headers=AUTH
            )

            assert antwort.status_code == 400

    def test_unbekannte_quelle_ist_404(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            antwort = client.post("/api/v1/runs/sync", json={"source": "gibtsnicht"}, headers=AUTH)

            assert antwort.status_code == 404

    def test_die_parameter_aus_paragraf_154_kommen_mit(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§16.2: "alle Parameter aus §15.4 als Body-Felder"."""
        with api(settings, tmp_path) as (client, _):
            payload = client.post(
                "/api/v1/runs/link-orphans",
                json={"scope": "engineering", "use_llm": False, "loose_threshold": 3},
                headers=AUTH,
            ).json()

            assert payload["params"]["use_llm"] is False
            assert payload["params"]["loose_threshold"] == 3


class TestWorkerUebernimmt:
    def test_der_worker_arbeitet_denselben_lauf_ab(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Kein zweiter Lauf: Die ID, die die API genannt hat, ist die, die arbeitet (§16.3)."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            run_id = client.post(
                "/api/v1/runs/embed", json={"scope": "engineering"}, headers=AUTH
            ).json()["id"]

            runtime.work(once=True)

            payload = client.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()
            assert payload["status"] == RunStatus.SUCCEEDED
            assert payload["stats"]["embedded"] == 14
            assert len(client.get("/api/v1/runs", headers=AUTH).json()["items"]) == 1

    def test_ein_abgebrochener_lauf_startet_nicht_mehr(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            run_id = client.post(
                "/api/v1/runs/embed", json={"scope": "engineering"}, headers=AUTH
            ).json()["id"]
            client.post(f"/api/v1/runs/{run_id}/cancel", headers=AUTH)

            runtime.work(once=True)

            payload = client.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()
            assert payload["status"] == RunStatus.CANCELLED
            assert payload["stats"] == {}


class TestAbbruch:
    def test_ein_abgeschlossener_lauf_ist_409(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            run_id = client.post(
                "/api/v1/runs/embed", json={"scope": "engineering"}, headers=AUTH
            ).json()["id"]
            runtime.work(once=True)

            assert client.post(f"/api/v1/runs/{run_id}/cancel", headers=AUTH).status_code == 409

    def test_unbekannter_lauf_ist_404(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            unbekannt = "00000000-0000-4000-8000-000000000000"

            assert client.get(f"/api/v1/runs/{unbekannt}", headers=AUTH).status_code == 404


class TestFortschrittsstrom:
    def test_ein_beendeter_lauf_liefert_sofort_done(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§24: "der Fortschritt live angezeigt" — der Strom endet mit dem Lauf."""
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            run_id = client.post(
                "/api/v1/runs/embed", json={"scope": "engineering"}, headers=AUTH
            ).json()["id"]
            runtime.work(once=True)

            with client.stream("GET", f"/api/v1/runs/{run_id}/events", headers=AUTH) as antwort:
                assert antwort.headers["content-type"].startswith("text/event-stream")
                text = "".join(antwort.iter_text())

            ereignisse = _ereignisse(text)
            assert [name for name, _ in ereignisse] == ["progress", "done"]
            assert ereignisse[-1][1]["status"] == RunStatus.SUCCEEDED

    def test_ein_unbekannter_lauf_meldet_einen_fehler_im_strom(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, _):
            unbekannt = "00000000-0000-4000-8000-000000000000"

            with client.stream("GET", f"/api/v1/runs/{unbekannt}/events", headers=AUTH) as antwort:
                text = "".join(antwort.iter_text())

            assert _ereignisse(text)[0][0] == "error"


class TestQuellenUndModelle:
    def test_quellen_melden_ihren_zustand(self, settings: Settings, tmp_path: Path) -> None:
        with api(settings, tmp_path) as (client, _):
            payload = client.get("/api/v1/sources", headers=AUTH).json()

            assert isinstance(payload["items"], list)

    def test_modelle_nennen_route_und_policy_ohne_einen_aufruf(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """§11.1: Ein Modellwechsel muss sich prüfen lassen, ohne etwas hinauszuschicken."""
        with api(settings, tmp_path) as (client, runtime):
            payload = client.get("/api/v1/models", headers=AUTH).json()

            aufgaben = {eintrag["task"]: eintrag for eintrag in payload["tasks"]}
            assert aufgaben["embedding"]["model_key"] == "p:m"
            assert payload["policies"]["personal"] == ["p"]
            assert runtime.catalog.usage(store="shared") == ()

    def test_die_nutzung_zaehlt_die_aufrufe_eines_laufs(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        with api(settings, tmp_path) as (client, runtime):
            befuellen(runtime, korpus())
            runtime.embeddings.run(scope="engineering")

            payload = client.get("/api/v1/models/usage", headers=AUTH).json()

            assert payload["items"]
            assert sum(item["calls"] for item in payload["items"]) > 0


def _ereignisse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Zerlegt einen SSE-Strom in Paare aus Ereignisname und Nutzlast."""
    ergebnis: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        zeilen = dict(zeile.split(": ", 1) for zeile in block.splitlines() if ": " in zeile)
        if "event" in zeilen and "data" in zeilen:
            ergebnis.append((zeilen["event"], json.loads(zeilen["data"])))
    return ergebnis
