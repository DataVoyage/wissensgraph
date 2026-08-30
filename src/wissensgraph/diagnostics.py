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

from sqlalchemy.exc import SQLAlchemyError

from wissensgraph.config.errors import ConfigError
from wissensgraph.config.masking import mask_dsn
from wissensgraph.config.network import is_local_dsn
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import load_sources
from wissensgraph.infrastructure.adapters import AdapterRegistry
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.introspection import vector_dimension
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
    results.extend(check_sources(settings))
    return DiagnosticsReport(results=tuple(results))
