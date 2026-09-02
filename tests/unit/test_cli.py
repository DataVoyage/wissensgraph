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


class TestMigrate:
    """``wg migrate`` (§19). Der tatsächliche Lauf gegen PostgreSQL steht in den Integrationstests.

    Hier wird nur geprüft, was die CLI ohne Datenbank beantworten kann — und das ist mehr, als es
    scheint: ``--sql`` rendert die vollständige Migration im Trockenlauf.
    """

    def test_sql_gibt_die_migration_aus_ohne_datenbank(self, config_file: Path) -> None:
        result = invoke("migrate", "--sql", "--config", str(config_file))

        assert result.exit_code == 0
        assert "-- Store: shared" in result.stdout
        assert "-- Store: personal" in result.stdout
        assert "CREATE TABLE concepts (" in result.stdout

    def test_check_meldet_ausstehende_migrationen_mit_rueckgabewert_eins(
        self, config_file: Path
    ) -> None:
        """Damit ist der Befehl in einem Startskript oder in CI verwendbar."""
        result = invoke("migrate", "--check", "--config", str(config_file))

        assert result.exit_code == 1
        assert "ausstehend" in result.stdout

    def test_unbekannter_store_wird_abgelehnt(self, config_file: Path) -> None:
        result = invoke("migrate", "--store", "gibtsnicht", "--config", str(config_file))

        assert result.exit_code == 1
        assert "Unbekannter Store" in result.output

    def test_beschraenkt_sich_auf_den_gewaehlten_store(self, config_file: Path) -> None:
        result = invoke("migrate", "--sql", "--store", "personal", "--config", str(config_file))

        assert "-- Store: personal" in result.stdout
        assert "-- Store: shared" not in result.stdout


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


