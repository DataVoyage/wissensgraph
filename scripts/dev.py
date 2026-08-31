"""Entwicklungsbefehle des Projekts — der Ersatz für ein ``Makefile``.

Bewusst Python und kein ``Makefile``, keine ``.sh`` und keine ``.ps1``: ``make`` ist auf Windows
in der Regel nicht vorhanden, und ein Shell-Skript und sein PowerShell-Gegenstück driften
auseinander, sobald jemand nur eines von beiden pflegt. Ein Python-Skript läuft überall dort, wo
das Projekt ohnehin entwickelt wird.

Aufruf::

    uv run python scripts/dev.py --help
    uv run python scripts/dev.py test
    uv run python scripts/dev.py up --profile minimal
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "ui"

#: Compose-Profile aus §5.4.
PROFILES = ("dev", "live", "minimal", "test")


def run(command: Sequence[str], *, cwd: Path | None = None) -> int:
    """Führt einen Befehl aus und gibt seinen Rückgabewert zurück.

    ``shell=False`` ist Absicht: Die Argumentliste wird ohne Shell-Interpretation übergeben, was
    unter Windows und POSIX identisch funktioniert und Zitierungsprobleme ausschließt.
    """
    executable = shutil.which(command[0])
    if executable is None:
        print(
            f"'{command[0]}' wurde nicht gefunden. Ist es installiert und im PATH?", file=sys.stderr
        )
        return 127
    print(f"$ {' '.join(command)}")
    return subprocess.run([executable, *command[1:]], cwd=cwd or REPO_ROOT, check=False).returncode


def run_all(commands: Sequence[Sequence[str]], *, cwd: Path | None = None) -> int:
    """Führt Befehle nacheinander aus und bricht beim ersten Fehler ab."""
    for command in commands:
        code = run(command, cwd=cwd)
        if code != 0:
            return code
    return 0


# ---------------------------------------------------------------------------
# Befehle
# ---------------------------------------------------------------------------


def cmd_setup(_args: argparse.Namespace) -> int:
    """Installiert alle Abhängigkeiten für Backend und UI."""
    return run_all(
        [
            ["uv", "sync", "--group", "dev"],
        ]
    ) or run(["npm", "install"], cwd=UI_DIR)


def cmd_test(args: argparse.Namespace) -> int:
    """Führt die Testsuiten aus.

    Die Coverage-Schwelle steht in ``pyproject.toml`` bzw. ``ui/vite.config.ts``; ein Lauf
    unterhalb der Schwelle schlägt fehl.
    """
    if args.only == "ui":
        return run(["npx", "vitest", "run", "--coverage"], cwd=UI_DIR)
    code = run(["uv", "run", "pytest"])
    if args.only == "python" or code != 0:
        return code
    return run(["npx", "vitest", "run", "--coverage"], cwd=UI_DIR)


def cmd_e2e(_args: argparse.Namespace) -> int:
    """Führt die Playwright-Tests der Kernflüsse aus (§24, Stufe 11).

    Getrennt von ``test``, weil sie einen Browser brauchen: ``npx playwright install chromium``
    lädt ihn einmalig herunter. Ein Standardlauf, der still ein paar hundert Megabyte zieht, wäre
    eine Überraschung an der falschen Stelle.
    """
    return run(["npx", "playwright", "test"], cwd=UI_DIR)


def cmd_client(_args: argparse.Namespace) -> int:
    """Erzeugt den TypeScript-Client aus dem OpenAPI-Schema neu (§24, Stufe 11)."""
    return run(["uv", "run", "python", "scripts/generate_client.py"])


def cmd_lint(_args: argparse.Namespace) -> int:
    """Prüft Formatierung, Lint-Regeln, Typen und die Schichtentrennung aus §4.2."""
    return run_all(
        [
            ["uv", "run", "ruff", "format", "--check", "."],
            ["uv", "run", "ruff", "check", "."],
            ["uv", "run", "mypy"],
            ["uv", "run", "lint-imports"],
        ]
    ) or run(["npx", "tsc", "--noEmit"], cwd=UI_DIR)


def cmd_format(_args: argparse.Namespace) -> int:
    """Formatiert den Python-Code und behebt automatisch behebbare Lint-Verstöße."""
    return run_all(
        [["uv", "run", "ruff", "format", "."], ["uv", "run", "ruff", "check", "--fix", "."]]
    )


def cmd_up(args: argparse.Namespace) -> int:
    """Startet den Stack mit einem Compose-Profil (§5.4)."""
    return run(["docker", "compose", "--profile", args.profile, "up", "-d", "--build"])


def cmd_down(args: argparse.Namespace) -> int:
    """Stoppt den Stack. Mit ``--volumes`` werden auch die Datenbankdaten gelöscht."""
    command = ["docker", "compose", "--profile", args.profile, "down"]
    if args.volumes:
        command.append("--volumes")
    return run(command)


def cmd_logs(args: argparse.Namespace) -> int:
    """Zeigt die Logs eines Dienstes oder aller Dienste."""
    command = ["docker", "compose", "logs", "--follow", "--tail", "100"]
    if args.service:
        command.append(args.service)
    return run(command)


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Führt ``wg doctor`` im laufenden api-Container aus (§19)."""
    return run(["docker", "compose", "exec", "api", "wg", "doctor"])


