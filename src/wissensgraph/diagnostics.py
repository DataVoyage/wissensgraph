"""Selbstprüfung des Systems — die Grundlage von ``wg doctor`` (§19).

``wg doctor`` beantwortet die Frage "läuft hier alles so, wie es soll?" an einer Stelle statt über
fünf einzelne Kommandos. In Stufe 0 prüft es Konfiguration und Datenbankverbindungen; mit den
folgenden Stufen kommen Provider (Stufe 7) und Quell-Adapter (Stufe 3) hinzu — die Struktur ist
darauf angelegt, dass eine neue Prüfung nur ein weiterer Eintrag in der Liste ist.

Die Prüfungen sind bewusst von der Ausgabe getrennt: Diese Datei liefert Daten, die CLI formatiert
sie. So ist dieselbe Prüfung später auch über die API abrufbar, ohne dass Text geparst wird.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, SQLAlchemyError

from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigError
from wissensgraph.config.masking import mask_dsn
from wissensgraph.config.models import load_models
from wissensgraph.config.network import is_local_dsn
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import load_sources
from wissensgraph.infrastructure.adapters import AdapterRegistry
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.introspection import constraint_exists, vector_dimension
from wissensgraph.infrastructure.db.migrations import (
    build_options,
    current_revision,
    head_revision,
)


class CheckStatus(StrEnum):
    """Ergebnis einer einzelnen Prüfung."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """Das Ergebnis einer Prüfung mit ihrer Begründung."""

    name: str
    status: CheckStatus
    detail: str
    context: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Ob die Prüfung ohne Fehler durchlief. Warnungen gelten als nicht fehlerhaft."""
        return self.status is not CheckStatus.FAIL


