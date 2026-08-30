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
from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import CheckStatus, run_diagnostics
from wissensgraph.infrastructure.db import StoreRegistry, UnknownStoreError
from wissensgraph.infrastructure.db.migrations import (
    MigrationResult,
    downgrade_store,
    render_sql,
    status,
    upgrade_all,
    upgrade_store,
)
from wissensgraph.migrations.context import MigrationError

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


def _load(config_file: Path | None, service: str, dotenv_file: Path | None = None) -> Settings:
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
    masked = mask_config(settings.model_dump(mode="json"))
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
    with StoreRegistry(settings) as registry:
        report = run_diagnostics(settings, registry)

    for result in report.results:
        typer.echo(f"{_SYMBOLS[result.status]} {result.name}: {result.detail}")

    typer.echo("")
    typer.echo("Ergebnis: " + ("alles in Ordnung" if report.healthy else "Fehler gefunden"))
    raise typer.Exit(code=report.exit_code)


@app.command("migrate")
def migrate(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    store: Annotated[
        str | None,
        typer.Option("--store", help="Nur diesen Store migrieren; sonst alle konfigurierten."),
    ] = None,
    revision: Annotated[
        str, typer.Option("--revision", help="Zielrevision; 'head' ist die neueste.")
    ] = "head",
    check: Annotated[
        bool,
        typer.Option("--check", help="Nur berichten, ob Migrationen ausstehen. Verändert nichts."),
    ] = False,
    sql: Annotated[
        bool,
        typer.Option("--sql", help="Das SQL ausgeben statt es auszuführen (Trockenlauf, §19)."),
    ] = False,
    downgrade_to: Annotated[
        str | None,
        typer.Option(
            "--downgrade-to",
            help="Migrationen bis zu dieser Revision zurücknehmen. Löscht Tabellen samt Inhalt.",
        ),
    ] = None,
) -> None:
    """Bringt die Stores auf den Stand des Schemas aus §7.4.

    Wiederholbar: Ein zweiter Aufruf auf einer bereits migrierten Datenbank meldet
    'unverändert' und schreibt nichts.
    """
    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)

    with StoreRegistry(settings) as registry:
        try:
            stores = _selected_stores(registry, store)
            if sql:
                exit_code = _migrate_sql(settings, registry, stores, revision)
            elif check:
                exit_code = _migrate_check(settings, registry, stores)
            elif downgrade_to is not None:
                exit_code = _migrate_down(settings, registry, stores, downgrade_to)
            else:
                exit_code = _migrate_apply(settings, registry, stores, revision)
        except (MigrationError, UnknownStoreError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    raise typer.Exit(code=exit_code)


def _selected_stores(registry: StoreRegistry, store: str | None) -> tuple[str, ...]:
    """Die zu bearbeitenden Stores; prüft einen ausdrücklich genannten Namen sofort."""
    if store is None:
        return registry.store_names
    registry.config_of(store)
    return (store,)


def _migrate_sql(
    settings: Settings, registry: StoreRegistry, stores: tuple[str, ...], revision: str
) -> int:
    """Gibt das SQL aller gewählten Stores aus, ohne eine Datenbank zu berühren."""
    for name in stores:
        typer.echo(f"-- Store: {name}")
        typer.echo(render_sql(settings, registry, name, revision=revision))
    return 0


def _migrate_check(settings: Settings, registry: StoreRegistry, stores: tuple[str, ...]) -> int:
    """Berichtet den Migrationsstand; Rückgabewert 1, sobald etwas aussteht."""
    items = [item for item in status(settings, registry) if item.store in stores]
    for item in items:
        symbol = _SYMBOLS[CheckStatus.FAIL if item.changed else CheckStatus.OK]
        state = "ausstehend" if item.changed else "aktuell"
        typer.echo(
            f"{symbol} {item.store}: {state} "
            f"(Stand '{item.revision_before}', Ziel '{item.revision_after}')"
        )
    return 1 if any(item.changed for item in items) else 0


def _migrate_apply(
    settings: Settings, registry: StoreRegistry, stores: tuple[str, ...], revision: str
) -> int:
    """Führt die Migration aus und meldet je Store, ob sich etwas geändert hat."""
    for name in stores:
        _report(upgrade_store(settings, registry, name, revision=revision))
    return 0


def _migrate_down(
    settings: Settings, registry: StoreRegistry, stores: tuple[str, ...], revision: str
) -> int:
    """Nimmt Migrationen zurück — der einzige schreibende Pfad, der Daten verwirft."""
    typer.echo(
        f"{_SYMBOLS[CheckStatus.WARN]} Rückbau auf '{revision}'. "
        f"Betroffene Tabellen werden samt Inhalt gelöscht.",
        err=True,
    )
    for name in stores:
        _report(downgrade_store(settings, registry, name, revision=revision))
    return 0


def _report(result: MigrationResult) -> None:
    """Eine Zeile je Store: was war, was ist, und ob sich etwas geändert hat."""
    change = "geändert" if result.changed else "unverändert"
    typer.echo(
        f"{_SYMBOLS[CheckStatus.OK]} {result.store}: {change} "
        f"('{result.revision_before}' -> '{result.revision_after}')"
    )


@app.command("serve")
def serve(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    skip_migrations: Annotated[
        bool,
        typer.Option("--skip-migrations", help="Den Server ohne vorherige Migration starten."),
    ] = False,
) -> None:
    """Startet die HTTP-API — der Startbefehl des api-Containers (§5.5).

    §5.5 legt die Reihenfolge fest: erst Migrationen, dann Server. Diese Reihenfolge steht hier
    als Code und nicht als verkettetes Shell-Kommando im Container: So gilt sie auf jeder
    Plattform gleich, ist testbar, und ein Fehler in der Migration verhindert den Serverstart,
    statt ihn nur zu verzögern.
    """
    import uvicorn

    from wissensgraph.api.app import create_app

    settings = _load(config_file, service="api", dotenv_file=dotenv_file)

    if not skip_migrations:
        with StoreRegistry(settings) as registry:
            try:
                for result in upgrade_all(settings, registry):
                    _report(result)
            except MigrationError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1) from exc

    # log_config=None lässt uvicorn die Logging-Konfiguration in Ruhe. Sonst richtet es eigene
    # Handler ein und setzt 'propagate = False' — seine Zugriffszeilen liefen dann an den
    # Pflichtfeldern aus §21.1 vorbei, und ein Log hätte zwei verschiedene Formate.
    uvicorn.run(
        create_app(settings),
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,
    )


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
