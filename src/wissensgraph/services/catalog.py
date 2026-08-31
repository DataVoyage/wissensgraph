"""Der Lesepfad der Oberfläche — Listen, Detailansichten, Zahlen (§16.2, §17.2).

Was hier steht, beantwortet die Fragen der sechs Ansichten aus §17.2: Welche Konzepte gibt es,
was hängt an diesem einen, welche Cluster existieren und wie groß sind sie, was wartet auf
Bestätigung, wie viel hat der letzte Lauf gekostet.

Der Dienst schreibt nichts. Das ist keine Formalie: §24 verlangt für Stufe 11 "Läufe blockieren
die UI nie", und die Trennung zwischen Lesen und Schreiben ist die Voraussetzung dafür — der
Lesepfad hängt an keiner Transaktion, die ein Lauf hält. Geschrieben wird ausschließlich über
:mod:`wissensgraph.services.curation`.

Cluster sind hier keine eigene Gattung, sondern Konzepte vom Typ ``Cluster`` mit ``member``-Kanten
(§13.2 Schritt 4). Die Ansicht "Cluster-Arbeitsplatz" bekommt deshalb keine zweite Datenquelle,
sondern denselben Graphen aus einem anderen Blickwinkel.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.bridges import bridge_sources
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.ports.models import ModelRouter, UsageSummary
from wissensgraph.ports.repositories import (
    ConceptFilter,
    Neighbour,
    Page,
    UnitOfWorkFactory,
)

#: Wie viele Einträge die Kurationsliste ohne weitere Angabe zeigt (§17.2).
CURATION_QUEUE_LIMIT = 50


def konzept_dict(concept: Concept) -> dict[str, Any]:
    """Die Serialisierung eines Konzepts für die API.

    ``body`` fehlt absichtlich in Listen und steht nur in der Detailansicht: Eine Tabelle mit
    zweihundert Zeilen würde sonst zweihundert Fließtexte über die Leitung schicken, von denen
    keiner angezeigt wird.
    """
    return {
        "id": concept.id,
        "store": concept.store,
        "scope": concept.scope,
        "type": concept.type,
        "title": concept.title,
        "description": concept.description,
        "resource": concept.resource,
        "tags": list(concept.tags),
        "audience": list(concept.audience),
        "status": str(concept.status),
        "source_name": concept.source_name,
        "external_id": concept.external_id,
        "source_updated_at": _zeit(concept.source_updated_at),
        "generated_by": concept.generated_by,
        "verified_by": concept.verified_by,
        "verified_at": _zeit(concept.verified_at),
        "curated": concept.curated,
        "created_at": _zeit(concept.created_at),
        "updated_at": _zeit(concept.updated_at),
    }


def kante_dict(edge: Edge) -> dict[str, Any]:
    """Die Serialisierung einer Kante für die API (§17.2, visuelle Kodierung)."""
    return {
        "id": str(edge.id),
        "from_store": edge.from_store,
        "from_id": edge.from_id,
        "to_store": edge.to_store,
        "to_id": edge.to_id,
        "kind": edge.kind,
        "weight": edge.weight,
        "confidence": edge.confidence,
        "reasoning": edge.reasoning,
        "resolved": edge.resolved,
        "generated_by": edge.generated_by,
        "verified_by": edge.verified_by,
        "verified_at": _zeit(edge.verified_at),
        "curated": edge.curated,
        "created_at": _zeit(edge.created_at),
    }


def journal_dict(entry: ChangeEntry) -> dict[str, Any]:
    """Die Serialisierung eines Journaleintrags (§16.2, ``/concepts/{id}/history``)."""
    return {
        "id": entry.id,
        "change_type": str(entry.change_type),
        "actor": entry.actor,
        "concept_id": entry.concept_id,
        "edge_id": None if entry.edge_id is None else str(entry.edge_id),
        "run_id": None if entry.run_id is None else str(entry.run_id),
        "changed_at": _zeit(entry.changed_at),
        "detail": entry.detail,
        "undoable": entry.change_type in UNDOABLE_CHANGES,
    }


#: Änderungsarten, die sich zurücknehmen lassen (§17.3). Alles andere ist entweder eine
#: Feststellung über die Welt (``source_deleted`` — die Quelle hat gelöscht, das nimmt kein Undo
#: zurück) oder ein Vermerk ohne eigene Wirkung (``curation_conflict``).
UNDOABLE_CHANGES: frozenset[ChangeType] = frozenset(
    {
        ChangeType.EDGE_ADDED,
        ChangeType.EDGE_REMOVED,
        ChangeType.CLUSTER_ASSIGNED,
        ChangeType.CLUSTER_REMOVED,
        ChangeType.VERIFIED,
        ChangeType.REJECTED,
        ChangeType.CREATED,
        ChangeType.UPDATED,
        ChangeType.STATUS_CHANGED,
    }
)


def _zeit(wert: datetime | None) -> str | None:
    """ISO-8601 oder ``None`` — die Form, die ein JSON-Client ohne Konvertierung versteht."""
    return None if wert is None else wert.isoformat()


@dataclass(frozen=True)
class ConceptPage:
    """Eine Seite des Dokumentenbrowsers (§16.1, cursor-basiert)."""

    items: tuple[Concept, ...]
    next_cursor: str | None

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form."""
        return {
            "items": [konzept_dict(item) for item in self.items],
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True)
class ConceptDetail:
    """Ein Konzept mit allem, was die Detailansicht braucht (§16.2, §17.2)."""

    concept: Concept
    outgoing: tuple[Edge, ...]
    incoming: tuple[Edge, ...]
    clusters: tuple[Concept, ...]
    locked_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form inklusive ``body`` — hier wird er gebraucht."""
        return {
            **konzept_dict(self.concept),
            "body": self.concept.body,
            "content_hash": self.concept.content_hash,
            "outgoing": [kante_dict(edge) for edge in self.outgoing],
            "incoming": [kante_dict(edge) for edge in self.incoming],
            "clusters": [{"id": cluster.id, "title": cluster.title} for cluster in self.clusters],
            "locked_fields": list(self.locked_fields),
        }


@dataclass(frozen=True)
class ClusterSummary:
    """Ein Cluster mit seiner Mitgliederzahl (§16.2, ``GET /clusters``)."""

    concept: Concept
    member_count: int
    centroid_age_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form."""
        return {
            **konzept_dict(self.concept),
            "member_count": self.member_count,
            "centroid_age_seconds": self.centroid_age_seconds,
        }


