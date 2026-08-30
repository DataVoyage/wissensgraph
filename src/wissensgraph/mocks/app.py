"""Der Mock-Quellserver: nachgebildete Endpunkte plus Steuerungs-API (§9).

Die Endpunkte bilden die Ausschnitte der Confluence- und Jira-REST-APIs nach, die die Adapter
wirklich benutzen — mit ihrer Paginierung und ihrer Verschachtelung. Damit läuft "der komplette
Codepfad inklusive Paginierung, Fehlerbehandlung und Rate-Limit-Logik in der Entwicklung
tatsächlich" (§9.1), und die Umstellung auf die echte Quelle ist eine URL.

Dieser Dienst ist ausdrücklich ein Entwicklungswerkzeug. Er hat keine Authentifizierung, weil er
keine hat schützen sollen, und er läuft nur im Compose-Profil ``dev`` und ``test`` (§5.4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response

from wissensgraph.config import defaults
from wissensgraph.mocks.state import (
    FailRule,
    MockState,
    ScenarioNotFound,
)
from wissensgraph.observability.logging import get_logger

_log = get_logger(__name__)

#: Pfadpräfixe der nachgebildeten Systeme. Beide Quellen hinter einem Dienst zu betreiben spart
#: im Compose einen Container und macht die Steuerungs-API zu genau einer Adresse.
CONFLUENCE_PREFIX = "/confluence"
JIRA_PREFIX = "/jira"


def create_mock_app(fixtures_dir: Path | None = None) -> FastAPI:
    """Baut die Anwendung des Mock-Servers.

    Args:
        fixtures_dir: Verzeichnis der Seed-Daten; ohne Angabe der Pfad aus den Defaults.

    Returns:
        Die ASGI-Anwendung. Der Zustand hängt als ``app.state.mock`` daran, damit ein Test ihn
        ohne HTTP einsehen kann.
    """
    verzeichnis = Path(defaults.MOCK_FIXTURES_DIR) if fixtures_dir is None else fixtures_dir
    state = MockState.from_fixtures(verzeichnis)

    app = FastAPI(
        title="Wissensgraph Mock-Quellen",
        description=(
            "Nachbildung der von den Adaptern benutzten Confluence- und Jira-Endpunkte, "
            "plus Steuerungs-API nach §9.3. Kein Produktivdienst."
        ),
        version="1.0.0",
    )
    app.state.mock = state

    _register_middleware(app, state)
    _register_control(app, state)
    _register_confluence(app, state)
    _register_jira(app, state)
    return app


# ---------------------------------------------------------------------------
# Störungen: Latenz und erzwungene Fehler (§9.3)
# ---------------------------------------------------------------------------


def _register_middleware(app: FastAPI, state: MockState) -> None:
    """Legt Latenz und erzwungene Fehler *vor* jeden Endpunkt.

    Als Middleware und nicht in den Endpunkten, weil es sonst je Endpunkt vergessen werden kann —
    und ein Rate-Limit, das nur auf der Seitenliste greift, aber nicht auf dem Einzelabruf, prüft
    das Retry-Verhalten des Adapters nur halb.
    """

    @app.middleware("http")
    async def stoerungen(request: Request, call_next: Any) -> Response:
        pfad = request.url.path
        if pfad.startswith(defaults.MOCK_CONTROL_PREFIX):
            return await call_next(request)  # type: ignore[no-any-return]

        if state.latency_seconds > 0:
            # Nicht 'time.sleep': Der Server soll langsam antworten, nicht stehenbleiben. Sonst
            # ließe sich mit einer künstlichen Latenz kein paralleler Zugriff mehr prüfen.
            await anyio.sleep(state.latency_seconds)

        regel = state.next_failure(pfad)
        if regel is not None:
            kopfzeilen = {}
            if regel.retry_after is not None:
                kopfzeilen[defaults.SOURCE_RETRY_AFTER_HEADER] = str(regel.retry_after)
            _log.info("mock.fehler_erzwungen", path=pfad, status=regel.status)
            return Response(
                content=f'{{"message":"erzwungener Fehler {regel.status}"}}',
                status_code=regel.status,
                media_type="application/json",
                headers=kopfzeilen,
            )
        return await call_next(request)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Steuerungs-API (§9.3)
# ---------------------------------------------------------------------------


def _register_control(app: FastAPI, state: MockState) -> None:
    """Die fünf Endpunkte aus der Tabelle in §9.3."""
    praefix = defaults.MOCK_CONTROL_PREFIX

    @app.post(f"{praefix}/reset", summary="Zurücksetzen auf den Seed-Zustand")
    def reset() -> dict[str, Any]:
        state.reset()
        return {"status": "reset", **state.as_dict()}

    @app.post(f"{praefix}/scenario/{{name}}", summary="Szenario anwenden")
    def scenario(name: str) -> dict[str, Any]:
        try:
            return state.apply_scenario(name)
        except ScenarioNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{praefix}/latency", summary="Künstliche Antwortzeit setzen")
    def latency(seconds: Annotated[float, Body(embed=True, ge=0.0)] = 0.0) -> dict[str, Any]:
        state.latency_seconds = seconds
        return {"latency_seconds": state.latency_seconds}

    @app.post(f"{praefix}/fail", summary="Fehlerantworten erzwingen")
    def fail(rule: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        """Setzt oder löscht die Fehlerregel.

        Ein leerer Rumpf oder ``count <= 0`` schaltet ab. Das ist die Aufräumzeile jedes Tests,
        der eine Störung erzwungen hat.
        """
        if not rule or int(rule.get("count", 1)) <= 0:
            state.fail = None
            state.request_count = 0
            return {"fail": None}
        state.fail = FailRule(
            status=int(rule.get("status", 500)),
            count=int(rule.get("count", 1)),
            after_requests=int(rule.get("after_requests", 0)),
            retry_after=(None if rule.get("retry_after") is None else float(rule["retry_after"])),
            path_prefix=rule.get("path_prefix"),
        )
        state.request_count = 0
        return {"fail": vars(state.fail)}

    @app.get(f"{praefix}/state", summary="Aktueller Zustand für Test-Assertions")
    def zustand() -> dict[str, Any]:
        return state.as_dict()


# ---------------------------------------------------------------------------
# Confluence (§9.1)
# ---------------------------------------------------------------------------


def _register_confluence(app: FastAPI, state: MockState) -> None:
    """Der von :class:`ConfluenceAdapter` benutzte Ausschnitt der Confluence-REST-API."""
    basis = f"{CONFLUENCE_PREFIX}/rest/api"

    @app.get(f"{basis}/space", summary="Spaces auflisten")
    def spaces() -> dict[str, Any]:
        return {"results": state.spaces, "size": len(state.spaces)}

    @app.get(f"{basis}/content", summary="Seiten auflisten, seitenweise")
    def content(
        spaceKey: Annotated[list[str] | None, Query()] = None,
        since: Annotated[str | None, Query()] = None,
        start: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1)] = defaults.SOURCE_PAGE_SIZE,
    ) -> dict[str, Any]:
        gewaehlt = [
            _mit_links(seite, state)
            for seite in _sortiert(state.pages)
            if _space_passt(seite, spaceKey) and _seite_ist_neuer(seite, since)
        ]
        ausschnitt = gewaehlt[start : start + limit]
        antwort: dict[str, Any] = {
            "results": ausschnitt,
            "start": start,
            "limit": limit,
            "size": len(ausschnitt),
            "totalSize": len(gewaehlt),
        }
        if start + limit < len(gewaehlt):
            antwort["_links"] = {"next": f"{basis}/content?start={start + limit}&limit={limit}"}
        return antwort

    @app.get(f"{basis}/content/deleted", summary="Gelöschte Seiten melden")
    def deleted() -> dict[str, Any]:
        return {"results": [{"id": page_id} for page_id in state.deleted_pages]}

    @app.get(f"{basis}/content/{{page_id}}", summary="Eine Seite holen")
    def einzelne(page_id: str) -> dict[str, Any]:
        seite = state.pages.get(page_id)
        if seite is None:
            raise HTTPException(status_code=404, detail=f"Seite '{page_id}' gibt es nicht.")
        return _mit_links(seite, state)


# ---------------------------------------------------------------------------
# Jira (§9.1)
# ---------------------------------------------------------------------------


def _register_jira(app: FastAPI, state: MockState) -> None:
    """Der von :class:`JiraAdapter` benutzte Ausschnitt der Jira-REST-API."""

    @app.get(f"{JIRA_PREFIX}/rest/agile/1.0/board", summary="Boards auflisten")
    def boards() -> dict[str, Any]:
        return {"values": state.boards, "total": len(state.boards)}

    @app.get(f"{JIRA_PREFIX}/rest/api/3/search", summary="Vorgänge suchen, seitenweise")
    def search(
        jql: Annotated[str | None, Query()] = None,
        since: Annotated[str | None, Query()] = None,
        startAt: Annotated[int, Query(ge=0)] = 0,
        maxResults: Annotated[int, Query(ge=1)] = defaults.SOURCE_PAGE_SIZE,
    ) -> dict[str, Any]:
        gewaehlt = [
            vorgang
            for vorgang in _sortiert(state.issues)
            if _vorgang_passt(vorgang, jql) and _vorgang_ist_neuer(vorgang, since)
        ]
        ausschnitt = gewaehlt[startAt : startAt + maxResults]
        return {
            "issues": ausschnitt,
            "startAt": startAt,
            "maxResults": maxResults,
            "total": len(gewaehlt),
        }

    @app.get(f"{JIRA_PREFIX}/rest/api/3/deleted", summary="Gelöschte Vorgänge melden")
    def deleted() -> dict[str, Any]:
        return {"keys": list(state.deleted_issues)}

    @app.get(f"{JIRA_PREFIX}/rest/api/3/issue/{{key}}", summary="Einen Vorgang holen")
    def issue(key: str) -> dict[str, Any]:
        vorgang = state.issues.get(key)
        if vorgang is None:
            raise HTTPException(status_code=404, detail=f"Vorgang '{key}' gibt es nicht.")
        return vorgang


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _sortiert(objekte: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Stabile Reihenfolge nach Schlüssel.

    Ohne sie liefert dieselbe Seite bei zwei Läufen andere Objekte, und ein Adapter, der korrekt
    paginiert, würde trotzdem etwas auslassen — ein Fehler, der aussähe wie einer im Adapter.
    """
    return [objekte[schluessel] for schluessel in sorted(objekte)]


