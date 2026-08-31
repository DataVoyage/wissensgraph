"""Der MCP-Server: die Werkzeuge aus §18.1 an einem Transport (§18.3).

Diese Datei ist die einzige im System, die das MCP-SDK kennt. Alles Fachliche steht in
:mod:`wissensgraph.mcp.tools` und ist ohne Server prüfbar; hier wird nur noch übersetzt —
Werkzeugbeschreibung hinein, JSON heraus.

Gebaut wird auf :class:`mcp.server.mcpserver.MCPServer` — das ist FastMCP: Im SDK 2.x trägt die
Klasse, die früher ``FastMCP`` hieß, diesen Namen. Der Gewinn gegenüber dem Server der unteren
Ebene ist nicht die Dekorator-Schreibweise (die brauchen wir nicht, unsere Werkzeuge sind Daten),
sondern der Transport: ``streamable_http_app()`` liefert eine gewöhnliche Starlette-Anwendung.
Damit ist der HTTP-Weg **prüfbar** — anders als stdio, wo nur zwei Ströme verbunden werden.

Die Werkzeuge kommen weiterhin aus :meth:`Toolbox.specs` und nicht aus typisierten Funktions-
signaturen. Die Eingabeschemata in §18.1 sind sorgfältig formulierte Anweisungen an einen Agenten;
sie aus Python-Annotationen zurückzugewinnen hieße, sie ein zweites Mal zu schreiben. Deshalb wird
das Schema unverändert veröffentlicht und für die Prüfung der Argumente ein Pydantic-Modell daraus
abgeleitet — eine Richtung, nicht zwei Quellen.

Die Absicherung aus §18.3 passiert beim **Bauen** und nicht beim Aufrufen: Die Laufzeit bekommt
eine Arbeitseinheiten-Fabrik, die für ``shared`` ausschließlich die nur lesende Verbindung
herausgibt. Ein Schreibversuch scheitert damit in PostgreSQL — gleichgültig, über welchen
Codepfad er kommt und ob dieser Code die Regel kennt.
"""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, cast

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.mcp.tools import Toolbox, ToolError, ToolSpec, build_toolbox
from wissensgraph.observability.logging import get_logger
from wissensgraph.runtime import Runtime

if TYPE_CHECKING:  # pragma: no cover — nur für die Typprüfung
    from mcp.server.mcpserver.tools import Tool
    from starlette.applications import Starlette

_log = get_logger(__name__)

#: Die JSON-Schema-Typen, die in §18.1 vorkommen, als Python-Typen.
_TYPEN: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


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


def _feldtyp(schema: dict[str, Any]) -> Any:
    """Der Python-Typ zu einem Eigenschafts-Schema aus §18.1.

    Bewusst schmal: Es werden genau die Formen übersetzt, die dort vorkommen. Ein unbekannter
    Typ wird zu :class:`object` und damit durchgelassen — das Schema bleibt die Wahrheit für den
    Agenten, dieses Modell prüft nur, dass ein Aufruf sie nicht grob verfehlt.
    """
    if schema.get("type") == "array":
        elemente = _TYPEN.get(str(schema.get("items", {}).get("type", "string")), str)
        return list[elemente]  # type: ignore[valid-type]
    return _TYPEN.get(str(schema.get("type", "")), object)


def _argumentmodell(spec: ToolSpec) -> Any:
    """Leitet aus dem Eingabeschema eines Werkzeugs das Modell zur Prüfung ab.

    Nicht verlangte Felder bekommen ``None`` als Vorgabe. Der Aufruf lässt sie danach wieder
    fallen (siehe :func:`_werkzeug`): Die Werkzeuge in §18.1 unterscheiden "nicht angegeben" von
    "angegeben" — ``concept_upsert`` schreibt nur die Felder fort, die wirklich dastehen. Käme
    jedes ausgelassene Feld als ``None`` an, würde ein Aufruf ohne ``title`` den Titel löschen.
    """
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
    from pydantic import Field, create_model

    verlangt = set(spec.input_schema.get("required", ()))
    felder: dict[str, Any] = {}
    for name, schema in spec.input_schema.get("properties", {}).items():
        typ = _feldtyp(schema)
        beschreibung = schema.get("description")
        if name in verlangt:
            felder[name] = (typ, Field(description=beschreibung))
        else:
            felder[name] = (typ | None, Field(default=None, description=beschreibung))
    return create_model(f"{spec.name}_arguments", __base__=ArgModelBase, **felder)


