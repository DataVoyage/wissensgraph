"""Der MCP-Server: die Werkzeuge aus §18.1 an einem Transport (§18.3).

Diese Datei ist die einzige im System, die das MCP-SDK kennt. Alles Fachliche steht in
:mod:`wissensgraph.mcp.tools` und ist ohne Server prüfbar; hier wird nur noch übersetzt —
Werkzeugbeschreibung hinein, JSON heraus.

Die Absicherung aus §18.3 passiert beim **Bauen** und nicht beim Aufrufen: Die Laufzeit bekommt
eine Arbeitseinheiten-Fabrik, die für ``shared`` ausschließlich die nur lesende Verbindung
herausgibt. Ein Schreibversuch scheitert damit in PostgreSQL — gleichgültig, über welchen
Codepfad er kommt und ob dieser Code die Regel kennt.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.mcp.tools import Toolbox, ToolError, build_toolbox
from wissensgraph.observability.logging import get_logger
from wissensgraph.runtime import Runtime

_log = get_logger(__name__)


def readonly_runtime(settings: Settings, **kwargs: Any) -> Runtime:
    """Eine Laufzeit, die auf ``shared`` nur lesen kann (§18.3).

    Die Beschränkung liegt in der Verbindung und nicht in einer Prüfung: §20.1 verlangt als
    Guard-Test ausdrücklich, dass "die MCP-Verbindung auf ``shared`` bei jedem Schreibversuch
    einen Datenbankfehler erzeugt". Eine Prüfung im Anwendungscode wäre nur so gut wie der
    Codepfad, der sie aufruft.
    """
    registry = StoreRegistry(settings)
    fabrik = UnitOfWorkFactory(registry, readonly_stores=frozenset({defaults.STORE_SHARED}))
    return Runtime(settings, unit_of_work=fabrik, **kwargs)


@dataclass(frozen=True)
class Handlers:
    """Die beiden Protokollrückrufe eines MCP-Servers (§18).

    Getrennt vom Server, weil hier die Logik liegt und dort nur die Verdrahtung: So lassen sich
    beide Rückrufe prüfen, ohne eine Sitzung aufzubauen — und ein Wechsel der SDK-Version berührt
    nur :func:`build_server`.
    """

    list_tools: Callable[[Any, Any], Awaitable[Any]]
    call_tool: Callable[[Any, Any], Awaitable[Any]]


def build_handlers(toolbox: Toolbox) -> Handlers:
    """Baut die Protokollrückrufe zu einer Werkzeugkiste (§18.1)."""
    from mcp import types

    werkzeuge = {spec.name: spec for spec in toolbox.specs()}

    def _antwort(text: str, *, fehler: bool = False) -> Any:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=fehler
        )

    async def _list_tools(_kontext: Any = None, _params: Any = None) -> Any:
        """Die Werkzeugliste — in der Reihenfolge aus §18.2."""
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                )
                for spec in werkzeuge.values()
            ]
        )

    async def _call_tool(_kontext: Any, params: Any) -> Any:
        """Führt ein Werkzeug aus und gibt seine Antwort als JSON-Text zurück.

        Ein :class:`ToolError` wird zu ``is_error`` und nicht zu einer Ausnahme: Er ist eine
        Auskunft an den Agenten ("das Konzept gibt es nicht", "in den geteilten Store darfst du
        nicht schreiben") und keine Störung des Protokolls. Daraus einen Transportfehler zu
        machen nähme dem Agenten die Möglichkeit, es anders zu versuchen. Alles andere fliegt
        weiter — ein Programmfehler gehört ins Log und nicht in eine höfliche Meldung.
        """
        spec = werkzeuge.get(params.name)
        if spec is None:
            return _antwort(f"Unbekanntes Werkzeug '{params.name}'.", fehler=True)
        try:
            ergebnis = spec.call(params.arguments or {})
        except ToolError as exc:
            _log.info("mcp.abgelehnt", tool=params.name, grund=str(exc))
            return _antwort(json.dumps({"error": str(exc)}, ensure_ascii=False), fehler=True)
        _log.info("mcp.aufgerufen", tool=params.name, actor=toolbox.actor)
        return _antwort(json.dumps(ergebnis, ensure_ascii=False, default=str))

    return Handlers(list_tools=_list_tools, call_tool=_call_tool)


def build_server(toolbox: Toolbox, *, name: str = "wissensgraph") -> Any:
    """Baut den MCP-Server und meldet die sieben Werkzeuge an (§18.1).

    Args:
        toolbox: Die Werkzeuge, gebunden an eine Sitzung.
        name: Der Name, unter dem der Server sich meldet.

    Returns:
        Den Server des SDK. Der Rückgabetyp ist bewusst offen: Das SDK ist die einzige
        Abhängigkeit dieses Moduls, und ein Typ daraus in der Signatur zöge es in jeden Aufrufer.
    """
    from mcp.server.lowlevel.server import Server

    rueckrufe = build_handlers(toolbox)
    server: Any = Server(name, on_list_tools=rueckrufe.list_tools, on_call_tool=rueckrufe.call_tool)
    return server


async def serve_stdio(settings: Settings, *, session: str) -> None:  # pragma: no cover — Transport
    """Startet den Server über stdio — der Weg, auf dem ein Agent ihn lokal einbindet (§18).

    Ohne Test, und das ist eine bewusste Grenze: Was hier passiert, ist das Verbinden zweier
    Ströme. Geprüft wird, was darin fließt — die Werkzeuge — und nicht, dass ``stdin`` ein
    ``stdin`` ist.
    """
    from mcp.server.stdio import stdio_server

    with readonly_runtime(settings) as runtime:
        server = build_server(build_toolbox(runtime, session=session))
        async with stdio_server() as (lesen, schreiben):
            await server.run(lesen, schreiben, server.create_initialization_options())


__all__ = ["Handlers", "build_handlers", "build_server", "readonly_runtime", "serve_stdio"]
