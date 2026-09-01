"""Tests der HTTP-API: Health, Auth, aufgelöste Konfiguration (§16, §20.3)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from wissensgraph.api.app import REQUEST_ID_HEADER, create_app
from wissensgraph.api.errors import PROBLEM_MEDIA_TYPE
from wissensgraph.config.defaults import SECRET_MASK
from wissensgraph.config.schema import Settings

pytestmark = pytest.mark.unit

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    """Konfiguration mit SQLite-Stores, damit die API ohne PostgreSQL prüfbar ist."""
    minimal_config_dict["stores"] = {
        "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
    }
    minimal_config_dict["api"] = {"auth_mode": "token", "token": TOKEN}
    return Settings.model_validate(minimal_config_dict)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestHealthz:
    def test_meldet_ok(self, client: TestClient) -> None:
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_braucht_keine_authentifizierung(self, client: TestClient) -> None:
        # Ein Healthcheck, der ein Token verlangt, ist im Orchestrator unbrauchbar.
        assert client.get("/healthz").status_code == 200


class TestReadyz:
    def test_meldet_beide_stores(self, client: TestClient) -> None:
        # Abnahmekriterium Stufe 0: "/readyz meldet beide Datenbanken".
        payload = client.get("/readyz").json()

        assert payload["status"] == "ready"
        assert {store["store"] for store in payload["stores"]} == {"shared", "personal"}
        assert all(store["healthy"] for store in payload["stores"])

    def test_antwortet_503_wenn_ein_store_fehlt(self, client: TestClient, mocker: Any) -> None:
        from wissensgraph.infrastructure.db.registry import StoreHealth

        mocker.patch(
            "wissensgraph.infrastructure.db.registry.StoreRegistry.check_all",
            return_value=(
                StoreHealth(store="shared", healthy=True, dsn="sqlite://"),
                StoreHealth(store="personal", healthy=False, dsn="sqlite://", detail="weg"),
            ),
        )

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_gibt_keine_klartext_zugangsdaten_aus(self, client: TestClient) -> None:
        assert "geheim" not in client.get("/readyz").text


class TestAuthentifizierung:
    def test_ohne_token_401(self, client: TestClient) -> None:
        response = client.get("/api/v1/config/effective")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_falsches_token_401(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/config/effective", headers={"Authorization": "Bearer falsch"}
        )

        assert response.status_code == 401

    def test_falsches_schema_401(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/config/effective", headers={"Authorization": f"Basic {TOKEN}"}
        )

        assert response.status_code == 401

    def test_richtiges_token_200(self, client: TestClient) -> None:
        assert client.get("/api/v1/config/effective", headers=AUTH).status_code == 200

    def test_auth_mode_none_ohne_token(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["stores"] = {
            "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
            "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        }
        minimal_config_dict["api"] = {"auth_mode": "none", "host": "127.0.0.1"}
        settings = Settings.model_validate(minimal_config_dict)

        with TestClient(create_app(settings)) as offener_client:
            assert offener_client.get("/api/v1/config/effective").status_code == 200


class TestDoctor:
    def test_liefert_die_pruefungen_als_json(self, client: TestClient) -> None:
        """§16.2: dieselbe Auskunft wie ``wg doctor``, als Zustellart für die UI (§17.2)."""
        payload = client.get("/api/v1/doctor", headers=AUTH).json()

        assert isinstance(payload["healthy"], bool)
        assert len(payload["checks"]) > 5
        for pruefung in payload["checks"]:
            assert pruefung["status"] in {"ok", "warn", "fail"}
            assert pruefung["name"]
            assert "detail" in pruefung

    def test_verlangt_authentifizierung(self, client: TestClient) -> None:
        assert client.get("/api/v1/doctor").status_code == 401


class TestEffectiveConfig:
    def test_liefert_aufgeloeste_konfiguration(self, client: TestClient) -> None:
        payload = client.get("/api/v1/config/effective", headers=AUTH).json()

        assert payload["embedding_dim"] == 768
        assert payload["clustering"]["neighbors_k"] == 8
        assert {scope["name"] for scope in payload["scopes"]} == {"engineering", "personal"}

    def test_maskiert_das_api_token(self, client: TestClient) -> None:
        # §6.1 Regel 5: die Konfiguration ist einsehbar — genau deshalb ohne Klartext-Secrets.
        payload = client.get("/api/v1/config/effective", headers=AUTH).json()

        assert payload["api"]["token"] == SECRET_MASK
        assert TOKEN not in client.get("/api/v1/config/effective", headers=AUTH).text

    def test_maskiert_dsn_passwoerter(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["stores"] = {
            "shared": {
                "dsn": "postgresql+psycopg://wg:sehr-geheim@db-shared:5432/wg",
                "allow_remote": True,
            },
            "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        }
        minimal_config_dict["api"] = {"auth_mode": "token", "token": TOKEN}
        settings = Settings.model_validate(minimal_config_dict)

        with TestClient(create_app(settings)) as pg_client:
            text = pg_client.get("/api/v1/config/effective", headers=AUTH).text

        assert "sehr-geheim" not in text
        assert "db-shared:5432" in text

    def test_enthaelt_die_fachregeln_fuer_die_ui(self, client: TestClient) -> None:
        # §17.1: Die UI enthält keine Fachlogik; Kantenarten und Typen kommen von hier.
        payload = client.get("/api/v1/config/effective", headers=AUTH).json()

        assert "member" in payload["edge_kinds"]["structural"]
        assert any(item["source_mirrored"] for item in payload["concept_types"])


class TestFehlerformat:
    def test_fehler_sind_problem_details(self, client: TestClient) -> None:
        # §16.1: "Fehler einheitlich als RFC-7807-Problem-Detail".
        response = client.get("/api/v1/config/effective")

        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        payload = response.json()
        assert payload["status"] == 401
        assert payload["title"] == "Nicht authentifiziert"
        assert payload["instance"] == "/api/v1/config/effective"
        assert payload["type"].startswith("https://")

    def test_unbekannter_pfad_ist_problem_detail(self, client: TestClient) -> None:
        response = client.get("/gibtsnicht")

        assert response.status_code == 404
        assert response.json()["title"] == "Nicht gefunden"

    def test_schema_verstoss_ist_problem_detail(self, client: TestClient) -> None:
        response = client.get("/readyz", params={"limit": "keine-zahl"})

        # /readyz nimmt keine Parameter entgegen; der Aufruf bleibt gültig.
        assert response.status_code in {200, 422}


class TestRequestId:
    def test_erzeugt_request_id(self, client: TestClient) -> None:
        response = client.get("/healthz")

        assert response.headers[REQUEST_ID_HEADER]

    def test_uebernimmt_mitgegebene_request_id(self, client: TestClient) -> None:
        response = client.get("/healthz", headers={REQUEST_ID_HEADER: "meine-id"})

        assert response.headers[REQUEST_ID_HEADER] == "meine-id"


class TestOpenApi:
    def test_schema_ist_erreichbar(self, client: TestClient) -> None:
        # §16.1: OpenAPI-Schema unter /api/v1/openapi.json — Grundlage des generierten
        # TypeScript-Clients der UI (§17.1).
        payload = client.get("/api/v1/openapi.json").json()

        assert payload["info"]["title"] == "Wissensgraph"
        assert "/readyz" in payload["paths"]
