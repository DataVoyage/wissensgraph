"""Traversierung, Suche, Übersicht und Nachbarn (§16.2).

Die vier Endpunkte sind der Lesepfad der Graph-Ansicht (§17.2 Ansicht 1) und zugleich das, was
der MCP-Server in Stufe 12 als Werkzeuge anbietet. Sie liegen deshalb in einem eigenen Router:
Beide Hüllen greifen auf dieselben Dienstaufrufe, und was hier an Fachlogik entstünde, fehlte dem
Agenten (Leitprinzip 14).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from wissensgraph.api.dependencies import ActorDep, RuntimeDep, SettingsDep, resolve_store
from wissensgraph.api.schemas import SearchRequest, TraverseRequest
from wissensgraph.config import defaults
from wissensgraph.config.schema import RankingConfig
from wissensgraph.ports.repositories import ConceptFilter
from wissensgraph.services.catalog import konzept_dict
from wissensgraph.services.graph import UnknownStartError

router = APIRouter(prefix="/api/v1/graph", tags=["Graph"])


@router.post("/traverse", summary="Vom Startknoten aus über mehrere Hops")
def traverse(
    payload: TraverseRequest, runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep
) -> dict[str, Any]:
    """Knoten, Kanten und Scores einer Traversierung (§12.1, §12.3).

    Ein unbekannter Startknoten ist ``404`` und kein leeres Ergebnis: "keine Nachbarn" und "gibt
    es nicht" sind zwei verschiedene Antworten, und nur eine davon ist ein Grund, die Eingabe zu
    prüfen.
    """
    del actor
    store = resolve_store(settings, payload.store)
    ueberschreibung = payload.ranking_overrides
    ranking = (
        None
        if ueberschreibung is None
        else RankingConfig(
            **{
                **settings.traversal.ranking.model_dump(),
                **{
                    name: wert
                    for name, wert in ueberschreibung.model_dump().items()
                    if wert is not None
                },
            }
        )
    )
    try:
        ergebnis = runtime.graph.traverse(
            [payload.start_id],
            store=store,
            hops=payload.hops,
            max_nodes=payload.max_nodes,
            ranking=ranking,
            kinds=payload.kinds,
            stores=payload.stores,
        )
    except UnknownStartError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.post("/search", summary="Zweistufige Suche über Cluster und Dokumente")
def search(
    payload: SearchRequest, runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep
) -> dict[str, Any]:
    """Die Suche aus §12.4; der benutzte Modus steht im Ergebnis.

    Der Modus ist Teil der Antwort und keine Fußnote: Ohne Embedding-Modell wird lexikalisch
    gesucht, und ein stiller Qualitätsverlust wäre die schlechtere Variante (§12.4, §11.5).
    """
    del actor
    store = (
        settings.store_of_scope(payload.scope)
        if payload.scope is not None and payload.scope in {item.name for item in settings.scopes}
        else resolve_store(settings, payload.store)
    )
    ergebnis = runtime.graph.search(
        payload.query, store=store, limit=payload.limit, granularity=payload.granularity
    )
    return {"store": store, **ergebnis.as_dict()}


@router.get("/overview", summary="Cluster-Übersicht als Einstiegspunkt")
def overview(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = defaults.SEARCH_LIMIT,
) -> dict[str, Any]:
    """Die Cluster eines Stores mit Titel, Beschreibung und Mitgliederzahl (§16.2, §18.1).

    Der günstige Einstieg: Ein Agent oder ein Mensch soll eine Sitzung nicht mit einer Suche
    beginnen müssen, sondern mit der Frage "worum geht es hier überhaupt?" (§18.2).
    """
    del actor
    gewaehlt = resolve_store(settings, store)
    zusammenfassungen, weiter = runtime.catalog.clusters(store=gewaehlt, scope=scope, limit=limit)
    return {
        "store": gewaehlt,
        "items": [item.as_dict() for item in zusammenfassungen],
        "next_cursor": weiter,
    }


@router.get("/neighbors/{concept_id:path}", summary="Ein Hop — für inkrementelles Aufklappen")
def neighbors(
    concept_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    max_nodes: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """Genau ein Hop (§16.2).

    Der eigene Endpunkt neben ``traverse`` ist §17.2 geschuldet: "Inkrementelles Aufklappen Hop
    für Hop … kein Vorabladen des Gesamtgraphen." Ein Klick auf einen Knoten soll eine Abfrage
    auslösen, deren Größe von diesem Knoten abhängt und nicht vom Graphen.
    """
    del actor
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.graph.traverse([concept_id], store=gewaehlt, hops=1, max_nodes=max_nodes)
    except UnknownStartError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.get("/loose", summary="Lose Knoten eines Stores")
def loose(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = defaults.SEARCH_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die Knoten aus ``v_loose_concepts`` als Filter des Browsers (§15.1, §17.2).

    Ein eigener Endpunkt und nicht nur ein Filterwert, weil die Graph-Ansicht ihn als eigene
    Ebene führt ("nur lose Knoten") und dabei dieselbe Definition benutzen soll wie der Lauf aus
    §15 — sonst zeigte die Oberfläche etwas anderes an, als der Lauf bearbeitet.
    """
    del actor
    gewaehlt = resolve_store(settings, store)
    seite = runtime.catalog.concepts(
        store=gewaehlt,
        filter=ConceptFilter(scope=scope, orphan=True),
        limit=limit,
        cursor=cursor,
    )
    return {
        "store": gewaehlt,
        "threshold": settings.orphans.loose_threshold,
        "items": [konzept_dict(item) for item in seite.items],
        "next_cursor": seite.next_cursor,
    }
