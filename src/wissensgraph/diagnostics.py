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

from wissensgraph.config.masking import mask_dsn
from wissensgraph.config.network import is_local_dsn
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry


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
    return DiagnosticsReport(results=tuple(results))
