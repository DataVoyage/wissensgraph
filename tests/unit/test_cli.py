"""Tests der Kommandozeile ``wg`` (§19).

Die CLI wird über den Typer-Runner geprüft, nicht über einen Unterprozess: So laufen die Tests
plattformunabhängig und ohne die Annahme, dass ein bestimmtes Konsolenskript im PATH liegt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wissensgraph import __version__
from wissensgraph.cli import app
from wissensgraph.config.defaults import SECRET_MASK

pytestmark = pytest.mark.unit

runner = CliRunner()

#: Pfad auf eine absichtlich nicht existierende ``.env``. Jeder Aufruf reicht ihn mit ``--dotenv``
#: durch, damit die Tests unabhängig davon laufen, was auf dem ausführenden Rechner in ``.env``
#: steht — eine dort gesetzte ``WG_``-Variable würde sonst die Erwartungen verschieben.
ISOLATED_DOTENV = Path(__file__).parent / "absichtlich-nicht-vorhanden.env"


def invoke(*args: str) -> Any:
    """Ruft die CLI mit isolierter ``.env`` auf."""
    return runner.invoke(app, [*args, "--dotenv", str(ISOLATED_DOTENV)])


@pytest.fixture
def config_file(minimal_config_dict: dict[str, Any], write_config: Any) -> Path:
    minimal_config_dict["stores"] = {
        "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
        "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
    }
    minimal_config_dict["api"] = {"auth_mode": "token", "token": "sehr-geheim"}
    return write_config(minimal_config_dict)


class TestConfigShow:
    def test_gibt_aufgeloeste_konfiguration_als_json(self, config_file: Path) -> None:
        # Abnahmekriterium Stufe 0: "wg config show zeigt die aufgelöste Konfiguration".
        result = invoke("config", "show", "--config", str(config_file))

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["embedding_dim"] == 768
        assert payload["clustering"]["neighbors_k"] == 8

    def test_maskiert_secrets(self, config_file: Path) -> None:
        # Abnahmekriterium Stufe 0: "... mit maskierten Secrets".
        result = invoke("config", "show", "--config", str(config_file))

        assert "sehr-geheim" not in result.stdout
        assert json.loads(result.stdout)["api"]["token"] == SECRET_MASK

    def test_fehlender_pflichtwert_bricht_mit_klarer_meldung_ab(
        self, minimal_config_dict: dict[str, Any], write_config: Any
    ) -> None:
        # Abnahmekriterium Stufe 0: "ein fehlender Pflichtwert bricht den Start mit klarer
        # Meldung ab".
        del minimal_config_dict["embedding_dim"]

        result = invoke("config", "show", "--config", str(write_config(minimal_config_dict)))

        assert result.exit_code == 2
        assert "embedding_dim" in result.output

    def test_nicht_aufloesbarer_platzhalter_bricht_ab(
        self, minimal_config_dict: dict[str, Any], write_config: Any
    ) -> None:
        minimal_config_dict["stores"]["shared"]["dsn"] = "${WG_GIBT_ES_NICHT}"

        result = invoke("config", "show", "--config", str(write_config(minimal_config_dict)))

        assert result.exit_code == 2
        assert "WG_GIBT_ES_NICHT" in result.output

    def test_fehlende_config_datei_bricht_ab(self, tmp_path: Path) -> None:
        result = invoke("config", "show", "--config", str(tmp_path / "weg.yaml"))

        assert result.exit_code == 2
        assert "existiert nicht" in result.output


class TestDoctor:
    def test_meldet_alles_in_ordnung(self, config_file: Path) -> None:
        result = invoke("doctor", "--config", str(config_file))

        assert result.exit_code == 0
        assert "alles in Ordnung" in result.stdout
        assert "store:shared" in result.stdout
        assert "store:personal" in result.stdout

    def test_endet_mit_eins_bei_unerreichbarem_store(self, config_file: Path, mocker: Any) -> None:
        from wissensgraph.infrastructure.db.registry import StoreHealth, StoreRegistry

        mocker.patch.object(
            StoreRegistry,
            "check_all",
            return_value=(StoreHealth("personal", False, "sqlite://", "weg"),),
        )

        result = invoke("doctor", "--config", str(config_file))

        assert result.exit_code == 1
        assert "Fehler gefunden" in result.stdout

    def test_ausgabe_bleibt_in_der_windows_codepage_darstellbar(self, config_file: Path) -> None:
        """Die Ausgabe darf keine Zeichen enthalten, an denen eine Windows-Konsole scheitert.

        Die deutschen Meldungen enthalten Umlaute; die sind in cp1252 (der Standard-Codepage
        einer deutschen Windows-Konsole) darstellbar. Symbole wie ``✓`` oder ``✗`` sind es nicht —
        deshalb stehen in :data:`wissensgraph.cli._SYMBOLS` ASCII-Kürzel. Ein Diagnosewerkzeug,
        das an seiner eigenen Ausgabe abbricht, wäre wertlos.
        """
        result = invoke("doctor", "--config", str(config_file))

        assert "[ ok ]" in result.stdout
        result.stdout.encode("cp1252")

    def test_gibt_keine_klartext_secrets_aus(self, config_file: Path) -> None:
        result = invoke("doctor", "--config", str(config_file))

        assert "sehr-geheim" not in result.stdout


class TestVersion:
    def test_gibt_paketversion_aus(self) -> None:
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert result.stdout.strip() == __version__


class TestHilfe:
    def test_ohne_argumente_zeigt_hilfe(self) -> None:
        result = runner.invoke(app, [])

        assert "doctor" in result.output
        assert "config" in result.output
