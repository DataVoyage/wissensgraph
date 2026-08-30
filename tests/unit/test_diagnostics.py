"""Tests der Selbstprüfung, die ``wg doctor`` zugrunde liegt (§19)."""

from __future__ import annotations

from typing import Any

import pytest

from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import (
    CheckStatus,
    check_api_exposure,
    check_configuration,
    check_model_policy,
    check_personal_locality,
    check_stores,
    run_diagnostics,
)
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.registry import StoreHealth

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    minimal_config_dict["stores"] = {
        "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
    }
    return Settings.model_validate(minimal_config_dict)


class TestCheckConfiguration:
    def test_meldet_ok_mit_kennzahlen(self, settings: Settings) -> None:
        result = check_configuration(settings)

        assert result.status is CheckStatus.OK
        assert "Scopes" in result.detail
        assert result.context["embedding_dim"] == 768


class TestCheckPersonalLocality:
    def test_lokaler_personal_store_ist_ok(self, settings: Settings) -> None:
        assert check_personal_locality(settings).status is CheckStatus.OK

    def test_entfernter_personal_store_warnt(self, minimal_config_dict: dict[str, Any]) -> None:
        # allow_remote=true schaltet Leitprinzip 2 bewusst ab — das soll sichtbar sein.
        minimal_config_dict["stores"]["personal"] = {
            "dsn": "postgresql://wg@db.example.com:5432/wg",
            "allow_remote": True,
        }
        settings = Settings.model_validate(minimal_config_dict)

        result = check_personal_locality(settings)

        assert result.status is CheckStatus.WARN
        assert "entfernten Host" in result.detail

    def test_maskiert_den_dsn(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["stores"]["personal"] = {
            "dsn": "postgresql://wg:sehr-geheim@db-personal:5432/wg",
            "allow_remote": False,
        }
        settings = Settings.model_validate(minimal_config_dict)

        result = check_personal_locality(settings)

        assert "sehr-geheim" not in str(result.context)

    def test_fehlender_personal_store_warnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["stores"] = {
            "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False}
        }
        minimal_config_dict["scopes"] = [{"name": "engineering", "store": "shared"}]
        minimal_config_dict["concept_types"] = [{"name": "Cluster", "stores": ["shared"]}]
        settings = Settings.model_validate(minimal_config_dict)

        assert check_personal_locality(settings).status is CheckStatus.WARN


class TestCheckModelPolicy:
    def test_default_ist_ok(self, settings: Settings) -> None:
        assert check_model_policy(settings).status is CheckStatus.OK

    def test_freigabe_warnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["personal_allow_remote_models"] = True
        settings = Settings.model_validate(minimal_config_dict)

        result = check_model_policy(settings)

        assert result.status is CheckStatus.WARN
        assert "WG_PERSONAL_ALLOW_REMOTE_MODELS" in result.detail


class TestCheckApiExposure:
    def test_token_modus_ist_ok(self, settings: Settings) -> None:
        assert check_api_exposure(settings).status is CheckStatus.OK

    def test_auth_none_warnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["api"] = {"auth_mode": "none", "host": "127.0.0.1"}
        settings = Settings.model_validate(minimal_config_dict)

        assert check_api_exposure(settings).status is CheckStatus.WARN


class TestCheckStores:
    def test_erreichbare_stores_sind_ok(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            results = check_stores(registry)

        assert {result.name for result in results} == {"store:shared", "store:personal"}
        assert all(result.status is CheckStatus.OK for result in results)

    def test_unerreichbarer_store_ist_fail(self, settings: Settings, mocker: Any) -> None:
        mocker.patch.object(
            StoreRegistry,
            "check_all",
            return_value=(StoreHealth("shared", False, "sqlite://", "weg"),),
        )

        with StoreRegistry(settings) as registry:
            results = check_stores(registry)

        assert results[0].status is CheckStatus.FAIL
        assert "weg" in results[0].detail


class TestRunDiagnostics:
    def test_bericht_ist_gesund(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            report = run_diagnostics(settings, registry)

        assert report.healthy is True
        assert report.exit_code == 0

    def test_warnung_bricht_den_bericht_nicht(self, minimal_config_dict: dict[str, Any]) -> None:
        # Eine Warnung ist ein Hinweis, kein Fehler — sonst wäre 'wg doctor' in CI unbrauchbar,
        # sobald jemand bewusst auth_mode=none nutzt.
        minimal_config_dict["stores"] = {
            "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
            "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        }
        minimal_config_dict["api"] = {"auth_mode": "none", "host": "127.0.0.1"}
        settings = Settings.model_validate(minimal_config_dict)

        with StoreRegistry(settings) as registry:
            report = run_diagnostics(settings, registry)

        assert report.healthy is True
        assert any(result.status is CheckStatus.WARN for result in report.results)

    def test_fehlender_store_macht_bericht_ungesund(self, settings: Settings, mocker: Any) -> None:
        mocker.patch.object(
            StoreRegistry,
            "check_all",
            return_value=(StoreHealth("personal", False, "sqlite://", "weg"),),
        )

        with StoreRegistry(settings) as registry:
            report = run_diagnostics(settings, registry)

        assert report.healthy is False
        assert report.exit_code == 1

    def test_as_dict_ist_serialisierbar(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            payload = run_diagnostics(settings, registry).as_dict()

        assert payload["healthy"] is True
        assert isinstance(payload["checks"], list)
        assert all("status" in check for check in payload["checks"])
