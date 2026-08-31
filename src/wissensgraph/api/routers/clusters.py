"""Cluster und ihre Mitgliedschaft (§16.2, §17.2 Ansicht 3).

Der Cluster-Arbeitsplatz ist die Fläche, auf der ein Mensch die maschinelle Ordnung korrigiert.
Jede Operation hier setzt deshalb ``curated = true`` und hinterlässt einen Journaleintrag — und
das Entfernen eines Mitglieds zusätzlich einen Ausschlussvermerk (§13.4). Ohne ihn wäre die
Handbewegung nach einem Clustering-Lauf verschwunden, und genau das prüft §24 ab.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from wissensgraph.api.dependencies import ActorDep, RuntimeDep, SettingsDep, resolve_store
from wissensgraph.api.schemas import (
    ClusterCreate,
    ClusterMerge,
    ClusterPatch,
    ClusterSplit,
    MembersAdd,
)
from wissensgraph.config import defaults
from wissensgraph.services.curation import CurationError, NotFoundError

router = APIRouter(prefix="/api/v1/clusters", tags=["Cluster"])


@router.get("", summary="Cluster mit Mitgliederzahl")
def list_clusters(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = defaults.SEARCH_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die Cluster eines Stores, cursor-basiert (§16.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    zusammenfassungen, weiter = runtime.catalog.clusters(
        store=gewaehlt, scope=scope, limit=limit, cursor=cursor
    )
    return {
        "store": gewaehlt,
        "items": [item.as_dict() for item in zusammenfassungen],
        "next_cursor": weiter,
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Cluster von Hand anlegen")
def create_cluster(
    payload: ClusterCreate,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    response: Response,
) -> dict[str, Any]:
    """Legt ein Cluster an und hängt eine Auswahl hinein (§16.2).

    Das Cluster ist ``curated`` und damit von der automatischen Neubetitelung ausgenommen
    (§13.2 Schritt 4): Wer selbst benennt, will nicht umbenannt werden.
    """
    store = resolve_store(settings, payload.store)
    try:
        ergebnis = runtime.curation.create_cluster(
            store=store,
            scope=payload.scope,
            title=payload.title,
            description=payload.description,
            member_ids=payload.member_ids,
            actor=actor,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    assert ergebnis.concept is not None
    response.headers["Location"] = f"/api/v1/clusters/{ergebnis.concept.id}"
    return ergebnis.as_dict()


@router.post("/merge", summary="Zwei Cluster verschmelzen")
def merge(
    payload: ClusterMerge, runtime: RuntimeDep, settings: SettingsDep, actor: ActorDep
) -> dict[str, Any]:
    """Hängt die Kanten des Quellclusters um und entfernt es (§16.2)."""
    store = resolve_store(settings, payload.store)
    try:
        ergebnis = runtime.curation.merge(
            store=store, source_id=payload.source_id, target_id=payload.target_id, actor=actor
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.get("/{cluster_id:path}", summary="Mitglieder, verwandte Cluster, Zentroid-Alter")
def get_cluster(
    cluster_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die Detailansicht eines Clusters (§16.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    detail = runtime.catalog.cluster(cluster_id, store=gewaehlt)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cluster '{cluster_id}' gibt es im Store '{gewaehlt}' nicht.",
        )
    return detail.as_dict()


@router.patch("/{cluster_id:path}", summary="Titel und Beschreibung von Hand setzen")
def patch_cluster(
    cluster_id: str,
    payload: ClusterPatch,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Setzt Titel/Beschreibung und sperrt damit die automatische Neubetitelung (§13.2)."""
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.patch_cluster(
            cluster_id,
            store=gewaehlt,
            title=payload.title,
            description=payload.description,
            actor=actor,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.post("/{cluster_id:path}/members", summary="Mitglieder hinzufügen")
def add_members(
    cluster_id: str,
    payload: MembersAdd,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Hängt Konzepte in ein Cluster und hebt bestehende Ausschlüsse auf (§13.4, §16.2)."""
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnisse = runtime.curation.add_members(
            cluster_id, store=gewaehlt, concept_ids=payload.concept_ids, actor=actor
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"items": [ergebnis.as_dict() for ergebnis in ergebnisse]}


@router.post("/{cluster_id:path}/split", summary="Auswahl in ein neues Cluster ausgliedern")
def split(
    cluster_id: str,
    payload: ClusterSplit,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Entfernt eine Auswahl und legt sie als neues Cluster an — in einer Transaktion (§16.2)."""
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.split(
            cluster_id,
            store=gewaehlt,
            concept_ids=payload.concept_ids,
            title=payload.title,
            description=payload.description,
            actor=actor,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ergebnis.as_dict()


@router.delete("/{cluster_id}/members/{concept_id:path}", summary="Mitglied entfernen")
def remove_member(
    cluster_id: str,
    concept_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Entfernt ein Mitglied **mit** Ausschlussvermerk (§13.4).

    Der Vermerk ist der eigentliche Vorgang: Ohne ihn schriebe der nächste Clustering-Lauf
    dieselbe Zuordnung wieder, weil die gemessene Nähe unverändert ist.
    """
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.remove_member(
            cluster_id, concept_id, store=gewaehlt, actor=actor
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ergebnis.as_dict()
