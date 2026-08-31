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
from wissensgraph.api.routers import clusters as clusters_router
from wissensgraph.api.routers import concepts as concepts_router
from wissensgraph.api.routers import config as config_router
from wissensgraph.api.routers import curation as curation_router
from wissensgraph.api.routers import graph as graph_router
from wissensgraph.api.routers import health as health_router
from wissensgraph.api.routers import models as models_router
from wissensgraph.api.routers import runs as runs_router
from wissensgraph.config.schema import Settings
from wissensgraph.observability.logging import bind_context, clear_context, get_logger
from wissensgraph.runtime import Runtime

#: Header, über den ein Aufrufer eine eigene Korrelations-ID mitgeben kann. Fehlt er, wird eine
#: erzeugt. Die ID landet als Pflichtfeld ``request_id`` in jedem Logeintrag der Anfrage (§21.1).
REQUEST_ID_HEADER = "X-Request-ID"

_logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Baut die Laufzeit auf und gibt ihre Verbindungen beim Herunterfahren wieder frei.

    Die Laufzeit ist dieselbe Klasse, die auch ``wg`` und der Worker benutzen. Sie hier zu bauen
    statt in jedem Router ist der Grund, warum ein Lauf über HTTP und derselbe Lauf über die
    Kommandozeile nicht auseinanderlaufen können (Leitprinzip 14).
    """
    settings: Settings = app.state.settings
    runtime = app.state.runtime if getattr(app.state, "runtime", None) else Runtime(settings)
    app.state.runtime = runtime
    app.state.registry = runtime.stores
    _logger.info("api_gestartet", env=settings.env, stores=list(runtime.stores.store_names))
    try:
        yield
    finally:
        runtime.close()
        _logger.info("api_beendet")


def create_app(settings: Settings, *, runtime: Runtime | None = None) -> FastAPI:
    """Baut die Anwendung für eine bestimmte Konfiguration.

    Args:
        settings: Die bereits aufgelöste und validierte Konfiguration (§6.1 Regel 4).
        runtime: Eine fertig zusammengesteckte Laufzeit. Ohne Angabe baut die Anwendung sich
            beim Start eine eigene. Der Parameter ist der Weg, die API gegen speicherresidente
            Repositories und den Fake-Provider zu fahren — ohne Datenbank, ohne Netz und ohne
            einen einzigen Token.
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
    app.state.runtime = runtime

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
    app.include_router(concepts_router.router)
    app.include_router(graph_router.router)
    app.include_router(curation_router.router)
    app.include_router(clusters_router.router)
    app.include_router(runs_router.router)
    app.include_router(models_router.router)
    return app
