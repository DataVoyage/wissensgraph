"""Die Bindung der Werkzeuge an das MCP-Protokoll (§18).

Wenig Fläche, aber drei wichtige Aussagen.

Erstens: Das veröffentlichte Eingabeschema ist **wörtlich** das aus §18.1. Das SDK kann ein Schema
auch aus einer Funktionssignatur ableiten; täte es das hier, stünden die Anweisungen an den
Agenten an zwei Stellen, und die zweite wäre die, die zählt.

Zweitens: Ein :class:`ToolError` wird zur **Antwort** mit ``is_error`` und nicht zur Ausnahme. Er
ist eine Auskunft an den Agenten — "das Konzept gibt es nicht", "in den geteilten Store darfst du
nicht schreiben" —, und daraus einen Transportfehler zu machen nähme ihm die Möglichkeit, es
anders zu versuchen. Ein Programmfehler dagegen bleibt auf dem Server: Der Agent bekommt eine
allgemeine Meldung, sein Text steht im Log.

Drittens ist der HTTP-Transport hier wirklich geprüft, und zwar mit dem echten MCP-Client über
eine ASGI-Anbindung: Handshake, Sitzung, ``tools/list`` und ``tools/call`` laufen über dieselbe
Starlette-Anwendung, die im Container am Port hängt — nur ohne Port. Für stdio gilt das nicht und
soll es nicht: Dort werden zwei Ströme verbunden, geprüft wird, was darin fließt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from mcp.server.mcpserver.exceptions import UnexpectedToolError

from wissensgraph.mcp.server import build_http_app, build_server
from wissensgraph.mcp.tools import ToolError, ToolSpec

pytestmark = pytest.mark.unit


def _schema(properties: dict[str, Any], *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


class _Kiste:
    """Eine Werkzeugkiste mit drei Werkzeugen — eines antwortet, zwei scheitern verschieden."""

    actor = "agent:test"

    def specs(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                name="echo",
                description="Gibt zurück, was es bekommt.",
                input_schema=_schema(
                    {
                        "a": {"type": "string", "description": "Ein Pflichtfeld."},
                        "n": {"type": "integer"},
                        "t": {"type": "array", "items": {"type": "string"}},
                    },
                    required=("a",),
                ),
                call=lambda args: {"gesehen": dict(args)},
            ),
            ToolSpec(
                name="verboten",
                description="Lehnt immer ab.",
                input_schema=_schema({}),
                call=self._ablehnen,
            ),
            ToolSpec(
                name="kaputt",
                description="Ein Programmfehler.",
                input_schema=_schema({}),
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
        raise ZeroDivisionError("die interne Wahrheit")


def _server() -> Any:
    return build_server(_Kiste(), name="prüfling")  # type: ignore[arg-type]


class TestWerkzeugliste:
    async def test_meldet_alle_werkzeuge_in_der_reihenfolge_aus_der_spezifikation(self) -> None:
        werkzeuge = await _server().list_tools()

        assert [werkzeug.name for werkzeug in werkzeuge] == ["echo", "verboten", "kaputt"]
        assert werkzeuge[0].description == "Gibt zurück, was es bekommt."

    async def test_veroeffentlicht_das_schema_aus_der_spezifikation_woertlich(self) -> None:
        """Nicht ein aus der Signatur abgeleitetes — die Feldbeschreibungen sind der Zweck."""
        werkzeuge = await _server().list_tools()

        assert werkzeuge[0].input_schema == _Kiste().specs()[0].input_schema

    def test_der_server_meldet_sich_unter_seinem_namen(self) -> None:
        assert _server().name == "prüfling"


class TestAufruf:
    async def test_gibt_das_ergebnis_als_json_zurueck(self) -> None:
        ergebnis = await _server().call_tool("echo", {"a": "hallo"})

        assert ergebnis.is_error is False
        assert json.loads(ergebnis.content[0].text) == {"gesehen": {"a": "hallo"}}

    async def test_ein_ausgelassenes_feld_kommt_nicht_als_none_an(self) -> None:
        """Sonst löschte ein ``concept_upsert`` ohne ``title`` den Titel (§18.1)."""
        ergebnis = await _server().call_tool("echo", {"a": "hallo"})

        assert json.loads(ergebnis.content[0].text)["gesehen"] == {"a": "hallo"}

    async def test_reicht_liste_und_zahl_unveraendert_durch(self) -> None:
        ergebnis = await _server().call_tool("echo", {"a": "x", "n": 3, "t": ["p", "q"]})

        assert json.loads(ergebnis.content[0].text)["gesehen"] == {
            "a": "x",
            "n": 3,
            "t": ["p", "q"],
        }

    async def test_ein_fehlendes_pflichtfeld_wird_abgewiesen(self) -> None:
        with pytest.raises(SdkToolError):
            await _server().call_tool("echo", {"n": 1})

    async def test_ein_werkzeugfehler_traegt_seinen_text_zum_agenten(self) -> None:
        with pytest.raises(SdkToolError) as fehler:
            await _server().call_tool("verboten", {})

        assert "Das darfst du nicht." in str(fehler.value)

    async def test_ein_unbekanntes_werkzeug_ist_eine_meldung(self) -> None:
        with pytest.raises(SdkToolError) as fehler:
            await _server().call_tool("gibtsnicht", {})

        assert "gibtsnicht" in str(fehler.value)

    async def test_ein_programmfehler_bleibt_auf_dem_server(self) -> None:
        """Er gehört ins Log und nicht in eine Meldung an den Agenten."""
        with pytest.raises(UnexpectedToolError) as fehler:
            await _server().call_tool("kaputt", {})

        assert "die interne Wahrheit" not in str(fehler.value)
        assert isinstance(fehler.value.__cause__, ZeroDivisionError)


class TestHttpTransport:
    """Derselbe Server über Streamable HTTP — mit dem echten Client, ohne Port."""

    begruessung: Any = None

    async def _sitzung(self, aufgabe: Any) -> Any:
        app = build_http_app(_Kiste(), host="0.0.0.0", path="/mcp")  # type: ignore[arg-type]
        transport = httpx2.ASGITransport(app=app)
        # Der Lebenszyklus muss von Hand laufen: ``ASGITransport`` schickt nur Anfragen und kein
        # 'lifespan.startup'. Ohne ihn steht die Sitzungsverwaltung des Transports nicht, und der
        # erste Aufruf scheitert an einer nicht gestarteten Task-Gruppe — im Container erledigt
        # uvicorn genau das.
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(transport=transport, base_url="http://mcp") as http,
            streamable_http_client("http://mcp/mcp", http_client=http) as (lesen, schreiben, *_),
            ClientSession(lesen, schreiben) as sitzung,
        ):
            self.begruessung = await sitzung.initialize()
            return await aufgabe(sitzung)

    async def test_ein_client_bekommt_die_werkzeugliste_ueber_http(self) -> None:
        ergebnis = await self._sitzung(lambda sitzung: sitzung.list_tools())

        assert [werkzeug.name for werkzeug in ergebnis.tools] == ["echo", "verboten", "kaputt"]

    async def test_der_handshake_nennt_namen_und_version(self) -> None:
        """Ohne Version sähe ein Agent in seiner Serverliste nur einen Namen."""
        await self._sitzung(lambda sitzung: sitzung.list_tools())

        assert self.begruessung.server_info.name == "wissensgraph"
        assert self.begruessung.server_info.version == version("wissensgraph")

    async def test_ein_werkzeug_laeuft_ueber_http(self) -> None:
        ergebnis = await self._sitzung(
            lambda sitzung: sitzung.call_tool("echo", {"a": "über das Netz"})
        )

        assert ergebnis.is_error is False
        assert json.loads(ergebnis.content[0].text) == {"gesehen": {"a": "über das Netz"}}

    async def test_ein_werkzeugfehler_kommt_als_antwort_und_nicht_als_transportfehler(
        self,
    ) -> None:
        ergebnis = await self._sitzung(lambda sitzung: sitzung.call_tool("verboten", {}))

        assert ergebnis.is_error is True
        assert "Das darfst du nicht." in ergebnis.content[0].text

    async def test_der_transport_verlangt_keine_authentifizierung(self) -> None:
        """Ausdrücklich so gewollt (§20.3): Die Absicherung liegt im Netz, nicht am Endpunkt.

        Der Test hält es fest, damit ein späteres Einschalten von Auth nicht unbemerkt
        geschieht — er schickt keinerlei Zugangsdaten und erwartet trotzdem eine Antwort.
        """
        ergebnis = await self._sitzung(lambda sitzung: sitzung.list_tools())

        assert ergebnis.tools
