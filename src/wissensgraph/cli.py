"""Kommandozeile ``wg`` (§19).

Eine dünne Hülle um dieselben Funktionen, die auch API und MCP-Server benutzen (Leitprinzip 14).
Kein Kommando enthält Fachlogik; jedes ruft eine Funktion auf, die auch ohne CLI aufrufbar ist.

Alle Kommandos laufen unverändert unter Windows, macOS und Linux — es wird an keiner Stelle eine
Shell aufgerufen oder ein Pfad zusammengesetzt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from wissensgraph.bootstrap import bootstrap
from wissensgraph.config.errors import ConfigError
from wissensgraph.config.masking import mask_config
from wissensgraph.diagnostics import CheckStatus, run_diagnostics
from wissensgraph.infrastructure.db import StoreRegistry

app = typer.Typer(
    name="wg",
    help="Werkzeuge des Wissensgraphen.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(name="config", help="Konfiguration einsehen.", no_args_is_help=True)
app.add_typer(config_app)

ConfigFileOption = Annotated[
    Path | None,
    typer.Option("--config", help="Pfad zur Kern-Config-Datei; sonst aus WG_CONFIG_DIR."),
]

DotenvFileOption = Annotated[
    Path | None,
    typer.Option("--dotenv", help="Pfad zur .env-Datei; sonst '.env' im Arbeitsverzeichnis."),
]

#: Symbole der Statusanzeige. Bewusst ASCII: Eine Windows-Konsole in einer Codepage ohne
#: Unicode-Unterstützung würde bei Symbolen wie ✓ abbrechen, und ein Diagnosewerkzeug, das an
#: seiner eigenen Ausgabe scheitert, ist wertlos.
_SYMBOLS = {CheckStatus.OK: "[ ok ]", CheckStatus.WARN: "[warn]", CheckStatus.FAIL: "[fail]"}


def _load(config_file: Path | None, service: str, dotenv_file: Path | None = None) -> object:
    """Lädt die Konfiguration und beendet die CLI bei einem Konfigurationsfehler sauber."""
    try:
        return bootstrap(service=service, config_file=config_file, dotenv_file=dotenv_file)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@config_app.command("show")
def config_show(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Als JSON ausgeben statt lesbar formatiert.")
    ] = True,
) -> None:
    """Zeigt die aufgelöste Konfiguration mit maskierten Secrets (§6.1 Regel 5)."""
    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    masked = mask_config(settings.model_dump(mode="json"))  # type: ignore[attr-defined]
    if as_json:
        typer.echo(json.dumps(masked, indent=2, ensure_ascii=False, sort_keys=True))
    else:  # pragma: no cover — reine Darstellungsvariante
        typer.echo(masked)


@app.command("doctor")
def doctor(config_file: ConfigFileOption = None, dotenv_file: DotenvFileOption = None) -> None:
    """Prüft Konfiguration, Datenschutzregeln und Datenbankverbindungen (§19).

    Endet mit Rückgabewert 1, sobald eine Prüfung fehlschlägt — damit ist das Kommando in einem
    Startskript oder in CI verwendbar.
    """
    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with StoreRegistry(settings) as registry:  # type: ignore[arg-type]
        report = run_diagnostics(settings, registry)  # type: ignore[arg-type]

    for result in report.results:
        typer.echo(f"{_SYMBOLS[result.status]} {result.name}: {result.detail}")

    typer.echo("")
    typer.echo("Ergebnis: " + ("alles in Ordnung" if report.healthy else "Fehler gefunden"))
    raise typer.Exit(code=report.exit_code)


@app.command("version")
def version() -> None:
    """Gibt die Version des Pakets aus."""
    from wissensgraph import __version__

    typer.echo(__version__)


def main() -> None:  # pragma: no cover — Einsprungpunkt des Konsolenskripts
    """Einsprungpunkt für ``python -m wissensgraph.cli``."""
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
