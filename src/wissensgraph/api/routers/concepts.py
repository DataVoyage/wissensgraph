"""Konzepte und Bestandszahlen (§16.2).

Die Endpunkte sind dünn: Sie übersetzen Anfrageparameter in einen :class:`ConceptFilter`, rufen
den Katalog- oder Kurationsdienst und geben dessen Antwort zurück. Jede Regel darüber, was
kuratierbar ist und was gesperrt bleibt, steht im Dienst — nicht hier und erst recht nicht in der
Oberfläche (§17.1).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from wissensgraph.api.dependencies import ActorDep, RuntimeDep, SettingsDep, resolve_store
from wissensgraph.api.schemas import ConceptCreate, ConceptPatch
from wissensgraph.config import defaults
from wissensgraph.ports.repositories import ConceptFilter
from wissensgraph.services.catalog import journal_dict, konzept_dict
from wissensgraph.services.curation import CurationError, NotFoundError

router = APIRouter(prefix="/api/v1", tags=["Konzepte"])

#: Wie viele Konzepte eine Seite ohne weitere Angabe enthält (§16.1).
PAGE_LIMIT = 50


@router.get("/stats", tags=["Betrieb"], summary="Konzept-, Kanten- und Cluster-Zahlen")
def stats(runtime: RuntimeDep, actor: ActorDep) -> dict[str, Any]:
    """Die Bestandszahlen je Store und Scope (§16.2)."""
    del actor
    return {"stores": [item.as_dict() for item in runtime.catalog.stats()]}


@router.get("/concepts", summary="Konzepte filtern und blättern")
def list_concepts(
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query()] = None,
    cluster_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    orphan: Annotated[bool | None, Query()] = None,
    curated: Annotated[bool | None, Query()] = None,
    unverified: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = PAGE_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die Facetten des Dokumentenbrowsers, cursor-basiert (§16.1, §16.2, §17.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    seite = runtime.catalog.concepts(
        store=gewaehlt,
        filter=ConceptFilter(
            scope=scope,
            concept_type=type,
            status=status_filter,
            query=q,
            cluster_id=cluster_id,
            source_name=source,
            orphan=orphan,
            curated=curated,
            unverified=unverified,
        ),
        limit=limit,
        cursor=cursor,
    )
    return {"store": gewaehlt, **seite.as_dict()}


@router.get("/concepts/{concept_id:path}/history", summary="Änderungsjournal eines Konzepts")
def history(
    concept_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die ``change_log``-Einträge eines Konzepts, neueste zuerst (§16.2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    eintraege = runtime.catalog.history(concept_id, store=gewaehlt)
    return {"items": [journal_dict(eintrag) for eintrag in eintraege]}


@router.get("/concepts/{concept_id:path}/similar", summary="Vektor-Nachbarn eines Konzepts")
def similar(
    concept_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = defaults.SEARCH_LIMIT,
) -> dict[str, Any]:
    """Ähnliche Konzepte, unabhängig von Kanten (§16.2).

    Ohne Embedding-Modell ist die Liste leer und der ``model_key`` ``null``. Das ist ein
    zulässiger Betriebszustand und kein Fehler (§11.5) — die Antwort sagt es nur ausdrücklich,
    statt Leere als "nichts Ähnliches" auszugeben.
    """
    del actor
    gewaehlt = resolve_store(settings, store)
    treffer = runtime.catalog.similar(concept_id, store=gewaehlt, limit=limit)
    return {
        "model_key": runtime.catalog.model_key,
        "items": [{**konzept_dict(concept), "similarity": wert} for concept, wert in treffer],
    }


@router.get("/concepts/{concept_id:path}", summary="Konzept mit Kanten, Clustern und Provenienz")
def get_concept(
    concept_id: str,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Die Detailansicht (§16.2, §17.2 Ansicht 2)."""
    del actor
    gewaehlt = resolve_store(settings, store)
    detail = runtime.catalog.concept(concept_id, store=gewaehlt)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Konzept '{concept_id}' gibt es im Store '{gewaehlt}' nicht.",
        )
    return detail.as_dict()


@router.post("/concepts", status_code=status.HTTP_201_CREATED, summary="Konzept anlegen")
def create_concept(
    payload: ConceptCreate, runtime: RuntimeDep, actor: ActorDep, response: Response
) -> dict[str, Any]:
    """Legt ein Konzept an — ausschließlich im ``personal``-Store (§16.2, §17.4)."""
    try:
        ergebnis = runtime.curation.create_concept(
            scope=payload.scope,
            concept_type=payload.type,
            title=payload.title,
            description=payload.description,
            body=payload.body,
            tags=payload.tags,
            actor=actor,
        )
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    assert ergebnis.concept is not None
    response.headers["Location"] = f"/api/v1/concepts/{ergebnis.concept.id}"
    return ergebnis.as_dict()


@router.patch("/concepts/{concept_id:path}", summary="Konzept ändern")
def patch_concept(
    concept_id: str,
    payload: ConceptPatch,
    runtime: RuntimeDep,
    settings: SettingsDep,
    actor: ActorDep,
    store: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Ändert ein Konzept; an gespiegelten Inhalten nur die kuratierbaren Felder (§17.4).

    Ein Versuch auf ein gesperrtes Feld endet mit ``409`` und nicht mit ``403``: Die Anfrage ist
    nicht unberechtigt, sie steht im Widerspruch zum Zustand des Konzepts. Die Meldung nennt, was
    an diesem Konzept änderbar wäre.
    """
    gewaehlt = resolve_store(settings, store)
    try:
        ergebnis = runtime.curation.patch_concept(
            concept_id, store=gewaehlt, changes=payload.changes(), actor=actor
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ergebnis.as_dict()
