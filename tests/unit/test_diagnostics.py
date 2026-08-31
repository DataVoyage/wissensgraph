"""Tests der Selbstprüfung, die ``wg doctor`` zugrunde liegt (§19)."""

from __future__ import annotations

from typing import Any

import pytest

from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import (
    CheckStatus,
    check_api_exposure,
    check_broker,
    check_configuration,
    check_model_policy,
    check_personal_locality,
    check_schema,
    check_sources,
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


class TestCheckSchema:
    """Die Schemaprüfung aus §7.4 / §11.7 — beantwortet zwei getrennte Fragen."""

    def test_ohne_postgresql_wird_die_pruefung_als_uebersprungen_gemeldet(
        self, settings: Settings
    ) -> None:
        """Eine Warnung statt eines Fehlers: 'nicht geprüft' ist nicht dasselbe wie 'kaputt'."""
        with StoreRegistry(settings) as registry:
            results = check_schema(settings, registry)

        assert {result.name for result in results} == {"schema:shared", "schema:personal"}
        assert all(result.status is CheckStatus.WARN for result in results)
        assert all("übersprungen" in result.detail for result in results)

    def test_nicht_erreichbarer_store_wird_als_fehler_gemeldet(
        self, settings: Settings, mocker: Any
    ) -> None:
        from sqlalchemy.exc import OperationalError

        mocker.patch.object(
            StoreRegistry, "engine", side_effect=OperationalError("SELECT 1", {}, Exception("weg"))
        )

        with StoreRegistry(settings) as registry:
            results = check_schema(settings, registry)

        assert all(result.status is CheckStatus.FAIL for result in results)
        assert all("nicht feststellbar" in result.detail for result in results)


class TestBrokerpruefung:
    """``check_broker`` — der Queue-Teil von ``wg doctor`` (§5.1, §16.3)."""

    def test_ohne_broker_gibt_es_eine_warnung(self, settings: Settings) -> None:
        """Kein Fehler: Im Profil 'minimal' läuft gar kein Broker (§5.4)."""
        ergebnis = check_broker(settings)

        assert ergebnis.name == "broker"
        assert ergebnis.status is CheckStatus.WARN
        assert "WG_BROKER_URL" in ergebnis.detail

    def test_ein_unerreichbarer_broker_warnt_ebenfalls(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """Ohne Broker fällt nur das Asynchrone aus; 'wg sync' läuft unverändert."""
        # Port 1 ist reserviert und nimmt keine Verbindungen an.
        minimal_config_dict["broker_url"] = "redis://127.0.0.1:1/0"
        settings = Settings.model_validate(minimal_config_dict)

        ergebnis = check_broker(settings)

        assert ergebnis.status is CheckStatus.WARN
        assert "nicht erreichbar" in ergebnis.detail

    def test_ein_erreichbarer_broker_meldet_die_warteschlange(
        self, minimal_config_dict: dict[str, Any], mocker: Any
    ) -> None:
        from wissensgraph.infrastructure.queue import RedisJobQueue

        minimal_config_dict["broker_url"] = "redis://broker:6379/0"
        settings = Settings.model_validate(minimal_config_dict)
        mocker.patch.object(RedisJobQueue, "__init__", return_value=None)
        mocker.patch.object(RedisJobQueue, "size", return_value=3)
        mocker.patch.object(RedisJobQueue, "close", return_value=None)

        ergebnis = check_broker(settings)

        assert ergebnis.status is CheckStatus.OK
        assert ergebnis.context["pending"] == 3


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

    def test_bericht_enthaelt_die_schemapruefung(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            report = run_diagnostics(settings, registry)

        assert {"schema:shared", "schema:personal"} <= {r.name for r in report.results}

    def test_as_dict_ist_serialisierbar(self, settings: Settings) -> None:
        with StoreRegistry(settings) as registry:
            payload = run_diagnostics(settings, registry).as_dict()

        assert payload["healthy"] is True
        assert isinstance(payload["checks"], list)
        assert all("status" in check for check in payload["checks"])


class TestQuellenpruefung:
    """``check_sources`` — der Adapter-Teil von ``wg doctor`` (§19, §8.3)."""

    def test_ohne_konfigurierte_quellen_gibt_es_eine_warnung(self, settings: Settings) -> None:
        """Zulässig (Profil 'minimal'), aber nichts, was man versehentlich haben will."""
        (ergebnis,) = check_sources(settings)

        assert ergebnis.name == "quellen"
        assert ergebnis.status is CheckStatus.WARN
        assert ergebnis.ok

    def test_eine_gesunde_quelle_erscheint_einzeln(self, settings: Settings, tmp_path: Any) -> None:
        pfad = tmp_path / "sources.yaml"
        pfad.write_text(
            "sources:\n"
            "  - name: dummy\n"
            "    adapter: dummy\n"
            '    class: "support.dummy_adapter:DummyAdapter"\n'
            "    id_prefix: dummy\n"
            "    target:\n"
            "      scope: engineering\n"
            "      default_type: Confluence Page\n",
            encoding="utf-8",
        )
        (ergebnis,) = check_sources(settings, path=pfad)

        assert ergebnis.name == "quelle:dummy"
        assert ergebnis.status is CheckStatus.OK

    def test_eine_fehlerhafte_quellkonfiguration_ist_ein_fehler(
        self, settings: Settings, tmp_path: Any
    ) -> None:
        """§6.5: Ein unauffindbarer Adapter ist ein Konfigurationsfehler, kein Betriebszustand."""
        pfad = tmp_path / "sources.yaml"
        pfad.write_text(
            "sources:\n"
            "  - name: q\n"
            "    adapter: gibtesnicht\n"
            "    id_prefix: q\n"
            "    target:\n"
            "      scope: engineering\n"
            "      default_type: Confluence Page\n",
            encoding="utf-8",
        )
        (ergebnis,) = check_sources(settings, path=pfad)

        assert ergebnis.status is CheckStatus.FAIL
        assert not ergebnis.ok


class TestAgentReadonly:
    """§18.3, §20.1 Guard 5 — als Betriebsprüfung."""

    def test_warnt_ausserhalb_von_postgresql(self, settings: Settings) -> None:
        """Auf SQLite gibt es die Zusicherung nicht — und das zu verschweigen wäre schlimmer."""
        from wissensgraph.diagnostics import check_agent_readonly

        with StoreRegistry(settings) as registry:
            ergebnis = check_agent_readonly(registry)

        assert ergebnis.status is CheckStatus.WARN
        assert "PostgreSQL" in ergebnis.detail

    def test_meldet_einen_unerreichbaren_store_als_warnung(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        from wissensgraph.diagnostics import check_agent_readonly

        unerreichbar = Settings.model_validate(
            {
                **minimal_config_dict,
                "stores": {
                    "shared": {
                        "dsn": "postgresql+psycopg://wg:wg@127.0.0.1:1/wg",
                        "allow_remote": True,
                    },
                    "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
                },
            }
        )
        with StoreRegistry(unerreichbar) as registry:
            ergebnis = check_agent_readonly(registry)

        assert ergebnis.status is CheckStatus.WARN
        assert "Nicht prüfbar" in ergebnis.detail


class TestVertexpruefung:
    """§11.4: Was sich am Vertex-Anbieter ohne Netz feststellen lässt — und was nicht.

    Ausgegeben wird der aufgelöste Endpunkt. Das ist der eigentliche Zweck dieser Prüfung: Ein
    Tippfehler im Standort erzeugt keinen Fehler, sondern einen anderen Ort der Verarbeitung, und
    ``eu``, ``europe-west4`` und ``global`` sind alle drei gültige Angaben.
    """

    @staticmethod
    def _models_datei(tmp_path: Any, **vertex: Any) -> Any:
        import yaml

        datei = tmp_path / "models.yaml"
        inhalt = {
            "providers": {"vertex": {"type": "vertex", **vertex}},
            "tasks": {
                "cluster_labeling": {
                    "primary": {"provider": "vertex", "model": "gemini-3.5-flash-lite"}
                }
            },
        }
        datei.write_text(yaml.safe_dump(inhalt), encoding="utf-8")
        return datei

    def _vertex(self, ergebnisse: Any) -> Any:
        return next(item for item in ergebnisse if item.name.startswith("vertex:"))

    def test_der_aufgeloeste_endpunkt_steht_im_bericht(
        self, settings: Settings, tmp_path: Any
    ) -> None:
        from wissensgraph.diagnostics import check_models

        datei = self._models_datei(tmp_path, project="mein-projekt", location="eu")

        ergebnis = self._vertex(check_models(settings, path=datei))

        assert ergebnis.status is CheckStatus.OK
        assert ergebnis.context["endpoint"] == "aiplatform.eu.rep.googleapis.com"
        assert "Standard-Anmeldung" in ergebnis.detail

    def test_ein_fehlender_standort_warnt(self, settings: Settings, tmp_path: Any) -> None:
        from wissensgraph.diagnostics import check_models

        datei = self._models_datei(tmp_path, project="mein-projekt")

        ergebnis = self._vertex(check_models(settings, path=datei))

        assert ergebnis.status is CheckStatus.WARN
        assert "WG_PROVIDER_VERTEX__LOCATION" in ergebnis.detail

    def test_ein_fehlender_schluessel_ist_ein_fehler(
        self, settings: Settings, tmp_path: Any
    ) -> None:
        """Anders als eine fehlende Angabe: Hier wurde ein Pfad genannt, und er stimmt nicht."""
        from wissensgraph.diagnostics import check_models

        datei = self._models_datei(
            tmp_path,
            project="mein-projekt",
            location="eu",
            credentials_file=str(tmp_path / "gibtsnicht.json"),
        )

        ergebnis = self._vertex(check_models(settings, path=datei))

        assert ergebnis.status is CheckStatus.FAIL
        assert "/app/secrets" in ergebnis.detail

    def test_ein_vorhandener_schluessel_wird_als_anmeldung_genannt(
        self, settings: Settings, tmp_path: Any
    ) -> None:
        from wissensgraph.diagnostics import check_models

        schluessel = tmp_path / "sa.json"
        schluessel.write_text("{}", encoding="utf-8")
        datei = self._models_datei(
            tmp_path, project="mein-projekt", location="global", credentials_file=str(schluessel)
        )

        ergebnis = self._vertex(check_models(settings, path=datei))

        assert ergebnis.status is CheckStatus.OK
        assert "Dienstkonto" in ergebnis.detail
        assert ergebnis.context["endpoint"] == "aiplatform.googleapis.com"

    def test_ohne_vertex_anbieter_entsteht_kein_eintrag(
        self, settings: Settings, tmp_path: Any
    ) -> None:
        """Die Prüfung meldet sich nur, wenn es etwas zu melden gibt."""
        import yaml

        from wissensgraph.diagnostics import check_models

        datei = tmp_path / "models.yaml"
        datei.write_text(
            yaml.safe_dump(
                {
                    "providers": {"gemini": {"type": "google_genai", "api_key": "x"}},
                    "tasks": {
                        "cluster_labeling": {
                            "primary": {"provider": "gemini", "model": "gemini-3.5-flash-lite"}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        ergebnisse = check_models(settings, path=datei)

        assert not [item for item in ergebnisse if item.name.startswith("vertex:")]