@dataclass(frozen=True)
class ClusterDetail:
    """Mitglieder und verwandte Cluster eines Clusters (§16.2, §17.2 Ansicht 3)."""

    concept: Concept
    members: tuple[Concept, ...]
    related: tuple[tuple[Concept, float], ...]
    centroid_age_seconds: float | None
    manual_title: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form."""
        return {
            **konzept_dict(self.concept),
            "description": self.concept.description,
            "members": [konzept_dict(item) for item in self.members],
            "related": [
                {"id": cluster.id, "title": cluster.title, "similarity": wert}
                for cluster, wert in self.related
            ],
            "centroid_age_seconds": self.centroid_age_seconds,
            "manual_title": self.manual_title,
        }


@dataclass(frozen=True)
class CurationTask:
    """Ein offener Posten der Kurationsliste (§16.2, ``/curation/queue``).

    Vier Arten stehen in §16.2: unbestätigte Kanten, ``supersedes``-Vorschläge, Relabel-Vorschläge
    und Cluster-Vorschläge. Sie kommen aus zwei Quellen — die ersten beiden aus den Kanten selbst,
    die letzten beiden aus dem Journal —, tragen aber dieselbe Form, damit die Oberfläche sie in
    *einer* Liste abarbeiten kann.
    """

    kind: str
    store: str
    edge: Edge | None = None
    concepts: tuple[Concept, ...] = ()
    entry: ChangeEntry | None = None
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form."""
        return {
            "kind": self.kind,
            "store": self.store,
            "confidence": self.confidence,
            "edge": None if self.edge is None else kante_dict(self.edge),
            "concepts": [konzept_dict(item) for item in self.concepts],
            "entry": None if self.entry is None else journal_dict(self.entry),
        }


#: Die Arten von Kurationsaufgaben (§16.2). ``unverified_edge`` und ``supersedes`` sind beides
#: Kanten; getrennt, weil §14.4 an ``supersedes`` eine Folgewirkung hängt, die ein Mensch
#: entscheiden muss.
TASK_UNVERIFIED_EDGE = "unverified_edge"
TASK_SUPERSEDES = "supersedes"
TASK_RELABEL = "relabel"
TASK_CLUSTER_SUGGESTION = "cluster_suggestion"


