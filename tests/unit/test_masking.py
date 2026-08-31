"""Tests der Secret-Maskierung (§6.1 Regel 5, §20.2)."""

from __future__ import annotations

import pytest

from wissensgraph.config.defaults import SECRET_MASK
from wissensgraph.config.masking import is_secret_key, mask_config, mask_dsn

pytestmark = pytest.mark.unit


class TestIsSecretKey:
    @pytest.mark.parametrize(
        "key",
        ["password", "api_key", "apiKey", "WG_API_TOKEN", "client_secret", "credentials_file"],
    )
    def test_erkennt_secret_schluessel(self, key: str) -> None:
        assert is_secret_key(key)

    @pytest.mark.parametrize("key", ["host", "port", "scope", "neighbors_k", "auth_mode"])
    def test_erkennt_harmlose_schluessel(self, key: str) -> None:
        assert not is_secret_key(key)

    def test_der_antwortdeckel_ist_kein_secret(self) -> None:
        """``max_response_tokens`` trägt den Marker 'token', ist aber eine Zahl aus §18.3.

        Maskiert stand in ``wg config show`` und unter ``/config/effective`` ein ``***`` — genau
        an der Stelle, an der man nachsieht, warum eine Agentenantwort gekürzt wurde. Ein
        Diagnosewerkzeug, das über harmlose Werte schweigt, wird beim Suchen nicht mehr geglaubt.
        """
        assert not is_secret_key("max_response_tokens")

    def test_die_ausnahme_gilt_nur_fuer_den_genauen_namen(self) -> None:
        """Sonst wäre die Liste ein Loch: Ein Präfix davor macht daraus wieder ein Secret."""
        assert is_secret_key("max_response_tokens_secret")
        assert is_secret_key("provider_max_response_tokens")


class TestMaskDsn:
    def test_maskiert_passwort_behaelt_host(self) -> None:
        masked = mask_dsn("postgresql+psycopg://wg:geheim@db-shared:5432/wg_shared")

        assert "geheim" not in masked
        # Host und Datenbank bleiben lesbar — sonst ist die Ausgabe für die Diagnose wertlos.
        assert "db-shared:5432/wg_shared" in masked
        assert "wg:***@" in masked

    def test_laesst_dsn_ohne_zugangsdaten_unveraendert(self) -> None:
        dsn = "postgresql+psycopg://db-shared:5432/wg_shared"

        assert mask_dsn(dsn) == dsn

    def test_laesst_wert_ohne_netloc_unveraendert(self) -> None:
        assert mask_dsn("sqlite:///lokal.db") == "sqlite:///lokal.db"

    def test_maskiert_redis_url(self) -> None:
        masked = mask_dsn("redis://benutzer:pw@broker:6379/0")

        assert "pw" not in masked
        assert "broker:6379" in masked


class TestMaskConfig:
    def test_maskiert_secret_werte(self) -> None:
        result = mask_config({"api": {"token": "sehr-geheim", "port": 8080}})

        assert result == {"api": {"token": SECRET_MASK, "port": 8080}}

    def test_maskiert_dsn_passwoerter(self) -> None:
        result = mask_config({"stores": {"shared": {"dsn": "postgres://u:p@host:5432/db"}}})

        assert result["stores"]["shared"]["dsn"] == "postgres://u:***@host:5432/db"

    def test_maskiert_in_listen(self) -> None:
        result = mask_config({"tokens": ["eins", "zwei"]})

        assert result == {"tokens": [SECRET_MASK, SECRET_MASK]}

    def test_laesst_none_als_none(self) -> None:
        # Der Unterschied zwischen "nicht gesetzt" und "gesetzt, aber geheim" ist für die
        # Diagnose wesentlich und darf durch die Maskierung nicht verlorengehen.
        assert mask_config({"api": {"token": None}}) == {"api": {"token": None}}

    def test_veraendert_das_original_nicht(self) -> None:
        original = {"api": {"token": "geheim"}}

        mask_config(original)

        assert original == {"api": {"token": "geheim"}}

    def test_skalarer_wert_ohne_schluessel_bleibt_unveraendert(self) -> None:
        assert mask_config("einfach") == "einfach"
