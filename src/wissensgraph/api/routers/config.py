"""Endpunkt für die aufgelöste Konfiguration (§6.1 Regel 5, §16.2).

``GET /api/v1/config/effective`` beantwortet die Frage, mit der jede Fehlersuche in einem
Container-Setup beginnt: *Womit läuft dieser Prozess eigentlich?* Ohne diesen Endpunkt bleibt nur
Raten über die Präzedenzkette aus YAML, ``.env`` und Prozessumgebung.

Die Antwort ist vollständig maskiert (§20.2). Sie ist deshalb auch für die UI unbedenklich, die
ihre Fachregeln — kuratierbare Felder, Kantenarten, Scopes — aus genau dieser Quelle bezieht und
keine eigene Fachlogik enthält (§17.1).
"""

from __future__ import annotations

from fastapi import APIRouter

from wissensgraph.api.dependencies import ActorDep, SettingsDep
from wissensgraph.config.masking import mask_config

router = APIRouter(prefix="/api/v1/config", tags=["Betrieb"])


@router.get("/effective", summary="Aufgelöste Konfiguration mit maskierten Secrets")
def effective_config(settings: SettingsDep, actor: ActorDep) -> dict[str, object]:
    """Gibt die aufgelöste Konfiguration zurück; Secrets sind durch ``***`` ersetzt."""
    del actor  # nur zur Durchsetzung der Authentifizierung
    masked = mask_config(settings.model_dump(mode="json"))
    assert isinstance(masked, dict)
    return masked