def _werkzeug(spec: ToolSpec, *, actor: str) -> Tool:
    """Bindet ein Werkzeug aus §18.1 an das SDK.

    Der ``Tool`` wird von Hand gebaut und nicht über ``Tool.from_function`` gewonnen: Jene
    Fabrik leitet das veröffentlichte Schema aus der Signatur ab, und genau das soll hier nicht
    passieren — veröffentlicht wird ``spec.input_schema`` unverändert.

    Ein :class:`ToolError` wird zum ``ToolError`` des SDK und damit zu einer Antwort mit
    ``is_error``. Er ist eine Auskunft an den Agenten ("das Konzept gibt es nicht", "in den
    geteilten Store darfst du nicht schreiben") und keine Störung des Protokolls. Daraus einen
    Transportfehler zu machen nähme dem Agenten die Möglichkeit, es anders zu versuchen. Alles
    andere fliegt weiter — das SDK protokolliert es und schickt dem Agenten eine allgemeine
    Meldung, damit ein Programmfehler seinen Text nicht über das Netz trägt.
    """
    from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
    from mcp.server.mcpserver.tools import Tool
    from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata

    modell = _argumentmodell(spec)

    async def aufrufen(**kwargs: Any) -> str:
        argumente = {name: wert for name, wert in kwargs.items() if wert is not None}
        try:
            ergebnis = spec.call(argumente)
        except ToolError as exc:
            _log.info("mcp.abgelehnt", tool=spec.name, grund=str(exc))
            raise SdkToolError(str(exc)) from exc
        _log.info("mcp.aufgerufen", tool=spec.name, actor=actor)
        return json.dumps(ergebnis, ensure_ascii=False, default=str)

    return Tool(
        fn=aufrufen,
        name=spec.name,
        title=None,
        description=spec.description,
        parameters=spec.input_schema,
        fn_metadata=FuncMetadata(arg_model=modell),
        is_async=True,
        context_kwarg=None,
        annotations=None,
    )


def build_server(toolbox: Toolbox, *, name: str = "wissensgraph") -> Any:
    """Baut den MCP-Server und meldet die sieben Werkzeuge an (§18.1).

    Args:
        toolbox: Die Werkzeuge, gebunden an eine Sitzung.
        name: Der Name, unter dem der Server sich meldet.

    Returns:
        Den ``MCPServer`` des SDK. Der Rückgabetyp ist bewusst offen: Das SDK ist die einzige
        Abhängigkeit dieses Moduls, und ein Typ daraus in der Signatur zöge es in jeden Aufrufer.
    """
    from mcp.server import MCPServer

    werkzeuge = [_werkzeug(spec, actor=toolbox.actor) for spec in toolbox.specs()]
    # Die Version steht in der Vorstellung des Servers; ohne sie sieht ein Agent nur einen Namen.
    # Sie kommt aus den Paketmetadaten und nicht aus einer zweiten Konstante — die liefe sonst
    # irgendwann gegen die in 'pyproject.toml'.
    return MCPServer(name, version=version("wissensgraph"), tools=werkzeuge)


def build_http_app(
    toolbox: Toolbox, *, host: str, path: str, name: str = "wissensgraph"
) -> Starlette:
    """Die Starlette-Anwendung des HTTP-Transports (§18).

    Sie liegt hier und nicht erst in :func:`serve_http`, damit ein Test sie ohne Port und ohne
    Container gegen einen ``TestClient`` fahren kann — derselbe Kniff wie bei den Quell-Tests.

    ``stateless_http`` ist gesetzt: Der Server hält zwischen zwei Aufrufen nichts, was er nicht
    ohnehin aus der Datenbank liest. Damit darf ein Agent jederzeit neu verbinden, und mehrere
    Repliken hinter demselben Port verhalten sich gleich.
    """
    server = build_server(toolbox, name=name)
    return cast(
        "Starlette",
        server.streamable_http_app(streamable_http_path=path, stateless_http=True, host=host),
    )


async def serve_http(settings: Settings, *, session: str) -> None:  # pragma: no cover — Transport
    """Startet den Server über HTTP — der Startbefehl des mcp-Containers (§5.1, §18).

    Ohne Test, und das ist eine bewusste Grenze: Was hier passiert, ist das Binden eines Ports.
    Geprüft wird die Anwendung, die daran hängt — :func:`build_http_app` —, und nicht, dass
    uvicorn einen Socket öffnet.
    """
    import uvicorn

    if settings.mcp.host not in defaults.API_LOOPBACK_HOSTS:
        _log.warning(
            "mcp.ungeschuetzt",
            host=settings.mcp.host,
            port=settings.mcp.port,
            hinweis=(
                "Der MCP-Server kennt keine Authentifizierung und schreibt in den persönlichen "
                "Store. An einer anderen Adresse als Loopback darf er nur in einem Netz stehen, "
                "das selbst abgesichert ist (§20.3)."
            ),
        )

    with readonly_runtime(settings) as runtime:
        anwendung = build_http_app(
            build_toolbox(runtime, session=session),
            host=settings.mcp.host,
            path=settings.mcp.path,
        )
        # log_config=None lässt uvicorn die Logging-Konfiguration in Ruhe — sonst hätte das Log
        # zwei Formate, und die Zugriffszeilen liefen an den Pflichtfeldern aus §21.1 vorbei.
        server = uvicorn.Server(
            uvicorn.Config(
                anwendung,
                host=settings.mcp.host,
                port=settings.mcp.port,
                log_config=None,
            )
        )
        await server.serve()


async def serve_stdio(settings: Settings, *, session: str) -> None:  # pragma: no cover — Transport
    """Startet den Server über stdio — der Weg, auf dem ein Agent ihn lokal einbindet (§18).

    Er bleibt neben HTTP bestehen: Ein Agent, der den Server als Unterprozess startet, braucht
    weder Port noch laufenden Container, und für den persönlichen Store ist das der kürzeste Weg.
    """
    with readonly_runtime(settings) as runtime:
        server = build_server(build_toolbox(runtime, session=session))
        await server.run_stdio_async()


__all__ = [
    "build_http_app",
    "build_server",
    "readonly_runtime",
    "serve_http",
    "serve_stdio",
]
