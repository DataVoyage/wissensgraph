"""Kanten und Kuration (§16.2, §17.2 Ansicht 4, §17.3).

Der Unterschied zwischen ``DELETE /edges/{id}`` und ``POST /edges/{id}/reject`` ist der Kern
dieses Routers und keine Geschmacksfrage: Löschen heißt "hier gehört sie nicht hin", Verwerfen
heißt "diese Beziehung gibt es nicht". Nur das zweite bindet einen Folgelauf — und genau das
verlangt §24 als Abnahmekriterium.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from wissensgraph.api.dependencies import ActorDep, RuntimeDep, SettingsDep, resolve_store
from wissensgraph.api.schemas import EdgeCreate, EdgeReject, UndoRequest
from wissensgraph.services.catalog import CURATION_QUEUE_LIMIT
from wissensgraph.services.curation import CurationError, NotFoundError
from wissensgraph.services.serialization import journal_dict

router = APIRouter(prefix="/api/v1", tags=["Kuration"])


@router.post("/edges", status_code=status.HTTP_201_CREATED, summary="Kante von Hand anlegen")
def create_edge(
    payload: EdgeCreate, runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep
) -> dict[str, Any]:
    """Legt eine kuratierte Kante an (§16.2).

    Sie ist damit vor jedem Lauf geschützt: §10.4 lässt kuratierte Kanten unangetastet. Ein
    früherer Negativvermerk auf dasselbe Tripel wird zurückgenommen — wer die Kante jetzt von Hand
    setzt, hat seine Meinung geändert.
    """
    store = resolve_store(settings, payload.store)
    try:
        ergebnis = runtime.curation.add_edge(
            store=store,
            from_id=payload.from_id,
            to_id=payload.to_id,
            to_store=payload.to_store,
            kind=payload.kind,
            actor=actor,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.delete(
    "/edges/{edge_id}",
    status_code=status.HTTP_200_OK,
    summary="Kante entfernen — ohne Negativvermerk",
)
def delete_edge(
    edge_id: UUID,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Entfernt eine Kante und schreibt ``edge_removed`` ins Journal (§16.2).

    Die Antwort trägt den Journaleintrag und nicht ``204``: Ohne ihn könnte die Oberfläche kein
    Undo anbieten, und §17.3 verlangt genau das für jede Kuration.
    """
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.delete_edge(edge_id, store=gewaehlt, actor=actor)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.post("/edges/{edge_id}/verify", summary="Kante bestätigen")
def verify_edge(
    edge_id: UUID,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Setzt ``verified_by``, ``verified_at`` und ``curated`` (§16.2, Leitprinzip 6)."""
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.verify_edge(edge_id, store=gewaehlt, actor=actor)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.post("/edges/{edge_id}/reject", summary="Kante verwerfen — mit Negativvermerk")
def reject_edge(
    edge_id: UUID,
    payload: EdgeReject,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Entfernt die Kante **und** vermerkt das Tripel, damit sie nicht neu entsteht (§16.2)."""
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.reject_edge(
            edge_id, store=gewaehlt, actor=actor, reason=payload.reason
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.get("/curation/queue", summary="Offene Kurationsaufgaben nach Confidence")
def queue(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = CURATION_QUEUE_LIMIT,
) -> dict[str, Any]:
    """Unbestätigte Kanten und ``supersedes``-Vorschläge (§16.2, §17.2 Ansicht 4)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    aufgaben = runtime.catalog.curation_queue(store=gewaehlt, limit=limit)
    return {"store": gewaehlt, "items": [aufgabe.as_dict() for aufgabe in aufgaben]}


@router.post("/curation/undo", summary="Eine Kuration zurücknehmen")
def undo(
    payload: UndoRequest, runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep
) -> dict[str, Any]:
    """Nimmt einen bestimmten Journaleintrag zurück (§17.3).

    Inhaltliche Änderungen sind ausgenommen und antworten mit ``409``: Das Journal hält Feldnamen
    fest, keine Werte (§7.4) — es *kann* einen alten Text nicht wiederherstellen. Der Endpunkt
    sagt das offen, statt die Hälfte wiederherzustellen und den Rest zu verschweigen.
    """
    store = resolve_store(settings, payload.store)
    try:
        ergebnis = runtime.curation.undo(payload.entry_id, store=store, actor=actor)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.get("/curation/journal", summary="Die jüngsten Journaleinträge eines Stores")
def journal(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
    store: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = CURATION_QUEUE_LIMIT,
) -> dict[str, Any]:
    """Das Änderungsjournal als Strom — die Nachvollziehbarkeit aus §17.3.

    Bewusst ohne Cache-Header: Ein Journal, das aus dem Zwischenspeicher kommt, behauptet über
    einen Zustand, den es gerade nicht kennt.
    """
    del actor
    response.headers["Cache-Control"] = "no-store"
    gewaehlt = resolve_store(settings, store)
    eintraege = runtime.catalog.journal(store=gewaehlt, limit=limit)
    return {"store": gewaehlt, "items": [journal_dict(eintrag) for eintrag in eintraege]}