class TestSourcesList:
    """``wg sources list`` — der Quellen-Teil aus §19."""

    @pytest.fixture
    def sources_file(self, tmp_path: Path) -> Path:
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
        return pfad

    def test_zeigt_die_quelle_mit_zustand_und_faehigkeiten(
        self, config_file: Path, sources_file: Path
    ) -> None:
        result = invoke(
            "sources", "list", "--config", str(config_file), "--sources", str(sources_file)
        )

        assert result.exit_code == 0
        assert "dummy" in result.stdout
        assert "healthy" in result.stdout
        assert "incremental" in result.stdout

    def test_json_ausgabe(self, config_file: Path, sources_file: Path) -> None:
        result = invoke(
            "sources",
            "list",
            "--json",
            "--config",
            str(config_file),
            "--sources",
            str(sources_file),
        )

        (eintrag,) = json.loads(result.stdout)

        assert eintrag["name"] == "dummy"
        assert eintrag["capabilities"]["single_fetch"] is True

    def test_ohne_quellen_ist_das_keine_stoerung(
        self, config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Am Standardort keine ``sources.yaml`` zu finden heißt: Es gibt keine Quellen."""
        monkeypatch.setenv("WG_CONFIG_DIR", str(tmp_path))

        result = invoke("sources", "list", "--config", str(config_file))

        assert result.exit_code == 0
        assert "Keine eingeschaltete Quelle" in result.stdout

    def test_ein_falscher_pfad_wird_gemeldet_statt_als_leer_ausgegeben(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        """Ein Vertipper in ``--sources`` darf nicht wie 'keine Quellen konfiguriert' aussehen.

        Das ist der Unterschied zwischen einer Feststellung über die Welt und einem Fehler des
        Aufrufers — und er entscheidet, wo jemand mit der Suche anfängt.
        """
        result = invoke(
            "sources",
            "list",
            "--config",
            str(config_file),
            "--sources",
            str(tmp_path / "gibt-es-nicht.yaml"),
        )

        assert result.exit_code != 0
        assert "existiert nicht" in result.output

    def test_ein_unbekannter_adapter_bricht_ab(self, config_file: Path, tmp_path: Path) -> None:
        """§6.5: Eine Quelle auf einen unauffindbaren Adapter ist ein Konfigurationsfehler."""
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

        result = invoke("sources", "list", "--config", str(config_file), "--sources", str(pfad))

        assert result.exit_code == 2
        assert "nirgends auffindbar" in result.output


@pytest.fixture
def fixture_sources(tmp_path: Path) -> Path:
    """Eine Quelle ohne Netzwerk und ohne Datenbankbedarf beim Registrieren (§9.1)."""
    pfad = tmp_path / "sources.yaml"
    pfad.write_text(
        "sources:\n"
        "  - name: fixtures\n"
        "    adapter: fixture-source\n"
        "    id_prefix: fix\n"
        "    target:\n"
        "      scope: engineering\n"
        "      default_type: Confluence Page\n"
        "    selection:\n"
        "      documents:\n"
        "        - external_id: '1'\n"
        "          title: Ein Dokument\n",
        encoding="utf-8",
    )
    return pfad


class TestSync:
    """``wg sync`` (§19). Der Lauf selbst braucht eine Datenbank; geprüft wird die Hülle."""

    def test_ohne_quelle_und_ohne_all_bricht_es_ab(self, config_file: Path) -> None:
        result = invoke("sync", "--config", str(config_file))

        assert result.exit_code == 2
        assert "--source" in result.output

    def test_beides_zugleich_bricht_ebenfalls_ab(self, config_file: Path) -> None:
        result = invoke("sync", "--config", str(config_file), "--source", "x", "--all")

        assert result.exit_code == 2

    def test_eine_unbekannte_quelle_meldet_sich_verstaendlich(
        self, config_file: Path, fixture_sources: Path
    ) -> None:
        result = invoke(
            "sync",
            "--config",
            str(config_file),
            "--sources",
            str(fixture_sources),
            "--source",
            "gibtesnicht",
        )

        assert result.exit_code == 1
        assert "Unbekannte Quelle" in result.output


class TestRuns:
    """``wg runs`` (§7.4, §16.2)."""

    def test_ein_unbekannter_store_bricht_ab(self, config_file: Path) -> None:
        result = invoke("runs", "list", "--config", str(config_file), "--store", "gibtesnicht")

        assert result.exit_code == 2
        assert "Unbekannter Store" in result.output

    def test_eine_ungueltige_lauf_id_wird_erkannt(self, config_file: Path) -> None:
        result = invoke("runs", "show", "keine-uuid", "--config", str(config_file))

        assert result.exit_code == 2
        assert "keine gültige Lauf-ID" in result.output


class TestConcepts:
    """``wg concepts`` — der Weg zu einem Brücken-Konzept (§7.3, §17.4)."""

    def test_ein_unbekannter_typ_bricht_vor_jeder_datenbank_ab(self, config_file: Path) -> None:
        """Die Taxonomie ist Konfiguration (§7.2); ein Tippfehler darin ist kein Laufzeitfehler."""
        result = invoke(
            "concepts",
            "add",
            "project:finance",
            "--config",
            str(config_file),
            "--type",
            "Projekt",
        )

        assert result.exit_code == 2
        assert "unbekannten Typ" in result.output

    def test_ein_typ_im_falschen_store_wird_abgewiesen(self, config_file: Path) -> None:
        """Ein ``Project`` gehört nach ``personal`` — der Scope entscheidet den Store (§7.2)."""
        result = invoke(
            "concepts",
            "add",
            "project:finance",
            "--config",
            str(config_file),
            "--scope",
            "engineering",
        )

        assert result.exit_code == 2
        assert "nicht zugelassen" in result.output


class TestGraph:
    """``wg graph`` (§12, §19)."""

    def test_ohne_startknoten_bricht_die_traversierung_ab(self, config_file: Path) -> None:
        result = invoke("graph", "traverse", "--config", str(config_file))

        assert result.exit_code == 2
        assert "--start" in result.output


class TestWorker:
    """``wg worker`` (§5.1, §16.3)."""

    def test_ohne_job_beendet_sich_der_einmal_lauf(
        self, config_file: Path, fixture_sources: Path
    ) -> None:
        result = invoke(
            "worker", "--config", str(config_file), "--sources", str(fixture_sources), "--once"
        )

        assert result.exit_code == 0
        assert "0 Job(s)" in result.stdout


class TestSetupAssistent:
    """``wg setup`` — der geführte Weg durch alle Einstellungen (§6).

    Geprüft wird hier die *Hülle*: Was das Kommando schreibt, wohin, und wie es sich verhält,
    wenn es nicht fragen kann. Der Katalog selbst und das Schreiben in die Dateien stehen in
    ``test_setup_assistent.py``.
    """

    @staticmethod
    def _arbeitsplatz(tmp_path: Path) -> tuple[Path, Path]:
        """Kopien der echten Dateien — der Assistent schreibt nie in das Repository."""
        wurzel = Path(__file__).resolve().parents[2]
        konfig = tmp_path / "config"
        konfig.mkdir()
        for name in ("wissensgraph.yaml", "models.yaml", "sources.yaml"):
            (konfig / name).write_text(
                (wurzel / "config" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        env = tmp_path / ".env"
        env.write_text("WG_EMBEDDING_DIM=768\nWG_API_TOKEN=echt-geheim\n", encoding="utf-8")
        return konfig / "wissensgraph.yaml", env

    def test_listet_den_katalog_ohne_etwas_zu_aendern(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)
        vorher = env.read_text(encoding="utf-8")

        ergebnis = runner.invoke(
            app, ["setup", "--list", "--config", str(kern), "--dotenv", str(env)]
        )

        assert ergebnis.exit_code == 0
        assert "WG_EMBEDDING_DIM" in ergebnis.stdout
        assert "clustering.min_similarity" in ergebnis.stdout
        assert env.read_text(encoding="utf-8") == vorher

    def test_zeigt_kein_geheimnis_im_klartext(self, tmp_path: Path) -> None:
        """§21.1: Die Ausgabe landet im Terminalprotokoll und in Bildschirmaufnahmen."""
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app, ["setup", "--list", "--config", str(kern), "--dotenv", str(env)]
        )

        assert "echt-geheim" not in ergebnis.stdout
        assert SECRET_MASK in ergebnis.stdout

    def test_liefert_den_katalog_auch_maschinenlesbar(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app, ["setup", "--list", "--json", "--config", str(kern), "--dotenv", str(env)]
        )

        katalog = json.loads(ergebnis.stdout)
        assert isinstance(katalog, list)
        namen = {eintrag["schluessel"] for eintrag in katalog}
        assert {"WG_API_PORT", "clustering.neighbors_k"} <= namen

    def test_schreibt_in_die_env_und_ins_yaml_je_nach_ziel(self, tmp_path: Path) -> None:
        """Die Regel, um die es geht: Der Platzhalter entscheidet, nicht der Aufrufer."""
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app,
            [
                "setup",
                "--config",
                str(kern),
                "--dotenv",
                str(env),
                "--set",
                "WG_API_PORT=9090",
                "--set",
                "clustering.neighbors_k=12",
            ],
        )

        assert ergebnis.exit_code == 0, ergebnis.stdout
        assert "WG_API_PORT=9090" in env.read_text(encoding="utf-8")
        assert "  neighbors_k: 12" in kern.read_text(encoding="utf-8")

    def test_laesst_die_kommentare_der_config_stehen(self, tmp_path: Path) -> None:
        """Neben 'min_similarity' stehen fünfundzwanzig Zeilen Begründung. Sie sind der
        eigentliche Wert der Datei — ein Assistent, der sie beim Ändern der Zahl verlöre,
        wäre schlimmer als gar keiner."""
        kern, env = self._arbeitsplatz(tmp_path)
        kommentarzeilen = sum(
            1 for zeile in kern.read_text(encoding="utf-8").splitlines()
            if zeile.strip().startswith("#")
        )

        runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env),
             "--set", "clustering.min_similarity=0.75"],
        )

        danach = sum(
            1 for zeile in kern.read_text(encoding="utf-8").splitlines()
            if zeile.strip().startswith("#")
        )
        assert danach == kommentarzeilen
        assert "  min_similarity: 0.75" in kern.read_text(encoding="utf-8")

    def test_weist_einen_unzulaessigen_wert_zurueck(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env),
             "--set", "WG_API_AUTH_MODE=vielleicht"],
        )

        assert ergebnis.exit_code == 2
        assert "WG_API_PORT" not in env.read_text(encoding="utf-8")

    def test_weist_eine_zuweisung_ohne_gleichheitszeichen_zurueck(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app, ["setup", "--config", str(kern), "--dotenv", str(env), "--set", "WG_API_PORT"]
        )

        assert ergebnis.exit_code == 2

    def test_prueft_ohne_zu_schreiben(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)
        vorher = env.read_text(encoding="utf-8")

        ergebnis = runner.invoke(
            app, ["setup", "--check", "--config", str(kern), "--dotenv", str(env)]
        )

        assert env.read_text(encoding="utf-8") == vorher
        assert ergebnis.exit_code in (0, 1)

    def test_meldet_eine_fehlende_konfigurationsdatei_verstaendlich(self, tmp_path: Path) -> None:
        """Der Assistent lädt die Konfiguration bewusst nicht über 'bootstrap': Er ist gerade
        das Werkzeug für den Fall, dass sie noch nicht trägt."""
        ergebnis = runner.invoke(
            app, ["setup", "--config", str(tmp_path / "gibt-es-nicht.yaml"), "--list"]
        )

        assert ergebnis.exit_code == 2
        assert "gibt-es-nicht.yaml" in ergebnis.output

    def test_nennt_den_skriptfaehigen_weg_wenn_die_eingabe_ausgeht(self, tmp_path: Path) -> None:
        """Im Container gibt es keine Tastatur. Ein Kommando, das dort auf eine Eingabe wartet,
        hängt still — die Meldung nennt stattdessen den Weg, der dort funktioniert.

        Bewusst kein Vorabtest auf ein Terminal: Antworten dürfen auch aus einer Pipe kommen,
        und ein solcher Test verböte gerade das."""
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(app, ["setup", "--config", str(kern), "--dotenv", str(env)])

        assert ergebnis.exit_code == 2
        assert "--set" in ergebnis.output


class TestSetupGefuehrt:
    """Der geführte Modus mit simulierter Eingabe.

    Er ist das eigentliche Kommando — ``--set`` ist die skriptfähige Abkürzung. Ein Assistent,
    dessen Führung nur von Hand geprüft wird, ist genau der Teil, der beim nächsten Umbau
    stillschweigend kaputtgeht.
    """

    @staticmethod
    def _arbeitsplatz(tmp_path: Path) -> tuple[Path, Path]:
        return TestSetupAssistent._arbeitsplatz(tmp_path)

    def test_uebernimmt_eingaben_und_schreibt_sie(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env), "-s", "Job-Queue (§5.1)"],
            input="redis://anderswo:6379/1\nj\n",
        )

        assert ergebnis.exit_code in (0, 1), ergebnis.output
        assert "WG_BROKER_URL=redis://anderswo:6379/1" in env.read_text(encoding="utf-8")

    def test_leere_eingabe_laesst_den_wert_stehen(self, tmp_path: Path) -> None:
        """Enter heißt "unverändert" — sonst müsste man jeden Wert neu tippen, um an den
        nächsten zu kommen."""
        kern, env = self._arbeitsplatz(tmp_path)
        vorher = env.read_text(encoding="utf-8")

        ergebnis = runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env), "-s", "Job-Queue (§5.1)"],
            input="\n",
        )

        assert "Nichts geändert" in ergebnis.output
        assert env.read_text(encoding="utf-8") == vorher

    def test_fragt_bei_unzulaessiger_eingabe_erneut(self, tmp_path: Path) -> None:
        """Statt abzubrechen: Wer sich vertippt, soll den Wert korrigieren können, ohne den
        ganzen Durchlauf zu wiederholen."""
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env), "--all",
             "-s", "config/wissensgraph.yaml — search"],
            input="viele\n25\n\n\nj\n",
        )

        assert "Erwartet wird eine ganze Zahl." in ergebnis.output
        assert "  limit: 25" in kern.read_text(encoding="utf-8")

    def test_ein_nein_schreibt_nichts(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)
        vorher = env.read_text(encoding="utf-8")

        ergebnis = runner.invoke(
            app,
            ["setup", "--config", str(kern), "--dotenv", str(env), "-s", "Job-Queue (§5.1)"],
            input="redis://anderswo:6379/1\nn\n",
        )

        assert "Abgebrochen" in ergebnis.output
        assert env.read_text(encoding="utf-8") == vorher

    def test_meldet_einen_unbekannten_abschnitt_mit_den_bekannten(self, tmp_path: Path) -> None:
        kern, env = self._arbeitsplatz(tmp_path)

        ergebnis = runner.invoke(
            app, ["setup", "--config", str(kern), "--dotenv", str(env), "-s", "Gibt es nicht"],
            input="\n",
        )

        assert ergebnis.exit_code == 2
        assert "Laufzeit" in ergebnis.output
