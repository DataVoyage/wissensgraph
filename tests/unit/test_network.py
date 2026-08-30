"""Tests der Lokalitätsprüfung für DSNs (§6.5, Leitprinzip 2)."""

from __future__ import annotations

import pytest

from wissensgraph.config.network import extract_host, is_local_dsn, is_local_host

pytestmark = pytest.mark.unit


class TestExtractHost:
    def test_liest_host_aus_dsn(self) -> None:
        assert extract_host("postgresql+psycopg://wg:pw@db-shared:5432/wg") == "db-shared"

    def test_liest_host_ohne_zugangsdaten(self) -> None:
        assert extract_host("postgresql://localhost:5432/wg") == "localhost"

    def test_ohne_host_none(self) -> None:
        assert extract_host("sqlite:///lokal.db") is None

    def test_kaputtes_adressliteral_ergibt_none(self) -> None:
        assert extract_host("postgresql://[unvollstaendig:5432/wg") is None


class TestIsLocalHost:
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "LOCALHOST"])
    def test_loopback_ist_lokal(self, host: str) -> None:
        assert is_local_host(host)

    @pytest.mark.parametrize("host", ["10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.1.1"])
    def test_private_adressen_sind_lokal(self, host: str) -> None:
        # Docker-Bridge-Netze vergeben genau solche Adressen.
        assert is_local_host(host)

    @pytest.mark.parametrize("host", ["db-personal", "db-shared"])
    def test_bekannte_compose_services_sind_lokal(self, host: str) -> None:
        # Im Container ist der Servicename der Normalfall; ein DNS-Lookup zur Startzeit wäre
        # weder verlässlich noch wünschenswert.
        assert is_local_host(host)

    @pytest.mark.parametrize("host", ["meinrechner.local", "api.localhost"])
    def test_lokale_domaenen_sind_lokal(self, host: str) -> None:
        assert is_local_host(host)

    def test_kein_host_gilt_als_lokal(self) -> None:
        # Kein Host heißt Unix-Socket oder lokale Datei.
        assert is_local_host(None)
        assert is_local_host("")

    @pytest.mark.parametrize(
        "host", ["db.example.com", "8.8.8.8", "wg-shared.eu-central-1.rds.amazonaws.com"]
    )
    def test_oeffentliche_hosts_sind_nicht_lokal(self, host: str) -> None:
        assert not is_local_host(host)


class TestIsLocalDsn:
    def test_lokaler_dsn(self) -> None:
        assert is_local_dsn("postgresql+psycopg://wg:pw@db-personal:5432/wg_personal")

    def test_entfernter_dsn(self) -> None:
        assert not is_local_dsn("postgresql+psycopg://wg:pw@db.example.com:5432/wg_personal")
