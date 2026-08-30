"""Kommandozeile ``wg`` (§19).

Eine dünne Hülle um dieselben Funktionen, die auch API und MCP-Server benutzen (Leitprinzip 14).
Kein Kommando enthält Fachlogik; jedes ruft eine Funktion auf, die auch ohne CLI aufrufbar ist.

Alle Kommandos laufen unverändert unter Windows, macOS und Linux — es wird an keiner Stelle eine
Shell aufgerufen oder ein Pfad zusammengesetzt.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Annotated

import typer

from wissensgraph.bootstrap import bootstrap
from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigError
from wissensgraph.config.masking import mask_config
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import load_sources
from wissensgraph.diagnostics import CheckStatus, run_diagnostics
from wissensgraph.infrastructure.adapters import AdapterRegistry
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

sources_app = typer.Typer(name="sources", help="Quellen einsehen (§19).", no_args_is_help=True)
app.add_typer(sources_app)

runs_app = typer.Typer(name="runs", help="Läufe einsehen (§7.4, §16.2).", no_args_is_help=True)
app.add_typer(runs_app)

concepts_app = typer.Typer(
    name="concepts", help="Konzepte anlegen und ansehen (§7, §17.4).", no_args_is_help=True
)
app.add_typer(concepts_app)

graph_app = typer.Typer(name="graph", help="Den Graphen abfragen (§12, §19).", no_args_is_help=True)
app.add_typer(graph_app)

SourcesFileOption = Annotated[
    Path | None,
    typer.Option("--sources", help="Pfad zu sources.yaml; sonst aus WG_SOURCES_FILE."),
]

StoreOption = Annotated[
    str,
    typer.Option("--store", help="Store, in dem die Läufe verbucht sind."),
]

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


@contextmanager
def _maschinenlesbar() -> Iterator[None]:
    """Lenkt den Log für die Dauer eines Blocks auf stderr.

    Der Log geht sonst nach stdout (§21.1) — für einen Dienst richtig, für ein Kommando mit
    ``--json`` fatal: Eine Logzeile mitten in der Ausgabe macht sie unlesbar für ``jq``. Hier ist
    stdout die Nutzlast, und alles andere gehört auf den Fehlerkanal.
    """
    root = logging.getLogger()
    umgeleitet = [
        (handler, handler.stream)
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler)
    ]
    for handler, _ in umgeleitet:
        handler.setStream(sys.stderr)
    try:
        yield
    finally:
        for handler, stream in umgeleitet:
            handler.setStream(stream)


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


@sources_app.command("list")
def sources_list(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    sources_file: Annotated[
        Path | None,
        typer.Option("--sources", help="Pfad zu sources.yaml; sonst aus WG_SOURCES_FILE."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Als JSON ausgeben statt als Tabelle.")
    ] = False,
) -> None:
    """Listet die konfigurierten Quellen mit Capabilities und Zustand (§19).

    Der Zustand entsteht durch einen echten ``health()``-Aufruf. Eine ausgefallene Quelle
    erscheint deshalb mit ihrem Grund und nicht einfach gar nicht (§8.3) — und das Kommando
    endet trotzdem mit 0: Ein Quellausfall ist eine Meldung, kein Werkzeugfehler.
    """
    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext():
        try:
            sources = load_sources(settings, path=sources_file)
            registered = AdapterRegistry().build_all(sources)
        except ConfigError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(
            json.dumps([item.as_dict() for item in registered], indent=2, ensure_ascii=False)
        )
        return

    if not registered:
        typer.echo("Keine eingeschaltete Quelle konfiguriert.")
        return

    for item in registered:
        symbol = _SYMBOLS[CheckStatus.OK if item.usable else CheckStatus.FAIL]
        faehig = ", ".join(
            name
            for name, an in (
                {} if item.adapter is None else item.adapter.capabilities.model_dump()
            ).items()
            if an
        )
        typer.echo(
            f"{symbol} {item.name} (adapter '{item.config.adapter}', Präfix "
            f"'{item.config.id_prefix}' -> Scope '{item.config.target.scope}')"
        )
        typer.echo(f"       Fähigkeiten: {faehig or 'keine'}")
        typer.echo(f"       Zustand: {item.health.state} — {item.health.detail}")


@app.command("sync")
def sync(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    sources_file: SourcesFileOption = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Name der Quelle aus sources.yaml.")
    ] = None,
    alle: Annotated[
        bool, typer.Option("--all", help="Über alle benutzbaren Quellen laufen.")
    ] = False,
    full: Annotated[
        bool,
        typer.Option("--full", help="Vollabgleich: den gespeicherten Cursor ignorieren."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Alles ausführen und am Ende verwerfen (§19)."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Die Läufe als JSON ausgeben.")] = False,
) -> None:
    """Gleicht eine Quelle oder alle Quellen mit dem Graphen ab (§10.1, §19).

    Wiederholbar und idempotent: Ein zweiter Lauf ohne Quelländerung schreibt nichts (§10.2
    Regel 3). Der Rückgabewert ist 1, sobald ein Lauf gescheitert ist — damit ist das Kommando in
    einem Skript verwendbar.
    """
    from wissensgraph.domain.runs import RunStatus
    from wissensgraph.ports.runs import SourceBusy
    from wissensgraph.runtime import Runtime, UnknownSourceError
    from wissensgraph.services.sync import SyncRequest

    if (source is None) == (not alle):
        typer.echo("Entweder --source <name> oder --all angeben, nicht beides.", err=True)
        raise typer.Exit(code=2)

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    request = SyncRequest(full=full, dry_run=dry_run)

    with _maschinenlesbar() if as_json else nullcontext():
        try:
            with Runtime(settings, sources_file=sources_file) as runtime:
                laeufe = (
                    runtime.run_sync_all(request)
                    if alle
                    else (runtime.run_sync(str(source), request),)
                )
        except ConfigError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        except (UnknownSourceError, SourceBusy, RuntimeError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps([run.as_dict() for run in laeufe], indent=2, ensure_ascii=False))
    else:
        for run in laeufe:
            _lauf_zeile(run)
        if dry_run:
            typer.echo(f"{_SYMBOLS[CheckStatus.WARN]} Trockenlauf: nichts geschrieben.")

    gescheitert = any(run.status is RunStatus.FAILED for run in laeufe)
    raise typer.Exit(code=1 if gescheitert else 0)


def _lauf_zeile(run: object) -> None:
    """Eine Zeile je Lauf: Zustand, Quelle, Dauer und die Zähler."""
    from wissensgraph.domain.runs import Run, RunStatus

    assert isinstance(run, Run)
    symbol = _SYMBOLS[CheckStatus.OK if run.status is RunStatus.SUCCEEDED else CheckStatus.FAIL]
    quelle = run.params.get(defaults.RUN_PARAM_SOURCE, "—")
    dauer = "" if run.duration_seconds is None else f", {run.duration_seconds:.1f} s"
    typer.echo(f"{symbol} {quelle}: {run.status}{dauer} (Lauf {run.id})")
    if run.error:
        typer.echo(f"       Fehler: {run.error}")
    if run.stats:
        zahlen = ", ".join(
            f"{name}={wert}" for name, wert in run.stats.items() if isinstance(wert, int)
        )
        typer.echo(f"       {zahlen}")


@runs_app.command("list")
def runs_list(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    store: StoreOption = defaults.STORE_SHARED,
    limit: Annotated[
        int, typer.Option("--limit", help="Wie viele Läufe höchstens.")
    ] = defaults.RUNS_LIST_LIMIT,
    as_json: Annotated[bool, typer.Option("--json", help="Als JSON ausgeben.")] = False,
) -> None:
    """Zeigt die zuletzt begonnenen Läufe eines Stores (§7.4, §16.2)."""
    from wissensgraph.infrastructure.db.locks import SqlSourceLocks
    from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
    from wissensgraph.services.sync import SyncService

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext(), StoreRegistry(settings) as registry:
        try:
            dienst = SyncService(settings, UnitOfWorkFactory(registry), SqlSourceLocks(registry))
            laeufe = dienst.recent_runs(store=store, limit=limit)
        except UnknownStoreError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(json.dumps([run.as_dict() for run in laeufe], indent=2, ensure_ascii=False))
        return
    if not laeufe:
        typer.echo(f"Keine Läufe im Store '{store}'.")
        return
    for run in laeufe:
        _lauf_zeile(run)


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Die ID des Laufs.")],
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    store: StoreOption = defaults.STORE_SHARED,
) -> None:
    """Zeigt einen Lauf mit Parametern und vollständiger Statistik."""
    from uuid import UUID

    from wissensgraph.infrastructure.db.locks import SqlSourceLocks
    from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
    from wissensgraph.services.sync import RunNotFound, SyncService

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar(), StoreRegistry(settings) as registry:
        try:
            dienst = SyncService(settings, UnitOfWorkFactory(registry), SqlSourceLocks(registry))
            run = dienst.get_run(UUID(run_id), store=store)
        except (RunNotFound, UnknownStoreError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            typer.echo(f"'{run_id}' ist keine gültige Lauf-ID.", err=True)
            raise typer.Exit(code=2) from exc

    typer.echo(json.dumps(run.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))


@concepts_app.command("add")
def concepts_add(
    concept_id: Annotated[str, typer.Argument(help="Die ID, z. B. 'project:finance'.")],
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    scope: Annotated[str, typer.Option("--scope", help="Scope; bestimmt den Store (§7.3).")] = (
        defaults.STORE_PERSONAL
    ),
    concept_type: Annotated[
        str, typer.Option("--type", help="Typ aus der Taxonomie (§7.2).")
    ] = "Project",
    title: Annotated[str | None, typer.Option("--title")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    body: Annotated[
        str | None, typer.Option("--body", help="Fließtext; '[[id]]' wird zur Kante (§7.1).")
    ] = None,
    link: Annotated[
        list[str] | None,
        typer.Option("--link", help="Verweis auf ein anderes Konzept; mehrfach angebbar."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Legt ein Konzept an oder schreibt es fort — der Weg zu einem Brücken-Konzept (§7.3).

    Ein Brücken-Konzept ist nichts Besonderes: ein Konzept vom Typ ``Project`` im Scope
    ``personal``, das per ``--link`` auf Konzepte des geteilten Stores zeigt. Die Kanten entstehen
    daraus von selbst, und ihr Zielstore wird gesucht statt behauptet (§12.1).

    Der Akteur ist ``user:cli``: Was von Hand kommt, gilt als kuratiert und wird von keinem Lauf
    überschrieben (§10.4).
    """
    from wissensgraph.domain.concepts import ConceptDraft
    from wissensgraph.runtime import Runtime
    from wissensgraph.services.concepts import ConceptValidationError

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    draft = ConceptDraft(
        id=concept_id,
        scope=scope,
        type=concept_type,
        title=title,
        description=description,
        body=body,
        references=tuple(link or ()),
        curated=True,
    )

    with _maschinenlesbar() if as_json else nullcontext():
        try:
            with Runtime(settings) as runtime:
                ergebnis = runtime.concepts.upsert(draft, actor=defaults.ACTOR_CLI)
        except ConceptValidationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(json.dumps(ergebnis.as_dict(), indent=2, ensure_ascii=False))
        return
    typer.echo(
        f"{_SYMBOLS[CheckStatus.OK]} {ergebnis.concept_id} in '{ergebnis.store}': "
        f"{ergebnis.outcome}, {len(ergebnis.edges_added)} Kante(n) neu, "
        f"{len(ergebnis.edges_removed)} entfernt."
    )