def _mit_links(seite: dict[str, Any], state: MockState) -> dict[str, Any]:
    """Ergänzt eine Seite um ihre internen Verweise aus ``links.json`` (§9.2).

    Die echte Confluence-API liefert Verweise nicht so; der Adapter für die echte Quelle wird sie
    aus dem Storage-Format lesen (§24, Stufe 13). Für den Kern ist der Unterschied unsichtbar —
    er sieht in beiden Fällen ein ``SourceDocument`` mit ``references``.
    """
    kopie = dict(seite)
    kopie["links"] = {**kopie.get("links", {}), "internal": state.links.get(str(seite["id"]), [])}
    return kopie


def _space_passt(seite: dict[str, Any], spaces: list[str] | None) -> bool:
    """Ob eine Seite zur Space-Auswahl gehört; ohne Auswahl gehören alle dazu."""
    if not spaces:
        return True
    return str(seite.get("space", {}).get("key", "")) in set(spaces)


def _seite_ist_neuer(seite: dict[str, Any], since: str | None) -> bool:
    """Serverseitiger Zeitfilter. Er ist eine Optimierung, keine Zusicherung — der Adapter
    filtert ohnehin noch einmal (§8.2 Regel 4)."""
    if not since:
        return True
    return str(seite.get("version", {}).get("when", "")) > since


def _vorgang_passt(vorgang: dict[str, Any], jql: str | None) -> bool:
    """Sehr grobe JQL-Nachbildung: Ohne Filter passt alles, sonst muss der Text vorkommen.

    Der Mock täuscht keine JQL-Auswertung vor. Was hier geprüft wird, ist der Codepfad des
    Adapters — dass er den Filter aus seiner Konfiguration überhaupt mitschickt.
    """
    if not jql:
        return True
    projekt = str(vorgang.get("key", "")).split("-")[0]
    return projekt in jql or "project" not in jql


def _vorgang_ist_neuer(vorgang: dict[str, Any], since: str | None) -> bool:
    """Zeitfilter der Vorgänge, analog zu den Seiten."""
    if not since:
        return True
    return str(vorgang.get("fields", {}).get("updated", "")) > since
