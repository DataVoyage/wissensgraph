"""Tests der Store-Registry (§20.1, Anwendungsebene der Store-Trennung)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry, UnknownStoreError

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    # SQLite-DSNs, damit die Registry ohne laufende PostgreSQL-Instanz prüfbar ist. Die Logik
    # der Registry ist von der Datenbank unabhängig; das Zusammenspiel mit PostgreSQL prüfen
    # die Integrationstests.
    minimal_config_dict["stores"] = {
        "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
    }
    return Settings.model_validate(minimal_config_dict)


class TestStoreAufloesung:
    def test_kennt_konfigurierte_stores(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            assert set(registry.store_names) == {"shared", "personal"}

    def test_liefert_store_konfiguration(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            assert registry.config_of("personal").allow_remote is False

    def test_unbekannter_store_wirft(self, settings: Settings) -> None:
        # Kein Codepfad soll sich einen Store "ausdenken" können (§20.1).
        with StoreRegistry(settings) as registry, pytest.raises(UnknownStoreError) as excinfo:
            registry.engine("archiv")

        assert "archiv" in str(excinfo.value)
        assert "shared" in str(excinfo.value)

    def test_engine_wird_zwischengespeichert(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            assert registry.engine("shared") is registry.engine("shared")

    def test_stores_erhalten_getrennte_engines(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            assert registry.engine("shared") is not registry.engine("personal")

    def test_postgres_engine_erhaelt_connect_timeout(
        self, minimal_config_dict: dict[str, Any], mocker: Any
    ) -> None:
        # Ein /readyz, das auf den TCP-Timeout des Betriebssystems wartet, ist wertlos.
        erzeuge = mocker.patch("wissensgraph.infrastructure.db.registry.create_engine")
        settings = Settings.model_validate(minimal_config_dict)

        StoreRegistry(settings).engine("shared")

        optionen = erzeuge.call_args.kwargs
        assert optionen["connect_args"] == {"connect_timeout": 5}
        assert optionen["pool_size"] == 5

    def test_sqlite_engine_ohne_postgres_optionen(self, settings: Settings, mocker: Any) -> None:
        # SQLite kennt weder pool_size noch connect_timeout; die Optionen dürfen dort nicht
        # gesetzt werden, sonst scheitert schon das Anlegen der Engine.
        erzeuge = mocker.patch("wissensgraph.infrastructure.db.registry.create_engine")

        StoreRegistry(settings).engine("shared")

        assert "connect_args" not in erzeuge.call_args.kwargs
        assert "pool_size" not in erzeuge.call_args.kwargs

    def test_dispose_leert_den_zwischenspeicher(self, settings: Settings) -> None:
        registry = StoreRegistry(settings)
        erste = registry.engine("shared")

        registry.dispose()

        assert registry.engine("shared") is not erste


class TestHealthcheck:
    def test_erreichbarer_store_ist_healthy(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            health = registry.check("shared")

        assert health.healthy is True
        assert health.store == "shared"
        assert health.detail is None

    def test_prueft_alle_stores(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            ergebnisse = registry.check_all()

        assert {item.store for item in ergebnisse} == {"shared", "personal"}
        assert all(item.healthy for item in ergebnisse)

    def test_unerreichbarer_store_meldet_fehler_statt_zu_werfen(
        self, settings: Settings, mocker: Any
    ) -> None:
        # /readyz soll den Zustand melden, nicht selbst abstürzen.
        mocker.patch.object(
            StoreRegistry,
            "engine",
            side_effect=OperationalError("connection refused", None, Exception()),
        )

        with StoreRegistry(settings) as registry:
            health = registry.check("personal")

        assert health.healthy is False
        assert health.detail

    def test_dsn_wird_maskiert_ausgegeben(
        self, minimal_config_dict: dict[str, Any], mocker: Any
    ) -> None:
        # §20.2: Der DSN erscheint in /readyz und im Log — ohne Passwort.
        minimal_config_dict["stores"] = {
            "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
            "personal": {
                "dsn": "postgresql+psycopg://wg:sehr-geheim@db-personal:5432/wg",
                "allow_remote": False,
            },
        }
        settings = Settings.model_validate(minimal_config_dict)
        mocker.patch.object(
            StoreRegistry, "engine", side_effect=OperationalError("weg", None, Exception())
        )

        with StoreRegistry(settings) as registry:
            health = registry.check("personal")

        assert "sehr-geheim" not in health.dsn
        assert "sehr-geheim" not in str(health.as_dict())

    def test_as_dict_ist_serialisierbar(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            payload = registry.check("shared").as_dict()

        assert payload == {
            "store": "shared",
            "healthy": True,
            "dsn": "sqlite+pysqlite:///:memory:",
            "detail": None,
        }

    def test_fehlerdetail_wird_gekuerzt(self, settings: Settings, mocker: Any) -> None:
        langer_fehler = OperationalError("Zeile eins\n" + "x" * 500, None, Exception("ursprung"))
        mocker.patch.object(StoreRegistry, "engine", side_effect=langer_fehler)

        with StoreRegistry(settings) as registry:
            health = registry.check("shared")

        assert health.healthy is False
        assert health.detail is not None
        assert len(health.detail) <= 200
