"""Tests des Konfigurations-Loaders und der Präzedenzkette (§6.2, §6.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wissensgraph.config.errors import (
    ConfigFileError,
    ConfigValidationError,
    PlaceholderResolutionError,
)
from wissensgraph.config.loader import (
    apply_env_overrides,
    build_settings,
    deep_merge,
    load_yaml_mapping,
    set_path,
)

pytestmark = pytest.mark.unit


class TestLoadYamlMapping:
    def test_liest_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.yaml"
        path.write_text("a: 1\nb: zwei\n", encoding="utf-8")

        assert load_yaml_mapping(path) == {"a": 1, "b": "zwei"}

    def test_leere_datei_ergibt_leeres_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "leer.yaml"
        path.write_text("", encoding="utf-8")

        assert load_yaml_mapping(path) == {}

    def test_fehlende_datei_wirft(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileError, match="existiert nicht"):
            load_yaml_mapping(tmp_path / "gibtsnicht.yaml")

    def test_ungueltiges_yaml_wirft(self, tmp_path: Path) -> None:
        path = tmp_path / "kaputt.yaml"
        path.write_text("a: [unvollstaendig\n", encoding="utf-8")

        with pytest.raises(ConfigFileError, match="gültiges YAML"):
            load_yaml_mapping(path)

    def test_liste_auf_oberster_ebene_wirft(self, tmp_path: Path) -> None:
        path = tmp_path / "liste.yaml"
        path.write_text("- eins\n- zwei\n", encoding="utf-8")

        with pytest.raises(ConfigFileError, match="Mapping"):
            load_yaml_mapping(path)


class TestDeepMerge:
    def test_fuehrt_verschachtelte_mappings_zusammen(self) -> None:
        base = {"api": {"host": "0.0.0.0", "port": 8080}}
        override = {"api": {"port": 9090}}

        assert deep_merge(base, override) == {"api": {"host": "0.0.0.0", "port": 9090}}

    def test_ersetzt_listen_statt_sie_zu_verketten(self) -> None:
        # Eine höhere Präzedenzstufe soll eine Liste vollständig ablösen können — sonst würde
        # man einen einmal konfigurierten Eintrag nie wieder los.
        base = {"scopes": [{"name": "a"}, {"name": "b"}]}
        override = {"scopes": [{"name": "c"}]}

        assert deep_merge(base, override) == {"scopes": [{"name": "c"}]}

    def test_uebernimmt_neue_schluessel(self) -> None:
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_veraendert_das_original_nicht(self) -> None:
        base = {"api": {"port": 8080}}

        deep_merge(base, {"api": {"port": 9090}})

        assert base == {"api": {"port": 8080}}


class TestSetPath:
    def test_setzt_wert_auf_oberster_ebene(self) -> None:
        target: dict[str, Any] = {}

        set_path(target, ("env",), "prod")

        assert target == {"env": "prod"}

    def test_legt_fehlende_ebenen_an(self) -> None:
        target: dict[str, Any] = {}

        set_path(target, ("stores", "shared", "dsn"), "postgres://x")

        assert target == {"stores": {"shared": {"dsn": "postgres://x"}}}

    def test_ersetzt_nicht_mapping_zwischenebene(self) -> None:
        target: dict[str, Any] = {"stores": "unerwartet"}

        set_path(target, ("stores", "shared", "dsn"), "postgres://x")

        assert target == {"stores": {"shared": {"dsn": "postgres://x"}}}


class TestApplyEnvOverrides:
    def test_setzt_wert_aus_env(self) -> None:
        result = apply_env_overrides({}, {"WG_ENV": "prod"})

        assert result["env"] == "prod"

    def test_wandelt_ganzzahl(self) -> None:
        result = apply_env_overrides({}, {"WG_API_PORT": "9090"})

        assert result["api"]["port"] == 9090

    def test_wandelt_wahrheitswert(self) -> None:
        result = apply_env_overrides({}, {"WG_PERSONAL_ALLOW_REMOTE_MODELS": "true"})

        assert result["personal_allow_remote_models"] is True

    def test_wandelt_kommaseparierte_liste(self) -> None:
        result = apply_env_overrides({}, {"WG_API_CORS_ORIGINS": "http://a, http://b"})

        assert result["api"]["cors_origins"] == ("http://a", "http://b")

    def test_ignoriert_leere_variable(self) -> None:
        # Eine gesetzte, aber leere Variable soll einen sinnvollen YAML-Wert nicht überschreiben.
        result = apply_env_overrides({"api": {"port": 8080}}, {"WG_API_PORT": ""})

        assert result["api"]["port"] == 8080

    def test_ungueltige_ganzzahl_wirft_mit_variablennamen(self) -> None:
        with pytest.raises(ConfigValidationError, match="WG_API_PORT"):
            apply_env_overrides({}, {"WG_API_PORT": "achtzig"})

    def test_ungueltiger_wahrheitswert_wirft(self) -> None:
        with pytest.raises(ConfigValidationError, match="Wahrheitswert"):
            apply_env_overrides({}, {"WG_PERSONAL_ALLOW_REMOTE_MODELS": "vielleicht"})


class TestBuildSettingsPraezedenz:
    """§6.2: Code-Defaults < config/*.yaml < .env-Datei < Prozess-ENV < CLI-Flag."""

    def test_code_default_greift_ohne_andere_angabe(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        settings = build_settings(
            config_file=write_config(minimal_config_dict), env={}, dotenv_file=empty_dotenv
        )

        assert settings.clustering.neighbors_k == 8

    def test_yaml_schlaegt_code_default(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        minimal_config_dict["clustering"] = {"neighbors_k": 12}

        settings = build_settings(
            config_file=write_config(minimal_config_dict), env={}, dotenv_file=empty_dotenv
        )

        assert settings.clustering.neighbors_k == 12

    def test_dotenv_schlaegt_yaml(
        self, minimal_config_dict: dict[str, Any], write_config: Any, tmp_path: Path
    ) -> None:
        minimal_config_dict["api"] = {"auth_mode": "token", "token": "aus-yaml", "port": 8080}
        dotenv = tmp_path / ".env"
        dotenv.write_text("WG_API_PORT=7070\n", encoding="utf-8")

        settings = build_settings(
            config_file=write_config(minimal_config_dict), env={}, dotenv_file=dotenv
        )

        assert settings.api.port == 7070

    def test_prozess_env_schlaegt_dotenv(
        self, minimal_config_dict: dict[str, Any], write_config: Any, tmp_path: Path
    ) -> None:
        # Diese Richtung ist der Grund für den eigenen .env-Parser: Verbreitete Bibliotheken
        # machen es standardmäßig andersherum.
        dotenv = tmp_path / ".env"
        dotenv.write_text("WG_API_PORT=7070\n", encoding="utf-8")

        settings = build_settings(
            config_file=write_config(minimal_config_dict),
            env={"WG_API_PORT": "6060"},
            dotenv_file=dotenv,
        )

        assert settings.api.port == 6060

    def test_overrides_schlagen_prozess_env(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        settings = build_settings(
            config_file=write_config(minimal_config_dict),
            env={"WG_API_PORT": "6060"},
            dotenv_file=empty_dotenv,
            overrides={"api": {"port": 5050}},
        )

        assert settings.api.port == 5050


class TestBuildSettingsPlatzhalter:
    def test_loest_platzhalter_aus_prozess_env(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        minimal_config_dict["stores"]["shared"]["dsn"] = "${WG_DB_SHARED_DSN}"

        settings = build_settings(
            config_file=write_config(minimal_config_dict),
            env={"WG_DB_SHARED_DSN": "postgresql://wg@db-shared:5432/wg_shared"},
            dotenv_file=empty_dotenv,
        )

        assert settings.stores["shared"].dsn == "postgresql://wg@db-shared:5432/wg_shared"

    def test_loest_platzhalter_aus_dotenv(
        self, minimal_config_dict: dict[str, Any], write_config: Any, tmp_path: Path
    ) -> None:
        minimal_config_dict["stores"]["shared"]["dsn"] = "${WG_DB_SHARED_DSN}"
        dotenv = tmp_path / ".env"
        dotenv.write_text("WG_DB_SHARED_DSN=postgresql://wg@db-shared:5432/aus_datei\n", "utf-8")

        settings = build_settings(
            config_file=write_config(minimal_config_dict), env={}, dotenv_file=dotenv
        )

        assert settings.stores["shared"].dsn.endswith("/aus_datei")

    def test_fehlender_pflichtwert_bricht_start_ab(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        # Abnahmekriterium Stufe 0: "ein fehlender Pflichtwert bricht den Start mit klarer
        # Meldung ab".
        minimal_config_dict["stores"]["shared"]["dsn"] = "${WG_DB_SHARED_DSN}"

        with pytest.raises(PlaceholderResolutionError) as excinfo:
            build_settings(
                config_file=write_config(minimal_config_dict), env={}, dotenv_file=empty_dotenv
            )

        assert "WG_DB_SHARED_DSN" in str(excinfo.value)


class TestBuildSettingsValidierung:
    def test_fehlermeldung_nennt_datei_und_feld(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        del minimal_config_dict["embedding_dim"]
        config_file = write_config(minimal_config_dict)

        with pytest.raises(ConfigValidationError) as excinfo:
            build_settings(config_file=config_file, env={}, dotenv_file=empty_dotenv)

        message = str(excinfo.value)
        assert config_file.name in message
        assert "embedding_dim" in message

    def test_entfernter_personal_dsn_bricht_start_ab(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        # §6.5 / Leitprinzip 2: der personal-Store darf den lokalen Rechner nicht verlassen.
        minimal_config_dict["stores"]["personal"]["dsn"] = "postgresql://wg@db.example.com/wg"

        with pytest.raises(ConfigValidationError, match="allow_remote"):
            build_settings(
                config_file=write_config(minimal_config_dict), env={}, dotenv_file=empty_dotenv
            )

    def test_config_datei_wird_aus_config_dir_abgeleitet(
        self, minimal_config_dict: dict[str, Any], write_config: Any, empty_dotenv: Path
    ) -> None:
        config_file = write_config(minimal_config_dict)

        settings = build_settings(
            env={"WG_CONFIG_DIR": str(config_file.parent)}, dotenv_file=empty_dotenv
        )

        assert settings.embedding_dim == 768
