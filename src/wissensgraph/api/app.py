"""Aufbau der FastAPI-Anwendung (§16.1).

Die API ist eine der drei dünnen Hüllen um denselben Kern (Leitprinzip 14) — sie enthält keine
Fachlogik, sondern übersetzt HTTP in Aufrufe der Anwendungsschicht und zurück.

Die Anwendung wird über eine Factory gebaut und nicht als Modul-Singleton angelegt. Damit kann
ein Test eine Anwendung mit eigener Konfiguration erzeugen, ohne globalen Zustand anzufassen —
und derselbe Prozess könnte theoretisch mehrere Konfigurationen bedienen.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from wissensgraph.api.errors import register_error_handlers
from wissensgraph.api.routers import config as config_router
from wissensgraph.api.routers import health as health_router
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.observability.logging import bind_context, clear_context, get_logger

#: Header, über den ein Aufrufer eine eigene Korrelations-ID mitgeben kann. Fehlt er, wird eine
#: erzeugt. Die ID landet als Pflichtfeld ``request_id`` in jedem Logeintrag der Anfrage (§21.1).
REQUEST_ID_HEADER = "X-Request-ID"

_logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Legt die Store-Registry an und schließt ihre Verbindungspools beim Herunterfahren."""
    settings: Settings = app.state.settings
    registry = StoreRegistry(settings)
    app.state.registry = registry
    _logger.info("api_gestartet", env=settings.env, stores=list(registry.store_names))
    try:
        yield
    finally:
        registry.dispose()
        _logger.info("api_beendet")


def create_app(settings: Settings) -> FastAPI:
    """Baut die Anwendung für eine bestimmte Konfiguration.

    Args:
        settings: Die bereits aufgelöste und validierte Konfiguration (§6.1 Regel 4).
    """
    app = FastAPI(
        title="Wissensgraph",
        version="0.1.0",
        summary="HTTP-API des Wissensgraphen",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # CORS strikt aus der Konfiguration, kein Wildcard (§20.3). Die Validierung des Schemas
    # lehnt '*' bereits ab; hier wird nur noch angewandt, was dort erlaubt wurde.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_context(request: Request, call_next: object) -> Response:
        """Bindet eine Korrelations-ID an den Logkontext und gibt sie im Header zurück."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        bind_context(request_id=request_id)
        try:
            response: Response = await call_next(request)  # type: ignore[operator]
        finally:
            clear_context()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    register_error_handlers(app)
    app.include_router(health_router.router)
    app.include_router(config_router.router)
    return app