@dataclass(frozen=True)
class DiagnosticsReport:
    """Alle Prüfergebnisse eines ``doctor``-Laufs."""

    results: tuple[CheckResult, ...]

    @property
    def healthy(self) -> bool:
        """Ob keine Prüfung fehlgeschlagen ist."""
        return all(result.ok for result in self.results)

    @property
    def exit_code(self) -> int:
        """Rückgabewert für die CLI: 0 bei Erfolg, 1 bei mindestens einem Fehler."""
        return 0 if self.healthy else 1

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form, z. B. für eine spätere Betriebsansicht in der UI (§17.2)."""
        return {
            "healthy": self.healthy,
            "checks": [
                {
                    "name": result.name,
                    "status": str(result.status),
                    "detail": result.detail,
                    "context": result.context,
                }
                for result in self.results
            ],
        }


def check_configuration(settings: Settings) -> CheckResult:
    """Bestätigt, dass eine gültige Konfiguration vorliegt.

    Dass diese Funktion überhaupt aufgerufen wird, ist bereits das Ergebnis: Eine ungültige
    Konfiguration hätte den Prozess vorher abgebrochen (§6.5).
    """
    return CheckResult(
        name="konfiguration",
        status=CheckStatus.OK,
        detail=(
            f"{len(settings.scopes)} Scopes in {len(settings.stores)} Stores, "
            f"{len(settings.concept_types)} Konzepttypen, Umgebung '{settings.env}'."
        ),
        context={"env": settings.env, "embedding_dim": settings.embedding_dim},
    )


def check_personal_locality(settings: Settings) -> CheckResult:
    """Prüft Leitprinzip 2: Der ``personal``-Store bleibt lokal.

    Die Schema-Validierung erzwingt das bereits, wenn ``allow_remote = false`` ist. Diese Prüfung
    fängt den anderen Fall ab: jemand hat ``allow_remote`` auf ``true`` gesetzt und damit die
    Schutzregel bewusst abgeschaltet. Das ist erlaubt, soll aber sichtbar sein.
    """
    personal = settings.stores.get("personal")
    if personal is None:
        return CheckResult(
            name="personal_lokal",
            status=CheckStatus.WARN,
            detail="Kein 'personal'-Store konfiguriert.",
        )
    if not is_local_dsn(personal.dsn):
        return CheckResult(
            name="personal_lokal",
            status=CheckStatus.WARN,
            detail=(
                "Der personal-Store liegt auf einem entfernten Host. Leitprinzip 2 ist damit "
                "bewusst abgeschaltet (allow_remote=true)."
            ),
            context={"dsn": mask_dsn(personal.dsn)},
        )
    return CheckResult(
        name="personal_lokal",
        status=CheckStatus.OK,
        detail="Der personal-Store liegt lokal.",
        context={"dsn": mask_dsn(personal.dsn)},
    )


def check_model_policy(settings: Settings) -> CheckResult:
    """Meldet, ob persönliche Inhalte an nicht-lokale Modelle gehen dürfen (§11.5)."""
    if settings.personal_allow_remote_models:
        return CheckResult(
            name="modell_policy",
            status=CheckStatus.WARN,
            detail=(
                "WG_PERSONAL_ALLOW_REMOTE_MODELS=true: persönliche Inhalte dürfen an "
                "nicht-lokale Provider gesendet werden. Leitprinzip 2 ist insoweit aufgeweicht."
            ),
        )
    return CheckResult(
        name="modell_policy",
        status=CheckStatus.OK,
        detail="Persönliche Inhalte gehen nur an als lokal deklarierte Provider.",
    )


def check_models(settings: Settings, path: Path | None = None) -> tuple[CheckResult, ...]:
    """Prüft die Router-Konfiguration: ladbar, vollständig, mit Zugangsdaten (§11.4, §11.7).

    Die Prüfung ruft kein Modell auf. Sie beantwortet, was sich ohne Netz beantworten lässt —
    und das ist mehr, als es zunächst scheint: Ob ein Task-Profil auf einen unbekannten Provider
    zeigt, ob die Vektordimension zum migrierten Schema passt, und ob überhaupt Zugangsdaten
    dastehen. Der erste echte Aufruf soll an keiner dieser drei Fragen scheitern.

    Ein Anbieter ohne Schlüssel ist eine **Warnung** und kein Fehler: Ein System ohne Modell ist
    ein zulässiger Zustand (§11.5) — der Kernspace funktioniert dann über Kanten und lexikalische
    Suche.
    """
    try:
        models = load_models(settings, path=path)
    except ConfigError as exc:
        return (
            CheckResult(
                name="modelle",
                status=CheckStatus.FAIL,
                detail=str(exc).splitlines()[0][:300],
            ),
        )

    if not models.tasks:
        return (
            CheckResult(
                name="modelle",
                status=CheckStatus.WARN,
                detail=(
                    "Keine Task-Profile konfiguriert (config/models.yaml). Embedding, Clustering "
                    "und Kantenerkennung stehen damit nicht zur Verfügung."
                ),
            ),
        )

    ergebnisse: list[CheckResult] = []
    for name in sorted(models.tasks):
        route = models.tasks[name].primary
        provider = models.providers[route.provider]
        fehlt = not provider.is_configured
        ergebnisse.append(
            CheckResult(
                name=f"modell:{name}",
                status=CheckStatus.WARN if fehlt else CheckStatus.OK,
                detail=(
                    f"{route.model_key} ({'lokal' if provider.local else 'extern'})"
                    + (" — Zugangsdaten fehlen." if fehlt else ".")
                ),
                context={"provider": route.provider, "model": route.model, "local": provider.local},
            )
        )
    return tuple(ergebnisse)


def check_api_exposure(settings: Settings) -> CheckResult:
    """Warnt vor einer unabgesicherten, nicht nur lokal gebundenen API (§20.3)."""
    if settings.api.auth_mode == "none":
        return CheckResult(
            name="api_absicherung",
            status=CheckStatus.WARN,
            detail=f"auth_mode='none' — nur an {settings.api.host} gebunden, ohne Token.",
        )
    return CheckResult(
        name="api_absicherung",
        status=CheckStatus.OK,
        detail=f"auth_mode='{settings.api.auth_mode}'.",
    )


def check_stores(registry: StoreRegistry) -> tuple[CheckResult, ...]:
    """Prüft die Verbindung zu jedem konfigurierten Store."""
    results = []
    for health in registry.check_all():
        results.append(
            CheckResult(
                name=f"store:{health.store}",
                status=CheckStatus.OK if health.healthy else CheckStatus.FAIL,
                detail=(
                    "erreichbar"
                    if health.healthy
                    else f"nicht erreichbar: {health.detail or 'unbekannter Fehler'}"
                ),
                context={"dsn": health.dsn},
            )
        )
    return tuple(results)


#: Tabelle und Spalte, die die Vektordimension des Schemas tragen (§7.4).
_EMBEDDING_TABLE = "concept_embeddings"
_EMBEDDING_COLUMN = "embedding"


def check_schema(settings: Settings, registry: StoreRegistry) -> tuple[CheckResult, ...]:
    """Prüft je Store, ob die Migrationen durch sind und das Schema zur Konfiguration passt.

    Zwei getrennte Fragen, die leicht verwechselt werden: Ein Store kann auf der neuesten Revision
    stehen und trotzdem nicht zur Konfiguration passen — nämlich dann, wenn ``WG_EMBEDDING_DIM``
    nach der Migration geändert wurde. Die Dimension steht als ``vector(n)`` im Schema und ändert
    sich nicht dadurch, dass eine Umgebungsvariable einen anderen Wert bekommt. Ohne diese Prüfung
    fällt der Widerspruch erst beim ersten Embedding-Lauf auf (§11.7).
    """
    head = head_revision()
    return tuple(
        _check_store_schema(settings, registry, store, head) for store in registry.store_names
    )


def _check_store_schema(
    settings: Settings, registry: StoreRegistry, store: str, head: str | None
) -> CheckResult:
    """Die Schemaprüfung eines einzelnen Stores."""
    name = f"schema:{store}"
    try:
        options = build_options(settings, store, registry)
        with registry.engine(store).connect() as connection:
            if connection.dialect.name != "postgresql":
                return CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    detail=(
                        f"Schemaprüfung übersprungen: Dialekt '{connection.dialect.name}' ist "
                        f"keine PostgreSQL-Datenbank."
                    ),
                )
            revision = current_revision(connection, options)
            dimension = vector_dimension(connection, _EMBEDDING_TABLE, _EMBEDDING_COLUMN)
    except SQLAlchemyError as exc:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"Migrationsstand nicht feststellbar: {str(exc).splitlines()[0][:200]}",
        )

    context: dict[str, object] = {"revision": revision, "head": head, "embedding_dim": dimension}

    if revision is None:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail="Nicht migriert. 'wg migrate' ausführen.",
            context=context,
        )
    if revision != head:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"Migrationen stehen aus: Store auf '{revision}', erwartet '{head}'.",
            context=context,
        )
    if dimension is not None and dimension != settings.embedding_dim:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=(
                f"Das Schema führt vector({dimension}), konfiguriert ist "
                f"{settings.embedding_dim}. WG_EMBEDDING_DIM wurde nach der Migration geändert; "
                f"die Embeddings müssen neu aufgebaut werden (§11.7)."
            ),
            context=context,
        )
    return CheckResult(
        name=name,
        status=CheckStatus.OK,
        detail=f"Revision '{revision}', vector({dimension}).",
        context=context,
    )


def check_sources(settings: Settings, path: Path | None = None) -> tuple[CheckResult, ...]:
    """Prüft die konfigurierten Quellen: auffindbar, konfigurierbar, erreichbar (§8.3, §19).

    Drei Ergebnisse sind möglich, und die Unterscheidung ist genau die aus §8.3 und §6.5:

    * Die Quellkonfiguration ist fehlerhaft oder ein Adapter nicht auffindbar — ein **Fehler**.
      Das ist ein Konfigurationsproblem und kein Betriebszustand (§6.5, letzter Punkt).
    * Eine Quelle ist konfiguriert, aber nicht erreichbar — ein **Fehler** je Quelle, der den
      Rest des Berichts nicht berührt. Ein ausgefallenes Confluence sagt nichts über Jira.
    * Keine Quellen konfiguriert — eine **Warnung**. Zulässig (etwa im Profil ``minimal``), aber
      nichts, was man versehentlich haben will.
    """
    try:
        sources = load_sources(settings, path=path)
        registered = AdapterRegistry().build_all(sources)
    except ConfigError as exc:
        return (
            CheckResult(
                name="quellen",
                status=CheckStatus.FAIL,
                detail=str(exc).splitlines()[0][:300],
            ),
        )

    if not registered:
        return (
            CheckResult(
                name="quellen",
                status=CheckStatus.WARN,
                detail="Keine eingeschaltete Quelle konfiguriert (config/sources.yaml).",
            ),
        )

    return tuple(
        CheckResult(
            name=f"quelle:{item.name}",
            status=CheckStatus.OK if item.usable else CheckStatus.FAIL,
            detail=f"{item.health.state}: {item.health.detail}",
            context={"adapter": item.config.adapter, "scope": item.config.target.scope},
        )
        for item in registered
    )


def check_store_separation(registry: StoreRegistry) -> tuple[CheckResult, ...]:
    """Prüft die Store-Trennung dort, wo sie wirkt: in der Datenbank (§20.1, §12.1).

    Drei Fragen je Store, und alle drei lassen sich nur *am laufenden System* beantworten — eine
    Konfiguration, die richtig aussieht, sagt über eine Datenbank nichts:

    1. Steht ``ck_shared_no_personal_ref`` dort, wo er hingehört, und nur dort? Der Constraint
       gehört in den geteilten Store und ausdrücklich **nicht** in den persönlichen: Dort wäre er
       das Verbot der Brücke, die §7.3 gerade will.
    2. Gibt es trotzdem Kanten über die Grenze, die es nicht geben dürfte? Der Constraint gilt für
       neue Zeilen; eine Datenbank, die vor seiner Einführung befüllt wurde, könnte alte tragen.
    3. Ist der nur lesende Zugang wirklich nur lesend (§20.1, Guard 5)?

    Ein Fehlschlag ist hier ein **Fehler** und keine Warnung. Bei allem anderen kostet ein
    Fehlalarm Aufmerksamkeit; hier kostet ein übersehener Befund die Datenschutzgrenze.
    """
    return tuple(_check_store_separation_one(registry, store) for store in registry.store_names)


def _check_store_separation_one(registry: StoreRegistry, store: str) -> CheckResult:
    """Die Trennungsprüfung eines einzelnen Stores."""
    name = f"store_trennung:{store}"
    ist_geteilt = store == defaults.STORE_SHARED
    try:
        with registry.engine(store).connect() as connection:
            if connection.dialect.name != "postgresql":
                return CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    detail=f"Übersprungen: Dialekt '{connection.dialect.name}'.",
                )
            constraint = constraint_exists(connection, "edges", "ck_shared_no_personal_ref")
            ueber_grenze = connection.execute(
                text("SELECT count(*) FROM edges WHERE from_store <> :store OR to_store <> :store"),
                {"store": store},
            ).scalar_one()
        nur_lesend = _pruefe_nur_lesend(registry, store)
    except SQLAlchemyError as exc:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"Nicht prüfbar: {str(exc).splitlines()[0][:200]}",
        )

    context: dict[str, object] = {
        "ck_shared_no_personal_ref": constraint,
        "kanten_ueber_die_grenze": ueber_grenze,
        "nur_lesender_zugang": nur_lesend,
    }

    if constraint is not ist_geteilt:
        fehlt = "fehlt" if ist_geteilt else "steht hier, gehört aber nicht hierher"
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"'ck_shared_no_personal_ref' {fehlt}. 'wg migrate' ausführen (§7.4).",
            context=context,
        )
    if ist_geteilt and ueber_grenze:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=(
                f"{ueber_grenze} Kante(n) im geteilten Store verweisen über die Store-Grenze. "
                f"Der geteilte Store darf nicht wissen, dass es persönliche Konzepte gibt "
                f"(§12.1)."
            ),
            context=context,
        )
    if not nur_lesend:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=(
                "Über den als nur lesend gedachten Zugang lässt sich schreiben (§20.1, Guard 5)."
            ),
            context=context,
        )
    bruecken = "" if ist_geteilt else f", {ueber_grenze} Brücke(n) nach außen"
    return CheckResult(
        name=name,
        status=CheckStatus.OK,
        detail=f"Grenze gewahrt, lesender Zugang schreibgeschützt{bruecken}.",
        context=context,
    )


def _pruefe_nur_lesend(registry: StoreRegistry, store: str) -> bool:
    """Versucht über den lesenden Zugang zu schreiben — und erwartet einen Datenbankfehler.

    Der Versuch geht auf eine Tabelle, die es gibt, und schreibt einen Wert, den es nicht geben
    darf. Gelingt er wider Erwarten, wird zurückgerollt: Eine Diagnose darf nichts hinterlassen,
    auch dann nicht, wenn sie einen Missstand feststellt.
    """
    try:
        with registry.readonly_engine(store).connect() as connection:
            transaktion = connection.begin()
            try:
                connection.execute(
                    text(
                        "INSERT INTO runs (id, kind, params, status, progress, stats) "
                        "VALUES (gen_random_uuid(), 'export', '{}', 'queued', 0, '{}')"
                    )
                )
            finally:
                transaktion.rollback()
    except SQLAlchemyError:
        return True
    return False


def check_broker(settings: Settings) -> CheckResult:
    """Prüft den Broker, über den asynchrone Läufe angestoßen werden (§5.1, §16.3).

    Das Ergebnis ist nie ein Fehler, und das ist eine bewusste Abwägung: Ohne Broker fallen die
    *asynchronen* Läufe aus — ``POST /runs/sync`` und der ``worker``. Alles Synchrone,
    einschließlich ``wg sync``, funktioniert unverändert. Im Profil ``minimal`` läuft gar kein
    Broker (§5.4); ein Fehler wäre dort ein Fehlalarm, und ein Diagnosewerkzeug, das im
    Regelbetrieb Fehlalarme gibt, wird nicht mehr gelesen.
    """
    if not settings.broker_url:
        return CheckResult(
            name="broker",
            status=CheckStatus.WARN,
            detail=(
                "Kein Broker konfiguriert (WG_BROKER_URL). Läufe über 'wg sync' laufen "
                "synchron; asynchrone Läufe über Queue und Worker sind nicht möglich."
            ),
        )

    from wissensgraph.infrastructure.queue import RedisJobQueue

    queue = RedisJobQueue(settings.broker_url)
    try:
        wartend = queue.size()
    except Exception as exc:  # Redis wirft je nach Ursache sehr verschiedene Typen.
        return CheckResult(
            name="broker",
            status=CheckStatus.WARN,
            detail=f"Broker nicht erreichbar: {type(exc).__name__}: {str(exc)[:150]}",
        )
    finally:
        queue.close()

    return CheckResult(
        name="broker",
        status=CheckStatus.OK,
        detail=f"erreichbar, {wartend} wartende(r) Job(s).",
        context={"pending": wartend},
    )


def check_agent_readonly(registry: StoreRegistry) -> CheckResult:
    """Prüft, ob die Agenten-Verbindung auf ``shared`` wirklich nur lesen kann (§18.3, §20.1).

    Der fünfte Guard aus §20.1 im Betrieb: Geprüft wird nicht, ob der Code die Regel kennt,
    sondern ob die *Verbindung* sie durchsetzt. Dafür wird ein Schreibversuch unternommen und
    erwartet, dass er scheitert — und alles wird anschließend zurückgerollt.

    Ein **Fehler** und keine Warnung, wenn der Versuch durchgeht: Ein Agent, der den geteilten
    Store beschreiben kann, verletzt §17.4 in seinem Kern.
    """
    konfiguration = registry.config_of(defaults.STORE_SHARED)
    dsn = konfiguration.readonly_dsn or konfiguration.dsn
    if not dsn.startswith("postgresql"):
        # Die Zusicherung hängt an ``default_transaction_read_only`` bzw. an einer eigenen
        # Datenbankrolle — beides gibt es nur in PostgreSQL. Auf SQLite (Tests, Werkzeuge) ist
        # sie schlicht nicht vorhanden, und das als "in Ordnung" zu melden wäre die gefährlichere
        # Auskunft.
        return CheckResult(
            name="agent-readonly",
            status=CheckStatus.WARN,
            detail=(
                "Der Store 'shared' läuft nicht auf PostgreSQL; eine nur lesende Verbindung "
                "lässt sich dort nicht erzwingen (§18.3). Im Betrieb ist das ein Fehler."
            ),
        )

    try:
        with registry.readonly_engine(defaults.STORE_SHARED).connect() as connection:
            transaktion = connection.begin()
            try:
                connection.execute(text("CREATE TEMP TABLE wg_schreibprobe (x int)"))
            except DatabaseError:
                return CheckResult(
                    name="agent-readonly",
                    status=CheckStatus.OK,
                    detail=(
                        "Die Agenten-Verbindung auf 'shared' weist Schreibversuche in der "
                        "Datenbank ab (§18.3)."
                    ),
                )
            finally:
                transaktion.rollback()
    except SQLAlchemyError as exc:
        return CheckResult(
            name="agent-readonly",
            status=CheckStatus.WARN,
            detail=f"Nicht prüfbar: {str(exc).splitlines()[0][:200]}",
        )

    return CheckResult(
        name="agent-readonly",
        status=CheckStatus.FAIL,
        detail=(
            "Die Agenten-Verbindung auf 'shared' konnte schreiben. Ein Agent darf die geteilte "
            "Struktur nicht verändern (§17.4); prüfe 'stores.shared.readonly_dsn'."
        ),
    )


def run_diagnostics(settings: Settings, registry: StoreRegistry) -> DiagnosticsReport:
    """Führt alle Prüfungen aus und fasst sie zu einem Bericht zusammen."""
    settings_checks: tuple[Callable[[Settings], CheckResult], ...] = (
        check_configuration,
        check_personal_locality,
        check_model_policy,
        check_api_exposure,
    )
    results = [check(settings) for check in settings_checks]
    results.extend(check_stores(registry))
    results.extend(check_schema(settings, registry))
    results.extend(check_store_separation(registry))
    results.extend(check_models(settings))
    results.extend(check_sources(settings))
    results.append(check_agent_readonly(registry))
    results.append(check_broker(settings))
    return DiagnosticsReport(results=tuple(results))
