"""ASGI-Einsprungpunkt für ``uvicorn`` (§5.1).

Uvicorn erwartet ein Modul mit einem Anwendungsobjekt. Diese Datei ist der einzige Ort, an dem
die Anwendung aus der Prozessumgebung heraus gebaut wird — überall sonst wird
:func:`wissensgraph.api.create_app` mit einer explizit übergebenen Konfiguration aufgerufen,
damit Tests ohne globalen Zustand auskommen.
"""

from __future__ import annotations

from wissensgraph.api.app import create_app
from wissensgraph.bootstrap import bootstrap

app = create_app(bootstrap(service="api"))
