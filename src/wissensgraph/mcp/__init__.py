"""Der MCP-Retrieval-Layer (§18).

Die dritte dünne Hülle um denselben Kern (Leitprinzip 14). Sie enthält keine Fachlogik: Jedes
Werkzeug ist ein Aufruf desselben Dienstes, den auch CLI und HTTP-API benutzen.

Was hier zusätzlich passiert und nirgendwo sonst, sind die drei Absicherungen aus §18.3 — eine
nur lesende Verbindung auf ``shared``, ein ``actor`` je Sitzung im Änderungsjournal und ein
Deckel auf der Antwortgröße.
"""

from wissensgraph.mcp.tools import (
    Toolbox,
    ToolError,
    ToolSpec,
    build_toolbox,
)

__all__ = ["ToolError", "ToolSpec", "Toolbox", "build_toolbox"]
