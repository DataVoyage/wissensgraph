"""Kommandozeile ``wg`` (§19).

Eine dünne Hülle um dieselben Funktionen, die auch API und MCP-Server benutzen (Leitprinzip 14).
Kein Kommando enthält Fachlogik; jedes ruft eine Funktion auf, die auch ohne CLI aufrufbar ist.

Alle Kommandos laufen unverändert unter Windows, macOS und Linux — es wird an keiner Stelle eine
Shell aufgerufen oder ein Pfad zusammengesetzt.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Annotated, Any

import click
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

models_app = typer.Typer(
    name="models", help="Den Model-Router einsehen (§11, §19).", no_args_is_help=True
)
app.add_typer(models_app)

ModelsFileOption = Annotated[
    Path | None,
    typer.Option("--models", help="Pfad zu models.yaml; sonst aus WG_MODELS_FILE."),
]

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


# -- Einrichtung ---------------------------------------------------------------


def _wizard_konfigdatei(config_file: Path | None) -> Path:
    """Die Kern-Config-Datei, die der Assistent bearbeitet — ohne sie zu laden.

    Bewusst nicht über ``bootstrap``: Der Assistent ist gerade das Werkzeug für den Fall, dass
    die Konfiguration *noch nicht* lädt. Ein Kommando, das erst eine gültige Konfiguration
    braucht, um sie einrichten zu können, hilft beim ersten Mal niemandem.
    """
    if config_file is not None:
        return config_file
    verzeichnis = os.environ.get("WG_CONFIG_DIR", "").strip() or defaults.LOCAL_CONFIG_DIR
    return Path(verzeichnis) / defaults.CORE_CONFIG_FILENAME


def _wizard_zeile(eintrag: Any, aktuell: str, quelle: str = "") -> str:
    """Eine Katalogzeile für die Übersicht."""
    from wissensgraph.config.wizard import maskiert

    marke = "!" if eintrag.pflicht and not aktuell else " "
    wert = maskiert(eintrag.schluessel, aktuell) or "(leer)"
    woher = "  [aus der Umgebung]" if quelle == "Umgebung" else ""
    return f" {marke} {eintrag.schluessel:<44} {wert}{woher}"


def _wizard_frage(eintrag: Any, aktuell: str, quelle: str = "") -> str | None:
    """Stellt eine Frage und gibt den neuen Wert zurück; ``None`` heißt "unverändert"."""
    from wissensgraph.config.wizard import maskiert

    typer.echo("")
    typer.echo(f"  {eintrag.schluessel}")
    if quelle == "Umgebung":
        # Wichtig zu wissen, bevor jemand hier etwas einträgt: Was der Prozess mitbringt,
        # schlägt die Datei (§6.2). Ein hier gesetzter Wert bliebe im Container wirkungslos.
        typer.echo("    Kommt derzeit aus der Prozessumgebung und schlägt die Datei (§6.2).")
    if eintrag.beschreibung:
        for stueck in textwrap.wrap(eintrag.beschreibung, width=94):
            typer.echo(f"    {stueck}")
    if eintrag.auswahl:
        typer.echo(f"    Erlaubt: {', '.join(eintrag.auswahl)}")
    if eintrag.vorgabe and not aktuell:
        typer.echo(f"    Vorgabe: {maskiert(eintrag.schluessel, eintrag.vorgabe)}")

    while True:
        eingabe: str = typer.prompt(
            f"    Wert [{maskiert(eintrag.schluessel, aktuell) or 'leer'}]",
            default="",
            show_default=False,
            hide_input=eintrag.geheim,
        ).strip()
        if not eingabe:
            return None
        if eingabe == "-":
            # Ein Wert lässt sich nur so wieder loswerden: Die leere Eingabe bedeutet bereits
            # "unverändert", und ohne diesen Weg bliebe ein einmal gesetztes Token für immer.
            return ""
        fehler = eintrag.pruefen(eingabe)
        if fehler is None:
            return eingabe
        typer.echo(f"    -> {fehler}")


def _wizard_bestaetigen(frage: str) -> bool:
    """Eine Ja/Nein-Frage, die auch auf Deutsch antwortbar ist.

    ``typer.confirm`` fragt ``[Y/n]`` und weist ein "j" als ungültige Eingabe zurück. In einem
    Werkzeug, dessen Fragen und Erklärungen durchgehend deutsch sind, ist das eine Falle: Wer
    "j" tippt, hat richtig geantwortet und bekommt einen Fehler.
    """
    ja = {"j", "ja", "y", "yes"}
    nein = {"n", "nein", "no"}
    while True:
        antwort = typer.prompt(f"{frage} [J/n]", default="j", show_default=False).strip().lower()
        if antwort in ja:
            return True
        if antwort in nein:
            return False
        typer.echo(f"  -> Bitte {'/'.join(sorted(ja))} oder {'/'.join(sorted(nein))}.")


@app.command("setup")
def setup(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    abschnitt: Annotated[
        list[str] | None,
        typer.Option(
            "--section",
            "-s",
            help="Nur diese Abschnitte durchgehen; mehrfach angebbar. Ohne Angabe: alle.",
        ),
    ] = None,
    setzen: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="NAME=WERT ohne Rückfrage setzen; mehrfach angebbar. Macht den Lauf skriptfähig.",
        ),
    ] = None,
    alle: Annotated[
        bool,
        typer.Option("--all", help="Auch Werte durchgehen, die bereits gesetzt sind."),
    ] = False,
    auflisten: Annotated[
        bool, typer.Option("--list", help="Nur den Katalog zeigen und nichts ändern.")
    ] = False,
    pruefen_nur: Annotated[
        bool, typer.Option("--check", help="Nur prüfen, ob die Konfiguration trägt.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Katalog bzw. Prüfergebnis maschinenlesbar ausgeben.")
    ] = False,
) -> None:
    """Richtet alle Einstellungen ein — geführt, an einer Stelle (§6).

    Die Konfiguration ist bewusst breit: Sie soll ohne Codeänderung tragen, was eine
    Installation unterscheidet (§6.1 Regel 1). Der Preis sind rund hundert Werte in drei
    Dateien, verteilt auf zwei Wege — ``config/*.yaml`` für alles Fachliche, ``.env`` für alles,
    was Zugang, Adresse oder Geheimnis ist (§20.2). Dieses Kommando beantwortet die Frage
    "welcher Wert gehört wohin" und schreibt an die richtige Stelle.

    Wohin ein Wert gehört, wird **abgelesen und nicht festgelegt**: Steht im YAML ein
    ``${WG_...}``-Platzhalter, gehört er in die ``.env``; steht dort ein Literal, ins YAML.
    Geschrieben wird zeilenweise, damit die Kommentare stehen bleiben — sie sind in beiden
    Dateien der halbe Inhalt.

    Beispiele:

        wg setup                       geführt durch alles, was noch fehlt

        wg setup --all                 auch schon Gesetztes noch einmal durchgehen

        wg setup --set WG_API_TOKEN=geheim --set WG_EMBEDDING_DIM=768

        wg setup --list                den ganzen Katalog ansehen

        wg setup --check               nur prüfen, nichts schreiben
    """
    from wissensgraph.config.wizard import (
        baue_katalog,
        finde_yaml_zeile,
        lies_env,
        maskiert,
        pruefe_gesamt,
        schreibe_env,
        schreibe_yaml,
    )

    kern = _wizard_konfigdatei(config_file)
    env_pfad = dotenv_file or Path(".env")
    beispiel = Path(".env.example")
    if not kern.is_file():
        typer.echo(
            f"Es gibt keine Konfigurationsdatei unter '{kern}'. Der Assistent bearbeitet eine "
            f"vorhandene Datei — die Vorlage liegt im Repository unter 'config/'.",
            err=True,
        )
        raise typer.Exit(code=2)

    katalog = baue_katalog(
        env_beispiel=beispiel,
        config_datei=kern,
        weitere_yaml=[
            kern.parent / defaults.MODELS_CONFIG_FILENAME,
            kern.parent / defaults.SOURCES_CONFIG_FILENAME,
        ],
    )
    env_werte = lies_env(env_pfad)
    yaml_zeilen = kern.read_text(encoding="utf-8").splitlines()

    def aktueller_wert(eintrag: Any) -> tuple[str, str]:
        """Der geltende Wert und woher er kommt.

        Die Prozessumgebung zählt mit, und das ist im Container der Regelfall: Dort setzt
        docker-compose die DSNs, die Ports und den Broker selbst — in der ``.env`` steht davon
        nichts. Ein Assistent, der nur die Datei liest, hielte all das für unbesetzt und fragte
        nach Werten, die längst gesetzt sind (§6.2: Prozess-ENV schlägt die Datei).
        """
        if eintrag.ziel == "env":
            aus_datei = env_werte.get(eintrag.schluessel, "")
            if aus_datei:
                return aus_datei, "Datei"
            aus_umgebung = os.environ.get(eintrag.schluessel, "")
            return (aus_umgebung, "Umgebung") if aus_umgebung else ("", "")
        gefunden = finde_yaml_zeile(yaml_zeilen, eintrag.pfad)
        return ("", "") if gefunden is None else (gefunden[1], "Datei")

    # -- Nur zeigen ------------------------------------------------------------
    if auflisten:
        if as_json:
            # Ohne `_maschinenlesbar`: Dieser Zweig lädt nichts und protokolliert nichts, es
            # gibt also keine Logzeile, die die Ausgabe stören könnte. Die Umlenkung wäre eine
            # Nebenwirkung ohne Anlass.
            typer.echo(
                json.dumps(
                        [
                            {
                                "schluessel": item.schluessel,
                                "abschnitt": item.abschnitt,
                                "ziel": item.ziel,
                                "beschreibung": item.beschreibung,
                                "vorgabe": defaults.SECRET_MASK if item.geheim else item.vorgabe,
                                "gesetzt": bool(aktueller_wert(item)[0]),
                                "quelle": aktueller_wert(item)[1],
                                "pflicht": item.pflicht,
                                "auswahl": list(item.auswahl),
                            }
                            for item in katalog
                        ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        for name in katalog.abschnitte:
            typer.echo("")
            typer.echo(f"{name}")
            for item in katalog.im_abschnitt(name):
                typer.echo(_wizard_zeile(item, *aktueller_wert(item)))
        typer.echo("")
        typer.echo(f"{len(katalog)} Einstellungen; '!' markiert einen fehlenden Pflichtwert.")
        return

    # -- Nur prüfen ------------------------------------------------------------
    if pruefen_nur:
        befund = pruefe_gesamt(config_datei=kern, env={**os.environ, **env_werte})
        for zeile in befund.fehler:
            typer.echo(f"{_SYMBOLS[CheckStatus.FAIL]} {zeile}")
        for zeile in befund.hinweise:
            typer.echo(f"{_SYMBOLS[CheckStatus.WARN]} {zeile}")
        if befund.in_ordnung:
            typer.echo(f"{_SYMBOLS[CheckStatus.OK]} Die Konfiguration trägt.")
        raise typer.Exit(code=0 if befund.in_ordnung else 1)

    # -- Werte sammeln ---------------------------------------------------------
    neu_env: dict[str, str] = {}
    neu_yaml: dict[tuple[str, ...], str] = {}

    for zuweisung in setzen or []:
        name, trenner, wert = zuweisung.partition("=")
        if not trenner:
            typer.echo(f"'{zuweisung}' ist keine Zuweisung. Erwartet wird NAME=WERT.", err=True)
            raise typer.Exit(code=2)
        eintrag = katalog.get(name.strip())
        if eintrag is None:
            # Unbekannte Namen sind erlaubt und landen in der .env: Der Katalog kennt die
            # dokumentierten Variablen, nicht jede, die ein eigener Adapter mitbringt (§8.4).
            neu_env[name.strip()] = wert.strip()
            continue
        fehler = eintrag.pruefen(wert.strip())
        if fehler is not None:
            typer.echo(f"{name.strip()}: {fehler}", err=True)
            raise typer.Exit(code=2)
        if eintrag.ziel == "env":
            neu_env[eintrag.schluessel] = wert.strip()
        else:
            neu_yaml[eintrag.pfad] = wert.strip()

    # -- Geführt ---------------------------------------------------------------
    if not setzen:
        gewaehlt = tuple(abschnitt) if abschnitt else katalog.abschnitte
        unbekannt = [name for name in gewaehlt if name not in katalog.abschnitte]
        if unbekannt:
            typer.echo(f"Unbekannte Abschnitte: {', '.join(unbekannt)}", err=True)
            typer.echo("Bekannt sind:", err=True)
            for name in katalog.abschnitte:
                typer.echo(f"  {name}", err=True)
            raise typer.Exit(code=2)

        typer.echo("Einrichtung des Wissensgraphen (§6).")
        typer.echo(f"  Umgebung:      {env_pfad}")
        typer.echo(f"  Konfiguration: {kern}")
        typer.echo("")
        typer.echo("Enter übernimmt den bisherigen Wert, '-' leert ihn, Strg-C bricht ab.")

        # Kein Vorabtest auf ein Terminal: Antworten dürfen auch aus einer Pipe kommen, und
        # ein `isatty`-Test verböte gerade das. Bemerkt wird die fehlende Eingabe dort, wo sie
        # ausbleibt — click wirft dann dieselbe Ausnahme wie bei Strg-C, und welcher der beiden
        # Fälle vorliegt, sagt erst hier zuverlässig das Terminal.
        try:
            for name in gewaehlt:
                offen = [
                    item
                    for item in katalog.im_abschnitt(name)
                    if alle or not aktueller_wert(item)[0] or item.pflicht
                ]
                if not offen:
                    continue
                typer.echo("")
                typer.echo(f"-- {name} " + "-" * max(0, 74 - len(name)))
                for item in offen:
                    antwort = _wizard_frage(item, *aktueller_wert(item))
                    if antwort is None:
                        continue
                    if item.ziel == "env":
                        neu_env[item.schluessel] = antwort
                    else:
                        neu_yaml[item.pfad] = antwort
        except (typer.Abort, click.exceptions.Abort):
            if sys.stdin.isatty():
                typer.echo("")
                typer.echo("Abgebrochen; nichts geschrieben.")
                raise typer.Exit(code=1) from None
            typer.echo(
                "Die Eingabe ist zu Ende, bevor alle Fragen beantwortet waren. Für einen "
                "skriptfähigen Lauf '--set NAME=WERT' benutzen, zum Ansehen '--list'.",
                err=True,
            )
            raise typer.Exit(code=2) from None

    # -- Schreiben -------------------------------------------------------------
    if not neu_env and not neu_yaml:
        typer.echo("")
        typer.echo("Nichts geändert.")
        return

    typer.echo("")
    typer.echo("Diese Werte werden geschrieben:")
    for name in sorted(neu_env):
        typer.echo(f"  {env_pfad}: {name}={maskiert(name, neu_env[name]) or '(leer)'}")
    for pfad in sorted(neu_yaml):
        typer.echo(f"  {kern}: {'.'.join(pfad)}={neu_yaml[pfad]}")

    if not setzen and not _wizard_bestaetigen("Schreiben?"):
        typer.echo("Abgebrochen; nichts geschrieben.")
        return

    geschrieben = schreibe_env(env_pfad, neu_env, vorlage=beispiel) if neu_env else 0
    fehlend: list[str] = []
    if neu_yaml:
        zahl, fehlend = schreibe_yaml(kern, neu_yaml)
        geschrieben += zahl

    gewuenscht = len(neu_env) + len(neu_yaml)
    typer.echo("")
    typer.echo(
        f"{geschrieben} von {gewuenscht} Werten geändert"
        + ("." if geschrieben == gewuenscht else "; der Rest stand schon so da.")
    )
    for vermisst in fehlend:
        typer.echo(
            f"{_SYMBOLS[CheckStatus.WARN]} '{vermisst}' steht nicht in {kern} und wurde nicht "
            f"angelegt — eine angehängte Zeile ohne ihren Kommentar wäre schlechter als dieser "
            f"Hinweis.",
            err=True,
        )

    befund = pruefe_gesamt(config_datei=kern, env={**os.environ, **lies_env(env_pfad)})
    typer.echo("")
    for zeile in befund.fehler:
        typer.echo(f"{_SYMBOLS[CheckStatus.FAIL]} {zeile}")
    for zeile in befund.hinweise:
        typer.echo(f"{_SYMBOLS[CheckStatus.WARN]} {zeile}")
    if befund.in_ordnung:
        typer.echo(f"{_SYMBOLS[CheckStatus.OK]} Die Konfiguration trägt. Weiter mit 'wg doctor'.")
    raise typer.Exit(code=0 if befund.in_ordnung else 1)


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
    from wissensgraph.domain.references import SourceReference
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
        # ``--link`` nennt nur ein Ziel; von Hand gesetzte Verweise sind gewöhnliche Referenzen.
        # Eine typisierte Beziehung entsteht aus einem Quellsystem, nicht von der Kommandozeile.
        references=tuple(SourceReference(target=ziel) for ziel in (link or ())),
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
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    granularity: Annotated[
        str,
        typer.Option(
            "--granularity",
            help="auto | cluster | document — erst Cluster, dann Dokumente (§12.4).",
        ),
    ] = defaults.SEARCH_GRANULARITY_AUTO,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Zweistufige Suche: erst Cluster, dann Dokumente (§12.4).

    Der Modus steht im Ergebnis. Ohne verfügbares Embedding-Modell ist er ``lexical`` — ein
    stiller Qualitätsverlust ohne Hinweis wäre die schlechtere Variante.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with _maschinenlesbar() if as_json else nullcontext(), Runtime(settings) as runtime:
        ergebnis = runtime.graph.search(query, store=store, limit=limit, granularity=granularity)

    if as_json:
        typer.echo(json.dumps(ergebnis.as_dict(), indent=2, ensure_ascii=False))
        return
    typer.echo(f"{len(ergebnis.hits)} Treffer (Modus '{ergebnis.mode}'):")
    for hit in ergebnis.hits:
        typer.echo(
            f"  {hit.score:6.4f}  {hit.concept.id} ({hit.concept.type}) — "
            f"{hit.concept.title or '—'}"
        )


ScopeOption = Annotated[str, typer.Option("--scope", help="Zu bearbeitender Scope (§6.3).")]


def _lauf_ausgeben(run: object, *, as_json: bool) -> None:
    """Ein Lauf der semantischen Schicht: als JSON oder als Zeilen mit seinen Zählern."""
    from wissensgraph.domain.runs import Run, RunStatus

    assert isinstance(run, Run)
    if as_json:
        typer.echo(json.dumps(run.as_dict(), indent=2, ensure_ascii=False))
        return

    zustand = CheckStatus.OK if run.status is RunStatus.SUCCEEDED else CheckStatus.FAIL
    dauer = f"{run.duration_seconds:.1f}s" if run.duration_seconds is not None else "—"
    typer.echo(f"{_SYMBOLS[zustand]} {run.kind} ({dauer})")
    if run.error:
        typer.echo(f"       {run.error}")
    for name, wert in run.stats.items():
        if wert not in (0, False, "", [], None):
            typer.echo(f"       {name}: {wert}")


@app.command("embed")
def embed(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    scope: ScopeOption = "",
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Alles neu einbetten — nach einem Modellwechsel (§11.7)."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Berechnet die fehlenden Embeddings eines Scopes (§13.1, §19).

    Idempotent: Ein zweiter Lauf über einen unveränderten Bestand kostet keinen einzigen Token —
    verglichen wird der gespeicherte ``source_hash`` mit dem aktuellen ``content_hash``.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        run = runtime.run_embed(_scope_pruefen(settings, scope), rebuild=rebuild)
    _lauf_ausgeben(run, as_json=as_json)


@app.command("cluster")
def cluster(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    scope: ScopeOption = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Gruppieren und zählen, nichts schreiben.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Bildet Cluster aus den Embeddings eines Scopes (§13.2, §19).

    Eine Zuordnung wird erst geschrieben, wenn sie ``clustering.stability_runs`` Läufe überlebt
    hat (§13.3) — der erste Lauf legt also Cluster an, ohne Mitglieder zu verknüpfen.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        run = runtime.run_cluster(_scope_pruefen(settings, scope), dry_run=dry_run)
    _lauf_ausgeben(run, as_json=as_json)


@app.command("relations")
def relations(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    scope: ScopeOption = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Fragen stellen, nichts schreiben.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Erkennt typisierte Beziehungen in den stabilen Clustern eines Scopes (§14, §19).

    "Keine Beziehung" ist die erwartete Mehrheitsantwort; ein Lauf mit wenigen neuen Kanten ist
    der Regelfall und kein Fehlschlag.
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        run = runtime.run_relations(_scope_pruefen(settings, scope), dry_run=dry_run)
    _lauf_ausgeben(run, as_json=as_json)


@app.command("link-orphans")
def link_orphans(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    scope: ScopeOption = "",
    loose_threshold: Annotated[int | None, typer.Option("--loose-threshold")] = None,
    proximity_top_n: Annotated[int | None, typer.Option("--proximity-top-n")] = None,
    proximity_auto_commit: Annotated[float | None, typer.Option("--proximity-auto-commit")] = None,
    proximity_candidate_band: Annotated[
        float | None, typer.Option("--proximity-candidate-band")
    ] = None,
    use_llm: Annotated[
        bool | None, typer.Option("--use-llm/--no-use-llm", help="Ob Stufe 2 läuft (§15.3).")
    ] = None,
    cluster_suggestion_limit: Annotated[
        int | None, typer.Option("--cluster-suggestion-limit")
    ] = None,
    cluster_preview_members: Annotated[
        int | None, typer.Option("--cluster-preview-members")
    ] = None,
    min_confidence: Annotated[float | None, typer.Option("--min-confidence")] = None,
    patterns: Annotated[
        list[Path] | None,
        typer.Option("--text-match-patterns", help="Musterdateien für §15.2a."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Nur berichten, nichts schreiben (§15.4).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Vernetzt lose Knoten in zwei Stufen — erst Code, dann Modell (§15, §19).

    Jeder Parameter aus §15.4 ist hier überschreibbar; ohne Angabe gilt der Wert aus
    ``config/wissensgraph.yaml`` (§6.2).
    """
    from wissensgraph.runtime import OrphanRequest, Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    request = OrphanRequest(
        scope=_scope_pruefen(settings, scope),
        loose_threshold=loose_threshold,
        proximity_top_n=proximity_top_n,
        proximity_auto_commit=proximity_auto_commit,
        proximity_candidate_band=proximity_candidate_band,
        use_llm=use_llm,
        cluster_suggestion_limit=cluster_suggestion_limit,
        cluster_preview_members=cluster_preview_members,
        min_confidence=min_confidence,
        pattern_files=tuple(str(pfad) for pfad in patterns or ()),
        dry_run=dry_run,
    )
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        run = runtime.run_orphans(request)
    _lauf_ausgeben(run, as_json=as_json)
    if dry_run and not as_json:
        typer.echo(f"{_SYMBOLS[CheckStatus.WARN]} Trockenlauf: nichts geschrieben.")


def _scope_pruefen(settings: Settings, scope: str) -> str:
    """Beendet die CLI verständlich, wenn der Scope fehlt oder unbekannt ist (§6.5)."""
    bekannt = [item.name for item in settings.scopes]
    if not scope:
        typer.echo(f"--scope ist Pflicht. Konfiguriert sind: {', '.join(bekannt)}.", err=True)
        raise typer.Exit(code=2)
    if scope not in bekannt:
        typer.echo(
            f"Unbekannter Scope '{scope}'. Konfiguriert sind: {', '.join(bekannt)}.", err=True
        )
        raise typer.Exit(code=2)
    return scope


@models_app.command("describe")
def models_describe(
    task: Annotated[
        str | None,
        typer.Argument(help="Eine einzelne Aufgabe; ohne Angabe alle konfigurierten."),
    ] = None,
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Zeigt, welches Modell für welche Aufgabe greifen würde (§11.2, §19).

    Ruft kein Modell auf. Das Kommando beantwortet genau die Frage, die ein Modellwechsel
    aufwirft — "wirkt die Änderung in models.yaml?" —, und zwar ohne einen einzigen Token.
    """
    from wissensgraph.config.models import UnknownTaskError
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        try:
            routen = runtime.router.routes() if task is None else (runtime.router.describe(task),)
        except UnknownTaskError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(json.dumps([route.as_dict() for route in routen], indent=2, ensure_ascii=False))
        return

    if not routen:
        typer.echo("Keine Task-Profile konfiguriert. Sie stehen in config/models.yaml (§11.3).")
        return
    for route in routen:
        # 'configured' sagt nur, ob das Nötigste dasteht — nicht, ob der Schlüssel gilt. Ein
        # Startvorgang, der jeden Anbieter anspräche, verbrauchte Token für eine Frage, die
        # niemand gestellt hat.
        zustand = CheckStatus.OK if route.configured else CheckStatus.WARN
        ort = "lokal" if route.local else "extern"
        typer.echo(f"{_SYMBOLS[zustand]} {route.task:<20} {route.model_key} ({ort})")
        if route.dim is not None:
            typer.echo(f"         Dimension {route.dim}, Bündel zu {route.batch_size}")
        if route.endpoint:
            # Nur bei Vertex belegt. Der Endpunkt folgt aus dem Standort, und aus 'eu' wird ein
            # anderer Ort der Verarbeitung als aus 'europe-west4' — sichtbar ist das nur hier.
            typer.echo(f"         Endpunkt {route.endpoint}")
        if route.fallbacks:
            typer.echo(f"         Fallback: {', '.join(route.fallbacks)}")
        if not route.configured:
            typer.echo("         Zugangsdaten fehlen — siehe .env.example (§11.4).")


@models_app.command("usage")
def models_usage(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    models_file: ModelsFileOption = None,
    store: StoreOption = defaults.STORE_SHARED,
    limit: Annotated[int, typer.Option("--limit")] = defaults.MODEL_USAGE_LIMIT,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Wertet ``model_calls`` aus: Aufrufe, Cache-Treffer, Token und geschätzte Kosten (§11.6).

    Je Store, nicht über beide: Ein Aufruf wird dort verbucht, wo der Inhalt herkommt. Was der
    persönliche Store gekostet hat, steht deshalb nur in seiner eigenen Abrechnung
    (Leitprinzip 2).
    """
    from wissensgraph.runtime import Runtime

    settings = _load(config_file, service="cli", dotenv_file=dotenv_file)
    with (
        _maschinenlesbar() if as_json else nullcontext(),
        Runtime(settings, models_file=models_file) as runtime,
    ):
        zeilen = runtime.router.usage(store=store, limit=limit)

    if as_json:
        typer.echo(json.dumps([zeile.as_dict() for zeile in zeilen], indent=2, ensure_ascii=False))
        return

    if not zeilen:
        typer.echo(f"Keine Modellaufrufe im Store '{store}' verbucht.")
        return
    typer.echo(f"{'Aufgabe':<20} {'Modell':<34} {'Aufr.':>6} {'Cache':>6} {'Token':>9} {'EUR':>9}")
    for zeile in zeilen:
        typer.echo(
            f"{zeile.task:<20} {zeile.provider + ':' + zeile.model:<34} "
            f"{zeile.calls:>6} {zeile.cache_hits:>6} "
            f"{zeile.tokens_in + zeile.tokens_out:>9} {zeile.cost_estimate_eur:>9.4f}"
        )
        if zeile.failures:
            typer.echo(f"{'':<20} davon nicht erfolgreich oder verhindert: {zeile.failures}")


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


@app.command("mcp")
def mcp(
    config_file: ConfigFileOption = None,
    dotenv_file: DotenvFileOption = None,
    session: Annotated[
        str,
        typer.Option(
            "--session",
            help="Kennung der Agenten-Sitzung; steht als 'agent:<session>' im Journal (§18.3).",
        ),
    ] = defaults.MCP_DEFAULT_SESSION,
    transport: Annotated[
        str | None,
        typer.Option(
            "--transport",
            help="'http' (Vorgabe aus der Konfiguration) oder 'stdio'.",
        ),
    ] = None,
    host: Annotated[str | None, typer.Option("--host", help="Bind-Adresse bei HTTP.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Port bei HTTP.")] = None,
) -> None:
    """Startet den MCP-Server — der Startbefehl des mcp-Containers (§5.1, §18).

    Zwei Transporte, dieselben Werkzeuge: ``http`` öffnet einen Port, unter dem ein Agent den
    Server auch dann erreicht, wenn er ihn nicht selbst gestartet hat; ``stdio`` bleibt für den
    Fall, dass er als Unterprozess eingebunden wird. Der Server kennt **keine**
    Authentifizierung — wer ihn weiter als an Loopback bindet, stellt ihn in ein Netz, das
    selbst abgesichert sein muss (§20.3).

    Auf dem geteilten Store hält der Server ausschließlich eine nur lesende Verbindung; ein
    Schreibversuch dorthin scheitert in der Datenbank und nicht erst an einer Prüfung (§18.3).
    """
    import asyncio

    from wissensgraph.mcp.server import serve_http, serve_stdio

    settings = _load(config_file, service="mcp", dotenv_file=dotenv_file)
    gewaehlt = transport or settings.mcp.transport
    if gewaehlt not in ("http", "stdio"):
        typer.echo(
            f"Unbekannter Transport '{gewaehlt}'; erlaubt sind 'http' und 'stdio'.", err=True
        )
        raise typer.Exit(code=2)
    if host is not None or port is not None:
        settings = settings.model_copy(
            update={
                "mcp": settings.mcp.model_copy(
                    update={
                        "host": host if host is not None else settings.mcp.host,
                        "port": port if port is not None else settings.mcp.port,
                    }
                )
            }
        )

    try:
        if gewaehlt == "stdio":
            asyncio.run(serve_stdio(settings, session=session))
        else:
            typer.echo(
                f"MCP-Server auf http://{settings.mcp.host}:{settings.mcp.port}"
                f"{settings.mcp.path} (ohne Authentifizierung).",
                err=True,
            )
            asyncio.run(serve_http(settings, session=session))
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except KeyboardInterrupt:  # pragma: no cover — nur beim Beenden von Hand
        typer.echo("MCP-Server beendet.", err=True)


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