@dataclass(frozen=True)
class StoreStats:
    """Die Zahlen eines Stores (§16.2, ``/stats``)."""

    store: str
    concepts: int
    edges: int
    clusters: int
    loose: int
    by_scope: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form."""
        return {
            "store": self.store,
            "concepts": self.concepts,
            "edges": self.edges,
            "clusters": self.clusters,
            "loose": self.loose,
            "by_scope": dict(self.by_scope),
            "by_type": dict(self.by_type),
            "by_status": dict(self.by_status),
        }


class CatalogService:
    """Der lesende Zugang zum Graphen für Oberfläche und Agent."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        *,
        router: ModelRouter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration.
            unit_of_work: Fabrik für Transaktionen je Store.
            router: Der Model-Router. **Optional**, und wie beim Graphdienst ist das die Aussage:
                Ohne ihn fehlen Vektor-Nachbarn und Zentroid-Alter, alles andere ist da. Eine
                Dokumentenliste soll nicht daran scheitern, dass kein Modell erreichbar ist.
            clock: Zeitquelle für das Zentroid-Alter.
        """
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._router = router
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def model_key(self) -> str | None:
        """Der Modellschlüssel, unter dem Vektoren in diesem System liegen (§11.7)."""
        if self._router is None:
            return None
        return self._router.describe(defaults.TASK_EMBEDDING).model_key

    # -- Konzepte ----------------------------------------------------------------

    def concepts(
        self,
        *,
        store: str,
        filter: ConceptFilter | None = None,
        limit: int = defaults.SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> ConceptPage:
        """Eine Seite des Dokumentenbrowsers (§16.2, §17.2 Ansicht 2)."""
        # Die Schwelle kommt immer aus der Konfiguration: Sie ist keine Angabe des Aufrufers,
        # sondern die Definition davon, was "lose" in dieser Installation heißt (§15.4).
        gesucht = replace(
            filter or ConceptFilter(), loose_threshold=self._settings.orphans.loose_threshold
        )
        with self._unit_of_work(store) as uow:
            seite: Page = uow.concepts.page(gesucht, limit=limit, cursor=cursor)
        return ConceptPage(items=seite.items, next_cursor=seite.next_cursor)

    def concept(self, concept_id: str, *, store: str) -> ConceptDetail | None:
        """Ein Konzept mit Kanten, Cluster-Zugehörigkeit und Provenienz (§16.2).

        Die eingehenden Kanten werden über die Store-Grenze hinweg rekonstruiert: Der geteilte
        Store weiß nicht, dass es persönliche Konzepte gibt (§12.1). Wer eine geteilte Seite
        ansieht, soll trotzdem erfahren, dass eine eigene Notiz auf sie zeigt.
        """
        with self._unit_of_work(store) as uow:
            concept = uow.concepts.get(concept_id)
            if concept is None:
                return None
            ausgehend = uow.edges.list_outgoing(concept_id)
            eingehend = list(uow.edges.list_incoming(concept_id))
            cluster_ids = [
                edge.from_id
                for edge in eingehend
                if edge.kind == defaults.EDGE_KIND_MEMBER and edge.to_id == concept_id
            ]
            cluster = uow.concepts.get_many(cluster_ids)

        for quell_store in bridge_sources(store, self._settings.stores):
            with self._unit_of_work(quell_store) as fremd:
                eingehend.extend(fremd.edges.bridges_into(to_store=store, to_ids=(concept_id,)))

        return ConceptDetail(
            concept=concept,
            outgoing=ausgehend,
            incoming=tuple(eingehend),
            clusters=tuple(sorted(cluster, key=lambda item: item.id)),
            locked_fields=self.locked_fields(concept),
        )

    def locked_fields(self, concept: Concept) -> tuple[str, ...]:
        """Die Felder, die an diesem Konzept nicht geändert werden dürfen (§17.3, §17.4).

        §17.3 verlangt: "Inhaltsfelder quellgespiegelter Konzepte sind sichtbar gesperrt, nicht
        nur schreibgeschützt." Sichtbar heißt: Die Oberfläche muss es *wissen*, bevor jemand
        tippt — sie darf es nicht erst an einer abgelehnten Anfrage merken. Deshalb steht die
        Antwort in der Detailansicht und nicht nur im Fehlerfall.

        Maßgeblich ist die Herkunft des einzelnen Konzepts, nicht allein die Taxonomie: Eine
        lokal angelegte Notiz vom Typ ``Note`` ist frei, dieselbe ID mit ``source_name`` wäre es
        nicht.
        """
        if not concept.is_from_source:
            return ()
        return LOCKED_CONTENT_FIELDS

    # -- Historie und Nachbarn ---------------------------------------------------

    def history(self, concept_id: str, *, store: str) -> tuple[ChangeEntry, ...]:
        """Die Journaleinträge eines Konzepts, neueste zuerst (§16.2)."""
        with self._unit_of_work(store) as uow:
            return uow.changes.entries_for(concept_id)

    def journal(self, *, store: str, limit: int = CURATION_QUEUE_LIMIT) -> tuple[ChangeEntry, ...]:
        """Die jüngsten Journaleinträge eines Stores (§17.2, §17.3)."""
        with self._unit_of_work(store) as uow:
            return uow.changes.recent(limit=limit)

    def similar(
        self, concept_id: str, *, store: str, limit: int = defaults.SEARCH_LIMIT
    ) -> tuple[tuple[Concept, float], ...]:
        """Vektor-Nachbarn eines Konzepts, unabhängig von Kanten (§16.2).

        Der Endpunkt ist die Antwort auf "was ähnelt dem hier, ohne dass es jemand verknüpft hat" —
        und damit die Vorstufe jeder Kuration, die eine fehlende Kante entdeckt.

        Ohne Router ist die Antwort leer und nicht etwa ein Fehler: Ohne Embedding-Modell gibt es
        keine Nachbarschaft, über die sich etwas sagen ließe (§11.5).
        """
        model_key = self.model_key
        if model_key is None:
            return ()
        with self._unit_of_work(store) as uow:
            nachbarn: tuple[Neighbour, ...] = uow.embeddings.neighbours(
                concept_id=concept_id, model_key=model_key, k=limit
            )
            konzepte = {
                item.id: item
                for item in uow.concepts.get_many([nachbar.concept_id for nachbar in nachbarn])
            }
        return tuple(
            (konzepte[nachbar.concept_id], nachbar.similarity)
            for nachbar in nachbarn
            if nachbar.concept_id in konzepte
        )

    # -- Cluster ------------------------------------------------------------------

    def clusters(
        self,
        *,
        store: str,
        scope: str | None = None,
        limit: int = defaults.SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> tuple[tuple[ClusterSummary, ...], str | None]:
        """Die Cluster eines Stores mit ihrer Mitgliederzahl (§16.2)."""
        with self._unit_of_work(store) as uow:
            seite = uow.concepts.page(
                ConceptFilter(scope=scope, concept_type=defaults.CONCEPT_TYPE_CLUSTER),
                limit=limit,
                cursor=cursor,
            )
            zusammenfassungen = tuple(
                ClusterSummary(
                    concept=cluster,
                    member_count=len(
                        [
                            edge
                            for edge in uow.edges.list_outgoing(cluster.id)
                            if edge.kind == defaults.EDGE_KIND_MEMBER
                        ]
                    ),
                )
                for cluster in seite.items
            )
        return zusammenfassungen, seite.next_cursor

    def cluster(self, cluster_id: str, *, store: str) -> ClusterDetail | None:
        """Mitglieder, verwandte Cluster und Zentroid-Alter (§16.2, §17.2 Ansicht 3).

        Das Zentroid-Alter beantwortet eine Frage, die in der Ansicht sonst niemand stellen kann:
        Ob die Mitglieder, die dort stehen, überhaupt noch zu dem Mittelpunkt passen, gegen den
        das nächste Clustering messen wird (§13.2 Schritt 5).
        """
        model_key = self.model_key
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.get(cluster_id)
            if cluster is None or cluster.type != defaults.CONCEPT_TYPE_CLUSTER:
                return None
            ausgehend = uow.edges.list_outgoing(cluster_id)
            mitglieder = uow.concepts.get_many(
                [edge.to_id for edge in ausgehend if edge.kind == defaults.EDGE_KIND_MEMBER]
            )
            verwandt_kanten = [
                edge for edge in ausgehend if edge.kind == defaults.EDGE_KIND_RELATED
            ]
            verwandte = {
                item.id: item
                for item in uow.concepts.get_many([edge.to_id for edge in verwandt_kanten])
            }
            zentroid = (
                None
                if model_key is None
                else next(
                    (
                        item
                        for item in uow.clusters.centroids(model_key=model_key)
                        if item.cluster_id == cluster_id
                    ),
                    None,
                )
            )

        alter = None
        if zentroid is not None and zentroid.updated_at is not None:
            alter = (self._clock() - zentroid.updated_at).total_seconds()
        return ClusterDetail(
            concept=cluster,
            members=tuple(sorted(mitglieder, key=lambda item: item.id)),
            related=tuple(
                (verwandte[edge.to_id], edge.weight or 0.0)
                for edge in verwandt_kanten
                if edge.to_id in verwandte
            ),
            centroid_age_seconds=alter,
            manual_title=cluster.curated,
        )

    # -- Kuration und Betrieb -----------------------------------------------------

    def curation_queue(
        self, *, store: str, limit: int = CURATION_QUEUE_LIMIT
    ) -> tuple[CurationTask, ...]:
        """Die offenen Posten eines Stores, nach Confidence sortiert (§16.2, §17.2 Ansicht 4)."""
        aufgaben: list[CurationTask] = []
        with self._unit_of_work(store) as uow:
            for edge in uow.edges.unverified(limit=limit):
                beteiligte = uow.concepts.get_many([edge.from_id, edge.to_id])
                aufgaben.append(
                    CurationTask(
                        kind=(
                            TASK_SUPERSEDES
                            if edge.kind == defaults.EDGE_KIND_SUPERSEDES
                            else TASK_UNVERIFIED_EDGE
                        ),
                        store=store,
                        edge=edge,
                        concepts=tuple(sorted(beteiligte, key=lambda item: item.id)),
                        confidence=edge.confidence,
                    )
                )
        return tuple(aufgaben)

    def stats(self) -> tuple[StoreStats, ...]:
        """Konzept-, Kanten- und Cluster-Zahlen je Store und Scope (§16.2)."""
        ergebnis: list[StoreStats] = []
        for store in self._settings.stores:
            with self._unit_of_work(store) as uow:
                zeilen = uow.concepts.counts()
                lose = uow.concepts.loose(threshold=self._settings.orphans.loose_threshold)
                kanten = uow.edges.count()
            nach_scope: dict[str, int] = {}
            nach_typ: dict[str, int] = {}
            nach_status: dict[str, int] = {}
            for zeile in zeilen:
                nach_scope[zeile.scope] = nach_scope.get(zeile.scope, 0) + zeile.count
                nach_typ[zeile.type] = nach_typ.get(zeile.type, 0) + zeile.count
                nach_status[zeile.status] = nach_status.get(zeile.status, 0) + zeile.count
            ergebnis.append(
                StoreStats(
                    store=store,
                    concepts=sum(zeile.count for zeile in zeilen),
                    edges=kanten,
                    clusters=nach_typ.get(defaults.CONCEPT_TYPE_CLUSTER, 0),
                    loose=len(lose),
                    by_scope=nach_scope,
                    by_type=nach_typ,
                    by_status=nach_status,
                )
            )
        return tuple(ergebnis)

    def runs(
        self, *, store: str, kind: RunKind | None = None, limit: int = defaults.RUNS_LIST_LIMIT
    ) -> tuple[Run, ...]:
        """Die jüngsten Läufe eines Stores (§16.2)."""
        with self._unit_of_work(store) as uow:
            return uow.runs.recent(kind=kind, limit=limit)

    def run(self, run_id: UUID) -> tuple[str, Run] | None:
        """Ein Lauf samt seinem Store; ``None``, wenn ihn keiner der Stores kennt.

        Über alle Stores gesucht, weil die Lauf-ID aus einer URL kommt und ein Aufrufer nicht
        wissen muss, in welcher Datenbank der Lauf verbucht wurde. Der Store steht im Ergebnis,
        damit ein Folgeaufruf ihn nicht erneut suchen muss.
        """
        for store in self._settings.stores:
            with self._unit_of_work(store) as uow:
                lauf = uow.runs.get(run_id)
            if lauf is not None:
                return store, lauf
        return None

    def usage(
        self, *, store: str, run_id: UUID | None = None, limit: int = defaults.MODEL_USAGE_LIMIT
    ) -> Sequence[UsageSummary]:
        """Modellnutzung je Lauf und Task (§16.2, ``/models/usage``)."""
        with self._unit_of_work(store) as uow:
            return uow.model_calls.usage(run_id=run_id, limit=limit)


#: Die Felder, die an einem quellgespiegelten Konzept gesperrt sind (§10.4, §17.4).
#: ``title``, ``description`` und ``body`` gehören der Quelle; ``status``, ``tags`` und die
#: Verifikation gehören dem Menschen — §17.4 führt beides getrennt auf.
LOCKED_CONTENT_FIELDS: tuple[str, ...] = ("title", "description", "body", "resource")


__all__ = [
    "CURATION_QUEUE_LIMIT",
    "LOCKED_CONTENT_FIELDS",
    "TASK_CLUSTER_SUGGESTION",
    "TASK_RELABEL",
    "TASK_SUPERSEDES",
    "TASK_UNVERIFIED_EDGE",
    "UNDOABLE_CHANGES",
    "CatalogService",
    "ClusterDetail",
    "ClusterSummary",
    "ConceptDetail",
    "ConceptPage",
    "CurationTask",
    "StoreStats",
]