def cmd_migrate(args: argparse.Namespace) -> int:
    """Führt ``wg migrate`` im laufenden api-Container aus (§19).

    Im Container und nicht auf dem Host: Der personal-Store liegt in einem Netz ohne Ausgang und
    ist von außen gar nicht erreichbar (§5.2).
    """
    command = ["docker", "compose", "exec", "api", "wg", "migrate"]
    if args.check:
        command.append("--check")
    return run(command)


def cmd_check(args: argparse.Namespace) -> int:
    """Der vollständige Durchlauf vor einem Commit: Lint, Typen, Tests."""
    return cmd_lint(args) or cmd_test(args)


# ---------------------------------------------------------------------------
# Argumentbehandlung
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Baut den Argumentparser mit allen Unterbefehlen."""
    parser = argparse.ArgumentParser(
        prog="dev.py",
        description="Entwicklungsbefehle des Wissensgraphen (plattformunabhängig).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help=cmd_setup.__doc__).set_defaults(func=cmd_setup)

    test_parser = subparsers.add_parser("test", help="Testsuiten ausführen.")
    test_parser.add_argument(
        "--only",
        choices=["python", "ui"],
        default=None,
        help="Nur eine der beiden Suiten ausführen.",
    )
    test_parser.set_defaults(func=cmd_test)

    subparsers.add_parser("e2e", help="Playwright-Tests der Kernflüsse.").set_defaults(func=cmd_e2e)
    subparsers.add_parser("client", help="TypeScript-Client neu erzeugen.").set_defaults(
        func=cmd_client
    )
    subparsers.add_parser("lint", help=cmd_lint.__doc__).set_defaults(func=cmd_lint)
    subparsers.add_parser("format", help=cmd_format.__doc__).set_defaults(func=cmd_format)
    subparsers.add_parser("check", help=cmd_check.__doc__).set_defaults(func=cmd_check)
    subparsers.add_parser("doctor", help=cmd_doctor.__doc__).set_defaults(func=cmd_doctor)

    migrate_parser = subparsers.add_parser("migrate", help="Migrationen im api-Container laufen.")
    migrate_parser.add_argument(
        "--check", action="store_true", help="Nur berichten, ob Migrationen ausstehen."
    )
    migrate_parser.set_defaults(func=cmd_migrate)

    up_parser = subparsers.add_parser("up", help="Stack starten.")
    up_parser.add_argument("--profile", choices=PROFILES, default="dev")
    up_parser.set_defaults(func=cmd_up)

    down_parser = subparsers.add_parser("down", help="Stack stoppen.")
    down_parser.add_argument("--profile", choices=PROFILES, default="dev")
    down_parser.add_argument(
        "--volumes", action="store_true", help="Auch die Datenbankdaten löschen."
    )
    down_parser.set_defaults(func=cmd_down)

    logs_parser = subparsers.add_parser("logs", help="Logs anzeigen.")
    logs_parser.add_argument("service", nargs="?", default=None)
    logs_parser.set_defaults(func=cmd_logs)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Einsprungpunkt."""
    args = build_parser().parse_args(argv)
    func = args.func
    result: int = func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