@concepts_app.command("show")
def concepts_show(
    concept_id: Annotated[str, typer.Argument(help="Die ID des Konzepts.")],
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    store: StoreOption = defaults.STORE_PERSONAL,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Zeigt ein Konzept mit seinen Kanten in **beide** Richtungen (§12.1).

    Die Gegenrichtung einer Brücke steht nicht im Zielstore — der geteilte Store weiß nicht, dass
    es persönliche Konzepte gibt. Sie wird deshalb aus den anderen Stores rekonstruiert, und genau
    das macht dieses Kommando sichtbar.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext(), Runtime(settings) as runtime:
        ansicht = runtime.concepts.describe(concept_id, store=store)

    if ansicht is None:
        typer.echo(f"Kein Konzept '{concept_id}' im Store '{store}'.", err=True)
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps(ansicht.as_dict(), indent=2, ensure_ascii=False))
        return

    concept = ansicht.concept
    typer.echo(f"{concept.id} ({concept.type}, Scope '{concept.scope}', {concept.status})")
    typer.echo(f"  Titel: {concept.title or '—'}")
    typer.echo(f"  ausgehend: {len(ansicht.outgoing)}")
    for edge in ansicht.outgoing:
        marke = "" if edge.resolved else "  (nicht auflösbar)"
        typer.echo(f"       {edge.kind} -> {edge.to_store}:{edge.to_id}{marke}")
    typer.echo(f"  eingehend: {len(ansicht.incoming)}")
    for edge in ansicht.incoming:
        marke = "" if edge.resolved else "  (nicht auflösbar)"
        typer.echo(f"       {edge.kind} <- {edge.from_store}:{edge.from_id}{marke}")


