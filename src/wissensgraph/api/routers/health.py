"""Betriebsendpunkte: Liveness und Readiness (§16.2).

Der Unterschied ist bewusst:

* ``/healthz`` sagt nur, dass der Prozess lebt. Er fragt keine Datenbank — sonst würde ein
  Datenbankausfall dazu führen, dass der Orchestrator einen völlig gesunden Prozess neu startet.
* ``/readyz`` prüft die Verbindungen zu **beiden** Stores. Erst wenn beide antworten, ist der
  Prozess bereit, Anfragen zu beantworten. ``worker`` und ``mcp`` warten im Compose-Setup genau
  auf diesen Endpunkt (§5.5).

Beide Endpunkte sind ohne Authentifizierung erreichbar: Ein Healthcheck, der ein Token braucht,
ist im Orchestrator unbrauchbar, und die Antwort enthält nichts Vertrauliches (DSNs sind
maskiert, §20.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from wissensgraph.api.dependencies import RegistryDep

router = APIRouter(tags=["Betrieb"])


@router.get("/healthz", summary="Liveness — lebt der Prozess?")
def healthz() -> dict[str, str]:
    """Meldet, dass der Prozess läuft. Fragt bewusst keine Datenbank ab."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness — sind beide Stores erreichbar?")
def readyz(registry: RegistryDep, response: Response) -> dict[str, object]:
    """Prüft die Verbindungen zu allen konfigurierten Stores (§16.2).

    Antwortet mit ``503``, sobald ein Store nicht erreichbar ist — der Prozess läuft dann zwar,
    kann aber keine sinnvolle Arbeit leisten.
    """
    stores = registry.check_all()
    ready = all(store.healthy for store in stores)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "stores": [store.as_dict() for store in stores],
    }
