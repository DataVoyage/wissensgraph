"""Tests des ``.env``-Parsers (§6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wissensgraph.config.dotenv import load_dotenv, parse_dotenv

pytestmark = pytest.mark.unit


class TestParseDotenv:
    def test_liest_einfache_zuweisungen(self) -> None:
        assert parse_dotenv("WG_ENV=dev\nWG_API_PORT=8080") == {
            "WG_ENV": "dev",
            "WG_API_PORT": "8080",
        }

    def test_ignoriert_kommentare_und_leerzeilen(self) -> None:
        content = "# Kommentar\n\nWG_ENV=dev\n   # eingerückter Kommentar\n"

        assert parse_dotenv(content) == {"WG_ENV": "dev"}

    def test_entfernt_export_praefix(self) -> None:
        assert parse_dotenv("export WG_ENV=prod") == {"WG_ENV": "prod"}

    @pytest.mark.parametrize("quoted", ['WG_TOKEN="geheim"', "WG_TOKEN='geheim'"])
    def test_entfernt_anfuehrungszeichen(self, quoted: str) -> None:
        assert parse_dotenv(quoted) == {"WG_TOKEN": "geheim"}

    def test_behaelt_gleichheitszeichen_im_wert(self) -> None:
        # DSNs und Base64-Token enthalten regelmäßig '='.
        assert parse_dotenv("WG_TOKEN=abc=def==") == {"WG_TOKEN": "abc=def=="}

    def test_erlaubt_leeren_wert(self) -> None:
        assert parse_dotenv("WG_BROKER_URL=") == {"WG_BROKER_URL": ""}

    def test_ueberspringt_zeilen_ohne_gleichheitszeichen(self) -> None:
        assert parse_dotenv("kaputt\nWG_ENV=dev") == {"WG_ENV": "dev"}

    def test_ueberspringt_zeilen_ohne_schluessel(self) -> None:
        assert parse_dotenv("=nurwert\nWG_ENV=dev") == {"WG_ENV": "dev"}

    def test_spaetere_zuweisung_gewinnt(self) -> None:
        assert parse_dotenv("WG_ENV=dev\nWG_ENV=prod") == {"WG_ENV": "prod"}


class TestLoadDotenv:
    def test_liest_vorhandene_datei(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        path.write_text("WG_ENV=test\n", encoding="utf-8")

        assert load_dotenv(path) == {"WG_ENV": "test"}

    def test_fehlende_datei_ist_kein_fehler(self, tmp_path: Path) -> None:
        # Im Container gibt es typischerweise keine .env — die Konfiguration kommt aus der
        # Prozessumgebung. Ein Fehler wäre hier falsch.
        assert load_dotenv(tmp_path / "gibt-es-nicht") == {}

    def test_verzeichnis_statt_datei_ist_kein_fehler(self, tmp_path: Path) -> None:
        assert load_dotenv(tmp_path) == {}
