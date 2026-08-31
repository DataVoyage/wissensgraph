"""Aufgelöste Task-Profile und Modellnutzung (§16.2).

Beide Endpunkte kosten keinen einzigen Token. Das ist bei ``/models`` der eigentliche Punkt: Er
beantwortet "welches Modell würde diese Aufgabe benutzen und wäre der Anbieter für diesen Store
überhaupt erlaubt?", ohne dafür etwas hinauszuschicken (§11.5, §11.7).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from wissensgraph.api.dependencies import ActorDep, RuntimeDep, SettingsDep, resolve_store
from wissensgraph.config import defaults

router = APIRouter(prefix="/api/v1/models", tags=["Modelle"])


@router.get("", summary="Task-Profile, Provider-Zustand und Store-Policies")
def models(runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep) -> dict[str, Any]:
    """Die aufgelösten Routen je Aufgabe (§11.3, §16.2).

    ``configured`` sagt, ob der Anbieter einen Schlüssel hat — nicht, ob er erreichbar ist. Der
    Unterschied ist Absicht: Eine Erreichbarkeitsprüfung wäre ein Aufruf, und dieser Endpunkt
    verspricht, keinen zu machen.
    """
    del actor
    routen = runtime.router.routes()
    richtlinien = {
        store: sorted(erlaubt) if (erlaubt := runtime.models.allowed_providers(store)) else None
        for store in settings.stores
    }
    return {
        "tasks": [
            {
                "task": route.task,
                "provider": route.provider,
                "model": route.model,
                "model_key": route.model_key,
                "local": route.local,
                "dim": route.dim,
                "temperature": route.temperature,
                "configured": route.configured,
                "fallbacks": list(route.fallbacks),
                "generated_by": route.generated_by,
            }
            for route in routen
        ],
        "policies": richtlinien,
        "budget": settings.budget.model_dump(),
    }


@router.get("/usage", summary="Aufrufe, Token und Kostenschätzung je Lauf und Task")
def usage(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    run_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = defaults.MODEL_USAGE_LIMIT,
) -> dict[str, Any]:
    """Die Auswertung aus ``model_calls`` (§7.4, §16.2, §21.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    zeilen = runtime.catalog.usage(store=gewaehlt, run_id=run_id, limit=limit)
    return {"store": gewaehlt, "items": [zeile.as_dict() for zeile in zeilen]}
