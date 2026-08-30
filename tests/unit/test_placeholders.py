"""Tests der Platzhalterauflösung (§6.1 Regel 3)."""

from __future__ import annotations

import pytest

from wissensgraph.config.errors import PlaceholderResolutionError
from wissensgraph.config.placeholders import find_placeholders, resolve_placeholders

pytestmark = pytest.mark.unit


class TestResolvePlaceholders:
    def test_ersetzt_einfachen_platzhalter(self) -> None:
        result = resolve_placeholders("${WG_DB_SHARED_DSN}", {"WG_DB_SHARED_DSN": "postgres://x"})
        assert result == "postgres://x"

    def test_ersetzt_platzhalter_innerhalb_eines_strings(self) -> None:
        result = resolve_placeholders("${DIR}/models.yaml", {"DIR": "/app/config"})
        assert result == "/app/config/models.yaml"

    def test_ersetzt_mehrere_platzhalter_in_einem_string(self) -> None:
        result = resolve_placeholders("${A}:${B}", {"A": "links", "B": "rechts"})
        assert result == "links:rechts"

    def test_steigt_rekursiv_in_mappings_und_listen(self) -> None:
        data = {"stores": {"shared": {"dsn": "${DSN}"}}, "origins": ["${HOST}", "fest"]}

        result = resolve_placeholders(data, {"DSN": "postgres://x", "HOST": "http://a"})

        assert result == {
            "stores": {"shared": {"dsn": "postgres://x"}},
            "origins": ["http://a", "fest"],
        }

    def test_laesst_nicht_string_werte_unveraendert(self) -> None:
        data = {"port": 8080, "enabled": True, "ratio": 0.85, "nichts": None}

        assert resolve_placeholders(data, {}) == data

    def test_nutzt_fallback_wenn_variable_fehlt(self) -> None:
        assert resolve_placeholders("${WG_LOG_LEVEL:-INFO}", {}) == "INFO"

    def test_nutzt_fallback_wenn_variable_leer_ist(self) -> None:
        # Eine gesetzte, aber leere Variable ist im Docker-Umfeld ein häufiges Versehen und
        # soll wie eine fehlende behandelt werden.
        assert resolve_placeholders("${WG_LOG_LEVEL:-INFO}", {"WG_LOG_LEVEL": ""}) == "INFO"

    def test_variable_schlaegt_fallback(self) -> None:
        assert resolve_placeholders("${LEVEL:-INFO}", {"LEVEL": "DEBUG"}) == "DEBUG"

    def test_leerer_fallback_ergibt_leeren_string(self) -> None:
        assert resolve_placeholders("${WG_BROKER_URL:-}", {}) == ""

    def test_loest_verschachtelte_platzhalter_auf(self) -> None:
        env = {"WG_CONFIG_DIR": "/app/config", "WG_MODELS_FILE": "${WG_CONFIG_DIR}/models.yaml"}

        assert resolve_placeholders("${WG_MODELS_FILE}", env) == "/app/config/models.yaml"


class TestFehlerverhalten:
    def test_fehlende_variable_ohne_fallback_wirft(self) -> None:
        with pytest.raises(PlaceholderResolutionError) as excinfo:
            resolve_placeholders({"stores": {"dsn": "${WG_DB_SHARED_DSN}"}}, {})

        assert excinfo.value.placeholder == "WG_DB_SHARED_DSN"

    def test_fehlermeldung_nennt_pfad_und_variable(self) -> None:
        with pytest.raises(PlaceholderResolutionError) as excinfo:
            resolve_placeholders({"a": {"b": ["${FEHLT}"]}}, {}, path="cfg")

        message = str(excinfo.value)
        assert "FEHLT" in message
        assert "cfg.a.b[0]" in message

    def test_ergibt_niemals_leeren_string_statt_fehler(self) -> None:
        # §6.1 Regel 3: "Nicht auflösbare Platzhalter sind ein Startfehler, kein leerer String."
        with pytest.raises(PlaceholderResolutionError):
            resolve_placeholders("${FEHLT}", {})

    def test_zyklischer_verweis_wirft_statt_endlos_zu_laufen(self) -> None:
        env = {"A": "${B}", "B": "${A}"}

        with pytest.raises(PlaceholderResolutionError):
            resolve_placeholders("${A}", env)


class TestFindPlaceholders:
    def test_sammelt_namen_aus_verschachtelter_struktur(self) -> None:
        data = {"a": "${EINS}", "b": ["${ZWEI}", {"c": "${DREI}"}], "d": 5}

        assert find_placeholders(data) == {"EINS", "ZWEI", "DREI"}

    def test_ohne_platzhalter_leere_menge(self) -> None:
        assert find_placeholders({"a": "fest", "b": [1, 2]}) == set()
