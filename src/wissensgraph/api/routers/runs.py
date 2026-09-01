"""Quellen, Läufe und ihr Fortschritt (§16.2, §16.3).

Alle ``POST /runs/*`` antworten mit ``202 Accepted``, einer Run-ID und einem ``Location``-Header.
Der Lauf entsteht dabei als Zeile in ``runs`` und der Job als Verweis darauf — nicht umgekehrt.
Das ist der Grund, warum die Oberfläche einen Lauf abonnieren kann, bevor der Worker ihn
überhaupt entnommen hat, und warum "Läufe blockieren die UI nie" (§17.3) keine Absichtserklärung
ist, sondern eine Eigenschaft des Aufbaus.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from wissensgraph.api.dependencies import (
    ActorDep,
    RuntimeDep,
    SettingsDep,
    resolve_store,
    store_of_scope,
)
from wissensgraph.api.schemas import EmbedRun, OrphanRun, ScopeRun, SyncRun
from wissensgraph.config import defaults
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.runtime import Runtime, SyncRequest, UnknownSourceError
from wissensgraph.services.orphans import OrphanRequest

router = APIRouter(prefix="/api/v1", tags=["Läufe"])

#: Wie oft der Fortschritt eines Laufs nachgesehen wird, solange er läuft (Sekunden).
#: Klein genug, dass die Oberfläche lebendig wirkt; groß genug, dass ein Dutzend offener Ströme
#: die Datenbank nicht beschäftigt.
EVENT_INTERVAL_SECONDS = 1.0

#: Nach dieser Zeit endet ein Ereignisstrom von selbst, auch wenn der Lauf noch läuft. Ein Strom,
#: der ewig offen bleibt, hält eine Verbindung und einen Worker-Thread — und ein Browser, der die
#: Seite verlassen hat, merkt davon nichts.
EVENT_MAX_SECONDS = 3600.0


@router.get("/sources", tags=["Betrieb"], summary="Konfigurierte Quellen mit Health")
def sources(runtime: RuntimeDep, actor: ActorDep) -> dict[str, Any]:
    """Adapter, Capabilities, Health und letzter Lauf (§16.2)."""
    del actor
    # ``as_dict`` der Registry und keine zweite Abschrift hier: Was ``wg sources list`` zeigt und
    # was die Betriebsansicht zeigt, ist dieselbe Auskunft (§16.2, Leitprinzip 14).
    return {
        "items": [
            {
                **quelle.as_dict(),
                "usable": quelle.usable,
                "last_run": (
                    None
                    if (letzter := _letzter_lauf(runtime, quelle.name)) is None
                    else letzter.as_dict()
                ),
            }
            for quelle in runtime.registered
        ]
    }


def _letzter_lauf(runtime: Runtime, source: str) -> Run | None:
    """Der jüngste Sync-Lauf einer Quelle, über alle Stores gesucht."""
    treffer: list[Run] = []
    for store in runtime.settings.stores:
        for lauf in runtime.catalog.runs(store=store, kind=RunKind.SYNC, limit=50):
            if lauf.params.get(defaults.RUN_PARAM_SOURCE) == source:
                treffer.append(lauf)
    if not treffer:
        return None
    return max(treffer, key=lambda lauf: (lauf.started_at is not None, lauf.started_at))


@router.get("/doctor", tags=["Betrieb"], summary="Diagnose nach dem Muster von wg doctor")
def doctor(runtime: RuntimeDep, actor: ActorDep) -> dict[str, Any]:
    """Alle Prüfungen aus ``wg doctor`` als JSON (§16.2, §17.2 Verwalten).

    Dieselben Prüfungen, dieselbe Reihenfolge, dieselbe Auskunft wie die CLI — die
    Diagnose-Karte der UI ist eine Zustellart, kein zweiter Doktor (Leitprinzip 14). Die
    Prüfungen verbinden sich wirklich mit den Stores; der Aufruf kostet entsprechend und
    gehört hinter einen Knopf, nicht in ein Abfrageintervall.
    """
    del actor
    from wissensgraph.diagnostics import run_diagnostics

    return run_diagnostics(runtime.settings, runtime.stores).as_dict()


@router.get("/runs", summary="Lauf-Historie mit Status und Statistik")
def list_runs(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    kind: Annotated[RunKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = defaults.RUNS_LIST_LIMIT,
) -> dict[str, Any]:
    """Die jüngsten Läufe eines Stores (§16.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    laeufe = runtime.catalog.runs(store=gewaehlt, kind=kind, limit=limit)
    return {"store": gewaehlt, "items": [lauf.as_dict() for lauf in laeufe]}


@router.get("/runs/{run_id}", summary="Ein Lauf mit Fortschritt und Fehlern")
def get_run(run_id: UUID, runtime: RuntimeDep, actor: ActorDep) -> dict[str, Any]:
    """Ein Lauf, über alle Stores gesucht (§16.2)."""
    del actor
    gefunden = runtime.catalog.run(run_id)
    if gefunden is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Kein Lauf mit der ID {run_id}."
        )
    store, lauf = gefunden
    return {"store": store, **lauf.as_dict()}