@graph_app.command("traverse")
def graph_traverse(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    start: Annotated[
        list[str] | None, typer.Option("--start", help="Startknoten; mehrfach angebbar.")
    ] = None,
    store: StoreOption = defaults.STORE_PERSONAL,
    hops: Annotated[
        int | None, typer.Option("--hops", help="Tiefe; sonst traversal.default_hops.")
    ] = None,
    max_nodes: Annotated[int | None, typer.Option("--max-nodes")] = None,
    tombstones: Annotated[
        bool, typer.Option("--tombstones", help="Grabsteine mit anzeigen (§12.3).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Löst den Kernspace um einen Startknoten auf (§12.1, §19).

    Das Ergebnis ist nach Bewertung sortiert: Nähe, Referenzdichte und Aktualität nach §12.3. Die
    Zahl der Datenbankabfragen steht mit dabei — sie ist eine zugesicherte Eigenschaft und keine
    Nebensache (§24, Stufe 6).
    """
    from wissensgraph.runtime import Runtime
    from wissensgraph.services.graph import UnknownStartError

    if not start:
        typer.echo("Mindestens ein --start angeben.", err=True)
        raise typer.Exit(code=2)

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext():
        try:
            with Runtime(settings) as runtime:
                ergebnis = runtime.graph.traverse(
                    start,
                    store=store,
                    hops=hops,
                    max_nodes=max_nodes,
                    include_tombstones=tombstones,
                )
        except UnknownStartError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(ergebnis.as_dict(), indent=2, ensure_ascii=False))
        return

    typer.echo(
        f"{len(ergebnis.nodes)} Knoten, {len(ergebnis.edges)} Kanten, {ergebnis.hops} Hops, "
        f"{ergebnis.queries} Abfrage(n)" + (", gedeckelt" if ergebnis.truncated else "")
    )
    for node in ergebnis.nodes:
        typer.echo(
            f"  {node.score:6.3f}  {node.hops} Hop(s), Dichte {node.density:3d}  "
            f"{node.store}:{node.concept.id} — {node.concept.title or '—'}"
        )


@graph_app.command("search")
def graph_search(
    query: Annotated[str, typer.Argument(help="Suchbegriff.")],
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    store: StoreOption = defaults.STORE_SHARED,
    limit: Annotated[int, typer.Option("--limit")] = defaults.SEARCH_LIMIT,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Lexikalische Suche über Volltext und Trigramm (§12.4).

    Solange es keine Embeddings gibt, ist das der einzige Modus — und er steht im Ergebnis. Ein
    stiller Qualitätsverlust wäre die schlechtere Variante.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext(), Runtime(settings) as runtime:
        ergebnis = runtime.graph.search(query, store=store, limit=limit)

    if as_json:
        typer.echo(json.dumps(ergebnis.as_dict(), indent=2, ensure_ascii=False))
        return
    typer.echo(f"{len(ergebnis.hits)} Treffer (Modus '{ergebnis.mode}'):")
    for hit in ergebnis.hits:
        typer.echo(
            f"  {hit.score:6.4f}  {hit.concept.id} ({hit.concept.type}) — "
            f"{hit.concept.title or '—'}"
        )


@app.command("worker")
def worker(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    sources_file: SourcesFileOption = None,
    once: Annotated[
        bool, typer.Option("--once", help="Nur einen Job abarbeiten und beenden.")
    ] = False,
) -> None:
    """Arbeitet Jobs aus der Queue ab — der Startbefehl des worker-Containers (§5.1, §16.3).

    Die Schleife endet erst mit dem Prozess. Ein einzelner gescheiterter Job beendet sie nicht:
    Er landet mit seinem Grund in ``runs`` und im Log, und der nächste Job läuft.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="worker", dotenv_file=dotenv_file)
    try:
        with Runtime(settings, sources_file=sources_file) as runtime:
            erledigt = runtime.work(once=once)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except KeyboardInterrupt:  # pragma: no cover — nur beim Beenden von Hand
        typer.echo("Worker beendet.", err=True)
        return

    typer.echo(f"{_SYMBOLS[CheckStatus.OK]} {erledigt} Job(s) abgearbeitet.")


@app.command("mock-sources")
def mock_sources(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    host: Annotated[str, typer.Option("--host", help="Bind-Adresse.")] = defaults.MOCK_HOST,
    port: Annotated[int, typer.Option("--port", help="Port.")] = defaults.MOCK_PORT,
    fixtures: Annotated[
        Path | None, typer.Option("--fixtures", help="Verzeichnis der Seed-Daten (§9.2).")
    ] = None,
) -> None:
    """Startet den Mock-Quellserver — der Startbefehl des Containers ``mock-sources`` (§9).

    Als Kommando und nicht als ``uvicorn``-Aufruf im Compose, damit derselbe Startweg auf jeder
    Plattform gilt und der Dienst dieselbe Logging-Einrichtung bekommt wie alle anderen.
    """
    import uvicorn

    from wissensgraph.mocks import FixturesNotFound, create_mock_app

    # Nur wegen des Loggings: Der Mock-Server benutzt die Konfiguration selbst nicht, soll aber
    # in dasselbe Format schreiben wie alle anderen Dienste (§21.1).
    _load(config_file, service="mock-sources", dotenv_file=dotenv_file)
    try:
        application = create_mock_app(fixtures)
    except FixturesNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{_SYMBOLS[CheckStatus.WARN]} Entwicklungsdienst ohne Authentifizierung (§9.1).")
    uvicorn.run(application, host=host, port=port, log_config=None)


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
