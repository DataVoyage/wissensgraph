"""Die Bindung der Werkzeuge an das MCP-Protokoll (§18).

Wenig Fläche, aber eine wichtige Aussage: Ein :class:`ToolError` wird zur **Antwort** mit
``is_error`` und nicht zur Ausnahme. Er ist eine Auskunft an den Agenten — "das Konzept gibt es
nicht", "in den geteilten Store darfst du nicht schreiben" —, und daraus einen Transportfehler zu
machen nähme ihm die Möglichkeit, es anders zu versuchen.

Der Transport selbst (stdio) ist nicht geprüft. Was dort passiert, ist das Verbinden zweier
Ströme; geprüft wird, was darin fließt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from wissensgraph.mcp.server import build_handlers, build_server
from wissensgraph.mcp.tools import ToolError, ToolSpec

pytestmark = pytest.mark.unit


class _Kiste:
    """Eine Werkzeugkiste mit zwei Werkzeugen — eines antwortet, eines lehnt ab."""

    actor = "agent:test"

    def specs(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                name="echo",
                description="Gibt zurück, was es bekommt.",
                input_schema={"type": "object", "properties": {}},
                call=lambda args: {"gesehen": dict(args)},
            ),
            ToolSpec(
                name="verboten",
                description="Lehnt immer ab.",
                input_schema={"type": "object", "properties": {}},
                call=self._ablehnen,
            ),
            ToolSpec(
                name="kaputt",
                description="Ein Programmfehler.",
                input_schema={"type": "object", "properties": {}},
                call=self._platzen,
            ),
        )

    @staticmethod
    def _ablehnen(args: Mapping[str, Any]) -> dict[str, Any]:
        del args
        raise ToolError("Das darfst du nicht.")

    @staticmethod
    def _platzen(args: Mapping[str, Any]) -> dict[str, Any]:
        del args
        raise ZeroDivisionError("kaputt")


class _Params:
    """Die Aufrufparameter, wie das SDK sie übergibt."""

    def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        self.name = name
        self.arguments = arguments


class TestWerkzeugliste:
    async def test_meldet_alle_werkzeuge_mit_ihrem_schema(self) -> None:
        rueckrufe = build_handlers(_Kiste())  # type: ignore[arg-type]

        ergebnis = await rueckrufe.list_tools(None, None)

        assert [werkzeug.name for werkzeug in ergebnis.tools] == ["echo", "verboten", "kaputt"]
        assert ergebnis.tools[0].description == "Gibt zurück, was es bekommt."


class TestServer:
    def test_der_server_meldet_sich_unter_seinem_namen(self) -> None:
        """Die Verdrahtung selbst — mehr macht ``build_server`` nicht."""
        server = build_server(_Kiste(), name="prüfling")  # type: ignore[arg-type]

        assert server.name == "prüfling"


class TestAufruf:
    async def test_gibt_das_ergebnis_als_json_zurueck(self) -> None:
        rueckrufe = build_handlers(_Kiste())  # type: ignore[arg-type]

        ergebnis = await rueckrufe.call_tool(None, _Params("echo", {"a": 1}))

        assert ergebnis.is_error is False
        assert json.loads(ergebnis.content[0].text) == {"gesehen": {"a": 1}}

    async def test_ein_werkzeugfehler_wird_zur_antwort_und_nicht_zur_ausnahme(self) -> None:
        rueckrufe = build_handlers(_Kiste())  # type: ignore[arg-type]

        ergebnis = await rueckrufe.call_tool(None, _Params("verboten"))

        assert ergebnis.is_error is True
        assert json.loads(ergebnis.content[0].text)["error"] == "Das darfst du nicht."

    async def test_ein_unbekanntes_werkzeug_ist_eine_meldung(self) -> None:
        rueckrufe = build_handlers(_Kiste())  # type: ignore[arg-type]

        ergebnis = await rueckrufe.call_tool(None, _Params("gibtsnicht"))

        assert ergebnis.is_error is True
        assert "gibtsnicht" in ergebnis.content[0].text

    async def test_ein_programmfehler_fliegt_weiter(self) -> None:
        """Er gehört ins Log und nicht in eine höfliche Meldung."""
        rueckrufe = build_handlers(_Kiste())  # type: ignore[arg-type]

        with pytest.raises(ZeroDivisionError):
            await rueckrufe.call_tool(None, _Params("kaputt"))