@router.post("/runs/{run_id}/cancel", summary="Lauf abbrechen")
def cancel_run(run_id: UUID, runtime: RuntimeDep, actor: ActorDep) -> dict[str, Any]:
    """Bricht einen wartenden oder laufenden Lauf ab (§16.2).

    Ein bereits abgeschlossener Lauf antwortet mit ``409``: Sein Ergebnis steht, und ein
    nachträgliches "abgebrochen" wäre eine Behauptung über etwas, das stattgefunden hat.
    """
    del actor
    beendet = runtime.cancel(run_id)
    if beendet is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Lauf {run_id} ist entweder unbekannt oder bereits abgeschlossen.",
        )
    return beendet.as_dict()


@router.get("/runs/{run_id}/events", summary="Server-Sent Events für den Fortschritt")
async def run_events(run_id: UUID, request: Request, runtime: RuntimeDep) -> StreamingResponse:
    """Der Live-Fortschritt eines Laufs (§16.3, §24).

    Bewusst ohne Authentifizierung im Header, sondern über dieselbe Sitzung wie der Rest: Die
    ``EventSource``-Schnittstelle des Browsers kann keine eigenen Header setzen. Der Strom liefert
    ausschließlich das, was ``GET /runs/{id}`` auch liefert — er ist eine Zustellart, kein zweiter
    Zugang zu anderen Daten.

    Der Strom endet, sobald der Lauf endgültig ist. Ein Client, der die Verbindung schließt,
    beendet ihn ebenfalls: Ohne diese Prüfung liefe die Schleife weiter, bis die Frist abläuft.
    """

    async def strom() -> AsyncIterator[bytes]:
        vergangen = 0.0
        letzter: dict[str, Any] | None = None
        while vergangen < EVENT_MAX_SECONDS:
            if await request.is_disconnected():
                return
            gefunden = await asyncio.to_thread(runtime.catalog.run, run_id)
            if gefunden is None:
                yield _ereignis("error", {"detail": f"Kein Lauf mit der ID {run_id}."})
                return
            store, lauf = gefunden
            aktuell = {"store": store, **lauf.as_dict()}
            # Nur bei Änderung senden: Ein Strom, der jede Sekunde denselben Zustand wiederholt,
            # ist für den Empfänger nicht von Fortschritt zu unterscheiden.
            if aktuell != letzter:
                yield _ereignis("progress", aktuell)
                letzter = aktuell
            if lauf.is_final:
                yield _ereignis("done", aktuell)
                return
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
            vergangen += EVENT_INTERVAL_SECONDS
        yield _ereignis("timeout", {"run_id": str(run_id)})

    return StreamingResponse(
        strom(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _ereignis(name: str, nutzlast: dict[str, Any]) -> bytes:
    """Ein einzelnes Server-Sent Event in der Form ``event:``/``data:``."""
    return f"event: {name}\ndata: {json.dumps(nutzlast, ensure_ascii=False)}\n\n".encode()


# ---------------------------------------------------------------------------
# Läufe anstoßen (§16.3)
# ---------------------------------------------------------------------------


@router.post("/runs/sync", status_code=status.HTTP_202_ACCEPTED, summary="Sync-Lauf anstoßen")
def start_sync(
    payload: SyncRun, runtime: RuntimeDep, actor: ActorDep, response: Response
) -> dict[str, Any]:
    """Legt einen Sync-Lauf an und stellt ihn in die Queue (§16.3)."""
    del actor
    try:
        lauf = runtime.submit_sync(
            payload.source, SyncRequest(full=payload.full, dry_run=payload.dry_run)
        )
    except UnknownSourceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _angenommen(lauf, response)


@router.post("/runs/embed", status_code=status.HTTP_202_ACCEPTED, summary="Embedding-Lauf")
def start_embed(
    payload: EmbedRun,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
) -> dict[str, Any]:
    """Stößt einen Embedding-Lauf an (§13.1, §16.3)."""
    del actor
    store_of_scope(settings, payload.scope)
    lauf = runtime.submit(
        RunKind.EMBED,
        scope=payload.scope,
        params={"scope": payload.scope, "rebuild": payload.rebuild},
    )
    return _angenommen(lauf, response)


@router.post("/runs/cluster", status_code=status.HTTP_202_ACCEPTED, summary="Clustering-Lauf")
def start_cluster(
    payload: ScopeRun,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
) -> dict[str, Any]:
    """Stößt einen Clustering-Lauf an (§13.2, §16.3)."""
    del actor
    store_of_scope(settings, payload.scope)
    lauf = runtime.submit(
        RunKind.CLUSTER,
        scope=payload.scope,
        params={"scope": payload.scope, "dry_run": payload.dry_run},
    )
    return _angenommen(lauf, response)


@router.post("/runs/relations", status_code=status.HTTP_202_ACCEPTED, summary="Kantenerkennung")
def start_relations(
    payload: ScopeRun,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
) -> dict[str, Any]:
    """Stößt einen Lauf der semantischen Kantenerkennung an (§14, §16.3)."""
    del actor
    store_of_scope(settings, payload.scope)
    lauf = runtime.submit(
        RunKind.RELATIONS,
        scope=payload.scope,
        params={"scope": payload.scope, "dry_run": payload.dry_run},
    )
    return _angenommen(lauf, response)


@router.post(
    "/runs/link-orphans", status_code=status.HTTP_202_ACCEPTED, summary="Verwaiste Knoten vernetzen"
)
def start_link_orphans(
    payload: OrphanRun,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
) -> dict[str, Any]:
    """Stößt einen Vernetzungslauf an — alle Parameter aus §15.4 als Body-Felder (§16.2)."""
    del actor
    store_of_scope(settings, payload.scope)
    # Die Anfrage wird hier schon gebaut, obwohl der Worker sie erneut baut: So scheitert ein
    # unzulässiger Parameter mit einer HTTP-Antwort und nicht später in einem Lauf, dessen
    # Fehlermeldung niemand mehr einem Aufruf zuordnen kann.
    OrphanRequest.from_params(payload.model_dump())
    lauf = runtime.submit(
        RunKind.LINK_ORPHANS,
        scope=payload.scope,
        params={name: wert for name, wert in payload.model_dump().items() if wert is not None},
    )
    return _angenommen(lauf, response)


def _angenommen(lauf: Run, response: Response) -> dict[str, Any]:
    """Die Antwort eines angenommenen Laufs: ``202`` mit ``Location`` (§16.3)."""
    response.headers["Location"] = f"/api/v1/runs/{lauf.id}"
    return lauf.as_dict()
