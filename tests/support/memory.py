"""Speicherresidente Umsetzung der Persistenz-Ports (§4.2).

Diese Fakes sind kein Zugeständnis an bequemere Tests, sondern der Beweis, dass die Ports
tragen: Wenn sich :class:`ConceptService` gegen eine Umsetzung ohne jede Datenbank betreiben
lässt, enthält er tatsächlich keine Infrastrukturannahmen (Leitprinzip 13).

Die Transaktionssemantik ist nachgebildet, nicht weggelassen — beim Betreten wird ein
Schnappschuss genommen und bei einer Ausnahme wiederhergestellt. Nur so lässt sich §10.2 Regel 5
("Konzept, Kanten und change_log gemeinsam") ohne PostgreSQL prüfen.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.ports.models import ModelCall, UsageSummary
from wissensgraph.ports.repositories import (
    AssignmentCandidate,
    Centroid,
    ConceptCount,
    ConceptFilter,
    LexicalHit,
    LooseConcept,
    Neighbour,
    Page,
)
from wissensgraph.ports.runs import SourceBusy, SourceCursorState
from wissensgraph.ports.sources import Cursor

#: Ersatzzeitpunkt, wenn ein Kantenentwurf keinen Erzeugungszeitpunkt trägt. In der Datenbank
#: setzt ``DEFAULT now()`` den Wert; hier braucht es einen deterministischen Ersatz.
_EPOCHE = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class _Kandidat:
    """Eine Zeile aus ``cluster_assignment_candidates`` — veränderlich wegen ``seen_count``."""

    score: float
    seen_count: int
    last_run: UUID
    excluded: bool


@dataclass
class StoreState:
    """Der gesamte Inhalt eines Stores — genau das, was eine Transaktion umfasst."""

    concepts: dict[str, Concept] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    changes: list[ChangeEntry] = field(default_factory=list)
    runs: dict[UUID, Run] = field(default_factory=dict)
    cursors: dict[str, SourceCursorState] = field(default_factory=dict)
    #: Vektor und Quell-Hash je (Konzept, Modellschlüssel) — der Inhalt von
    #: ``concept_embeddings`` (§7.4). Der Hash steht daneben, weil §13.1 an ihm entscheidet,
    #: ob neu eingebettet werden muss.
    embeddings: dict[tuple[str, str], tuple[tuple[float, ...], str]] = field(default_factory=dict)
    centroids: dict[str, Centroid] = field(default_factory=dict)
    candidates: dict[tuple[str, str], _Kandidat] = field(default_factory=dict)
    model_calls: list[ModelCall] = field(default_factory=list)
    #: Verworfene Kantentripel (§16.2, Migration 0003) — der Schlüssel ist das Tripel selbst.
    rejections: dict[tuple[str, str, str, str, str], str] = field(default_factory=dict)
    #: Der Zähler der ``BIGSERIAL``-Spalte ``change_log.id``. Er steht hier und nicht als
    #: Klassenvariable, weil jeder Store seine eigene Sequenz hat.
    next_change_id: int = 1


class MemoryConceptRepository:
    """Konzepte eines Stores im Speicher."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def get(self, concept_id: str) -> Concept | None:
        return self._state.concepts.get(concept_id)

    def exists(self, concept_id: str) -> bool:
        return concept_id in self._state.concepts

    def existing_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        return frozenset(item for item in concept_ids if item in self._state.concepts)

    def resolvable_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        return frozenset(
            item
            for item in concept_ids
            if item in self._state.concepts
            and self._state.concepts[item].status is not ConceptStatus.TOMBSTONE
        )

    def get_many(self, concept_ids: Sequence[str]) -> tuple[Concept, ...]:
        return tuple(
            self._state.concepts[item] for item in concept_ids if item in self._state.concepts
        )

    def search_lexical(self, query: str, *, limit: int) -> tuple[LexicalHit, ...]:
        """Eine bewusst grobe Nachbildung: Teilzeichenkette statt Volltext und Trigramm.

        Was sich damit prüfen lässt, ist der Vertrag — Grabsteine bleiben draußen, die Zahl der
        Treffer ist begrenzt, die Reihenfolge ist stabil. Was PostgreSQL daraus macht, prüft der
        Integrationstest; ein Fake, der ``ts_rank`` nachzubauen versuchte, prüfte am Ende nur
        seine eigene Nachbildung.
        """
        begriff = query.strip().casefold()
        if not begriff:
            return ()
        treffer = [
            concept
            for concept in self._state.concepts.values()
            if concept.status is not ConceptStatus.TOMBSTONE
            and begriff
            in " ".join(filter(None, (concept.title, concept.description, concept.body))).casefold()
        ]
        treffer.sort(key=lambda concept: concept.id)
        return tuple(
            LexicalHit(concept=concept, score=1.0 / (1 + rang))
            for rang, concept in enumerate(treffer[:limit])
        )

    def in_scope(self, scope: str, *, concept_type: str | None = None) -> tuple[Concept, ...]:
        treffer = [
            concept
            for concept in self._state.concepts.values()
            if concept.scope == scope
            and concept.status is not ConceptStatus.TOMBSTONE
            and (concept_type is None or concept.type == concept_type)
        ]
        treffer.sort(key=lambda concept: concept.id)
        return tuple(treffer)

    def loose(self, *, threshold: int, scope: str | None = None) -> tuple[LooseConcept, ...]:
        """Nachbildung von ``v_loose_concepts``: nur nicht-strukturelle Kanten zählen (§7.7).

        Die Sicht zählt ``member`` bewusst nicht mit. Ein Konzept, das ausschließlich in einem
        Cluster hängt, ist thematisch weiterhin unvernetzt — und genau darum geht es §15.1.
        """
        treffer: list[LooseConcept] = []
        for concept in self._state.concepts.values():
            if concept.status is ConceptStatus.TOMBSTONE:
                continue
            if scope is not None and concept.scope != scope:
                continue
            grad = sum(
                1
                for edge in self._state.edges
                if edge.kind != defaults.EDGE_KIND_MEMBER
                and (
                    (edge.from_store == concept.store and edge.from_id == concept.id)
                    or (edge.to_store == concept.store and edge.to_id == concept.id)
                )
            )
            if grad < threshold:
                treffer.append(
                    LooseConcept(
                        id=concept.id,
                        scope=concept.scope,
                        type=concept.type,
                        title=concept.title,
                        semantic_degree=grad,
                    )
                )
        treffer.sort(key=lambda item: (item.semantic_degree, item.id))
        return tuple(treffer)

    def page(self, filter: ConceptFilter, *, limit: int, cursor: str | None = None) -> Page:
        """Nachbildung der Facettenfilterung aus §16.2 — dieselbe Auswahl, ohne SQL."""
        treffer: list[Concept] = []
        for concept in sorted(self._state.concepts.values(), key=lambda item: item.id):
            if cursor is not None and concept.id <= cursor:
                continue
            if not filter.include_tombstones and concept.status is ConceptStatus.TOMBSTONE:
                continue
            if filter.scope is not None and concept.scope != filter.scope:
                continue
            if filter.concept_type is not None and concept.type != filter.concept_type:
                continue
            if filter.status is not None and str(concept.status) != filter.status:
                continue
            if filter.source_name is not None and concept.source_name != filter.source_name:
                continue
            if filter.curated is not None and concept.curated != filter.curated:
                continue
            if filter.unverified is not None and (concept.verified_at is None) != filter.unverified:
                continue
            if filter.query and not self._passt_zur_suche(concept, filter.query):
                continue
            if filter.cluster_id is not None and not any(
                edge.from_store == self._store
                and edge.from_id == filter.cluster_id
                and edge.kind == defaults.EDGE_KIND_MEMBER
                and edge.to_id == concept.id
                for edge in self._state.edges
            ):
                continue
            if filter.orphan is not None:
                lose = {item.id for item in self.loose(threshold=filter.loose_threshold)}
                if (concept.id in lose) != filter.orphan:
                    continue
            treffer.append(concept)
            if len(treffer) > limit:
                break
        weiter = len(treffer) > limit
        seite = tuple(treffer[:limit])
        return Page(items=seite, next_cursor=seite[-1].id if weiter and seite else None)

    @staticmethod
    def _passt_zur_suche(concept: Concept, query: str) -> bool:
        begriff = query.casefold()
        return any(
            begriff in (feld or "").casefold() for feld in (concept.title, concept.description)
        )

    def counts(self) -> tuple[ConceptCount, ...]:
        gezaehlt: dict[tuple[str, str, str], int] = {}
        for concept in self._state.concepts.values():
            schluessel = (concept.scope, concept.type, str(concept.status))
            gezaehlt[schluessel] = gezaehlt.get(schluessel, 0) + 1
        return tuple(
            ConceptCount(scope=scope, type=typ, status=status, count=anzahl)
            for (scope, typ, status), anzahl in sorted(gezaehlt.items())
        )

    def delete(self, concept_id: str) -> bool:
        return self._state.concepts.pop(concept_id, None) is not None

    def save(self, concept: Concept) -> None:
        if concept.store != self._store:
            raise ValueError(
                f"Konzept '{concept.id}' gehört zu '{concept.store}', nicht zu '{self._store}'."
            )
        vorhanden = self._state.concepts.get(concept.id)
        if vorhanden is not None:
            # Wie die Datenbank: 'created_at' bleibt beim Update stehen.
            concept = concept.model_copy(update={"created_at": vorhanden.created_at})
        self._state.concepts[concept.id] = concept


class MemoryEdgeRepository:
    """Kanten eines Stores im Speicher."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def list_outgoing(self, concept_id: str) -> tuple[Edge, ...]:
        return tuple(
            edge
            for edge in self._state.edges
            if edge.from_store == self._store and edge.from_id == concept_id
        )

    def list_incoming(self, concept_id: str) -> tuple[Edge, ...]:
        return tuple(
            edge
            for edge in self._state.edges
            if edge.to_store == self._store and edge.to_id == concept_id
        )

    def neighbourhood(self, concept_ids: Sequence[str]) -> tuple[Edge, ...]:
        gesucht = frozenset(concept_ids)
        return tuple(
            edge
            for edge in self._state.edges
            if (edge.from_store == self._store and edge.from_id in gesucht)
            or (edge.to_store == self._store and edge.to_id in gesucht)
        )

    def bridges_into(self, *, to_store: str, to_ids: Sequence[str]) -> tuple[Edge, ...]:
        gesucht = frozenset(to_ids)
        return tuple(
            edge
            for edge in self._state.edges
            if edge.from_store == self._store
            and edge.to_store == to_store
            and edge.to_id in gesucht
        )

    def foreign_targets(self) -> dict[str, frozenset[str]]:
        gruppiert: dict[str, set[str]] = {}
        for edge in self._state.edges:
            if edge.from_store == self._store and edge.to_store != self._store:
                gruppiert.setdefault(edge.to_store, set()).add(edge.to_id)
        return {store: frozenset(ids) for store, ids in gruppiert.items()}

    def unresolved_targets(self) -> frozenset[str]:
        return frozenset(
            edge.to_id
            for edge in self._state.edges
            if edge.from_store == self._store and not edge.resolved
        )

    def attach_to_store(self, *, to_store: str, to_ids: frozenset[str]) -> int:
        vorhandene_tripel = {edge.triple for edge in self._state.edges}
        anzahl = 0
        for index, edge in enumerate(self._state.edges):
            passt = (
                edge.from_store == self._store
                and not edge.resolved
                and edge.to_store != to_store
                and edge.to_id in to_ids
            )
            if not passt:
                continue
            neu = (edge.from_store, edge.from_id, to_store, edge.to_id, edge.kind)
            if neu in vorhandene_tripel:
                continue
            self._state.edges[index] = edge.model_copy(
                update={"to_store": to_store, "resolved": True}
            )
            vorhandene_tripel.add(neu)
            anzahl += 1
        return anzahl

    def set_foreign_resolution(self, *, to_store: str, resolvable: frozenset[str]) -> int:
        anzahl = 0
        for index, edge in enumerate(self._state.edges):
            if edge.from_store != self._store or edge.to_store != to_store:
                continue
            soll = edge.to_id in resolvable
            if edge.resolved != soll:
                self._state.edges[index] = edge.model_copy(update={"resolved": soll})
                anzahl += 1
        return anzahl

    def replace_generated(
        self, *, from_id: str, generated_by: Sequence[str], drafts: Sequence[EdgeDraft]
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        besitz = frozenset(generated_by)
        vorhanden = self.list_outgoing(from_id)
        fremd = {
            edge.triple for edge in vorhanden if edge.curated or edge.generated_by not in besitz
        }
        eigene = {
            edge.triple: edge
            for edge in vorhanden
            if not edge.curated and edge.generated_by in besitz
        }
        gewuenscht = {draft.triple: draft for draft in drafts if draft.triple not in fremd}

        hinzugefuegt: list[Edge] = []
        for triple, draft in gewuenscht.items():
            if triple in eigene:
                self._aktualisieren(eigene[triple], draft)
                continue
            edge = Edge(
                **draft.model_dump(),
                id=new_edge_id(),
                created_at=draft.generated_at or _EPOCHE,
            )
            self._state.edges.append(edge)
            hinzugefuegt.append(edge)

        entfernt = tuple(edge for triple, edge in eigene.items() if triple not in gewuenscht)
        for edge in entfernt:
            self._state.edges.remove(edge)

        return tuple(hinzugefuegt), entfernt

    def _aktualisieren(self, vorhanden: Edge, draft: EdgeDraft) -> None:
        """Schreibt die veränderlichen Felder einer bestehenden Kante fort — wie das SQL-Pendant.

        Ohne diesen Schritt wäre der Fake bequemer als die Datenbank: Ein Ziel, das inzwischen
        existiert, bekäme sein ``resolved = true`` nie.
        """
        veraenderlich = (
            "resolved",
            "weight",
            "confidence",
            "reasoning",
            "generated_at",
            "generated_by",
        )
        neu = {name: getattr(draft, name) for name in veraenderlich}
        if all(getattr(vorhanden, name) == wert for name, wert in neu.items()):
            return
        index = self._state.edges.index(vorhanden)
        self._state.edges[index] = vorhanden.model_copy(update=neu)

    def add(self, draft: EdgeDraft) -> Edge | None:
        if draft.from_store != self._store:
            raise ValueError(f"Kante gehört zu '{draft.from_store}', nicht zu '{self._store}'.")
        if any(edge.triple == draft.triple for edge in self._state.edges):
            return None
        edge = Edge(
            **draft.model_dump(), id=new_edge_id(), created_at=draft.generated_at or _EPOCHE
        )
        self._state.edges.append(edge)
        return edge

    def kinds_between(self, *, from_id: str, to_id: str) -> frozenset[str]:
        return frozenset(
            edge.kind
            for edge in self._state.edges
            if edge.from_store == self._store
            and edge.to_store == self._store
            and {edge.from_id, edge.to_id} == {from_id, to_id}
        )

    # -- Kuration (§16.2) ---------------------------------------------------------

    def count(self) -> int:
        return sum(1 for edge in self._state.edges if edge.from_store == self._store)

    def get(self, edge_id: UUID) -> Edge | None:
        return next((edge for edge in self._state.edges if edge.id == edge_id), None)

    def remove(self, edge_id: UUID) -> Edge | None:
        kante = self.get(edge_id)
        if kante is not None:
            self._state.edges.remove(kante)
        return kante

    def verify(self, *, edge_id: UUID, actor: str, now: datetime) -> Edge | None:
        kante = self.get(edge_id)
        if kante is None:
            return None
        index = self._state.edges.index(kante)
        neu = kante.model_copy(update={"verified_by": actor, "verified_at": now, "curated": True})
        self._state.edges[index] = neu
        return neu

    def unverified(self, *, limit: int, kinds: Sequence[str] = ()) -> tuple[Edge, ...]:
        treffer = [
            edge
            for edge in self._state.edges
            if edge.from_store == self._store
            and edge.generated_by is not None
            and not edge.curated
            and edge.verified_at is None
            and (not kinds or edge.kind in kinds)
        ]
        treffer.sort(key=lambda edge: (-(edge.confidence or 0.0), str(edge.id)))
        return tuple(treffer[:limit])

    def retarget(self, *, from_id: str, to_id: str, kind: str | None = None) -> int:
        umgehaengt = 0
        for kante in list(self._state.edges):
            if kind is not None and kante.kind != kind:
                continue
            if kante.from_store == self._store and kante.from_id == from_id:
                neu = kante.model_copy(update={"from_id": to_id})
            elif kante.to_store == self._store and kante.to_id == from_id:
                neu = kante.model_copy(update={"to_id": to_id})
            else:
                continue
            self._state.edges.remove(kante)
            if neu.from_id == neu.to_id and neu.from_store == neu.to_store:
                continue
            if any(edge.triple == neu.triple for edge in self._state.edges):
                continue
            self._state.edges.append(neu)
            umgehaengt += 1
        return umgehaengt

    def reject(self, *, edge: Edge, actor: str, reason: str | None, now: datetime) -> None:
        del now
        self._state.rejections[edge.triple] = reason or actor

    def rejected_kinds(self, *, from_id: str, to_id: str) -> frozenset[str]:
        return frozenset(
            triple[4]
            for triple in self._state.rejections
            if triple[0] == self._store and {triple[1], triple[3]} == {from_id, to_id}
        )

    def unreject(
        self, *, from_store: str, from_id: str, to_store: str, to_id: str, kind: str
    ) -> bool:
        return (
            self._state.rejections.pop((from_store, from_id, to_store, to_id, kind), None)
            is not None
        )

    def refresh_resolution(self) -> int:
        anzahl = 0
        for index, edge in enumerate(self._state.edges):
            if edge.from_store != self._store or edge.to_store != self._store:
                continue
            ziel = self._state.concepts.get(edge.to_id)
            soll = ziel is not None and ziel.status is not ConceptStatus.TOMBSTONE
            if edge.resolved != soll:
                self._state.edges[index] = edge.model_copy(update={"resolved": soll})
                anzahl += 1
        return anzahl


class MemoryChangeLogRepository:
    """Änderungsjournal eines Stores im Speicher."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def append(self, entry: ChangeEntry) -> ChangeEntry:
        """Vergibt eine ID wie die ``BIGSERIAL``-Spalte und hängt den Eintrag an."""
        vergeben = entry.model_copy(update={"id": self._state.next_change_id})
        self._state.next_change_id += 1
        self._state.changes.append(vergeben)
        return vergeben

    def entries_for(self, concept_id: str) -> tuple[ChangeEntry, ...]:
        return tuple(
            entry for entry in reversed(self._state.changes) if entry.concept_id == concept_id
        )

    def for_edge(self, edge_id: UUID) -> tuple[ChangeEntry, ...]:
        return tuple(entry for entry in reversed(self._state.changes) if entry.edge_id == edge_id)

    def get(self, entry_id: int) -> ChangeEntry | None:
        return next((entry for entry in self._state.changes if entry.id == entry_id), None)

    def recent(self, *, limit: int) -> tuple[ChangeEntry, ...]:
        return tuple(reversed(self._state.changes))[:limit]

    def has_open_curation_conflict(self, *, concept_id: str, source_content_hash: str) -> bool:
        return any(
            entry.change_type is ChangeType.CURATION_CONFLICT
            and entry.concept_id == concept_id
            and (entry.detail or {}).get(CONFLICT_SOURCE_HASH_KEY) == source_content_hash
            for entry in self._state.changes
        )


class MemoryRunRepository:
    """Die Läufe eines Stores im Speicher."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def create(self, run: Run) -> None:
        self._state.runs[run.id] = run

    def get(self, run_id: UUID) -> Run | None:
        return self._state.runs.get(run_id)

    def update(self, run: Run) -> None:
        vorhanden = self._state.runs.get(run.id)
        if vorhanden is None:
            return
        # Wie das SQL-Pendant: 'kind' und 'params' sind nicht überschreibbar.
        self._state.runs[run.id] = run.model_copy(
            update={"kind": vorhanden.kind, "params": vorhanden.params}
        )

    def recent(self, *, kind: RunKind | None = None, limit: int = 20) -> tuple[Run, ...]:
        passend = [run for run in self._state.runs.values() if kind is None or run.kind is kind]
        # Wie 'ORDER BY started_at DESC NULLS FIRST': Ein noch nicht gestarteter Lauf steht oben.
        passend.sort(key=lambda run: (run.started_at is not None, run.started_at), reverse=True)
        return tuple(passend[:limit])

    def active_for_source(self, source: str) -> Run | None:
        for run in self.recent(kind=RunKind.SYNC, limit=len(self._state.runs) or 1):
            if not run.is_final and run.params.get("source") == source:
                return run
        return None


class MemorySourceCursorRepository:
    """Die Fortschrittsmarken der Quellen eines Stores im Speicher."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def get(self, source_name: str) -> SourceCursorState | None:
        return self._state.cursors.get(source_name)

    def save(
        self, source_name: str, cursor: Cursor, *, full_sync_at: datetime | None = None
    ) -> None:
        vorhanden = self._state.cursors.get(source_name)
        self._state.cursors[source_name] = SourceCursorState(
            source_name=source_name,
            cursor=cursor,
            # Wie das SQL-Pendant: Ein inkrementeller Lauf lässt 'last_full_sync' stehen.
            last_full_sync=(
                full_sync_at
                if full_sync_at is not None
                else (None if vorhanden is None else vorhanden.last_full_sync)
            ),
            updated_at=datetime.now(UTC),
        )

    def delete(self, source_name: str) -> bool:
        return self._state.cursors.pop(source_name, None) is not None


class MemoryEmbeddingRepository:
    """Vektoren eines Stores im Speicher.

    Die Kosinusähnlichkeit wird hier wirklich gerechnet und nicht nachgeahmt. Nur so prüft ein
    Test über §13.2 die Cluster-Bildung und nicht seine eigene Vorbereitung — eine Nachbildung,
    die etwa nach Titelgleichheit sortierte, hätte jede Schwelle bedeutungslos gemacht.
    """

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def outdated(self, *, model_key: str, scope: str | None = None) -> tuple[str, ...]:
        offen = []
        for concept in self._state.concepts.values():
            if concept.status is ConceptStatus.TOMBSTONE:
                continue
            if scope is not None and concept.scope != scope:
                continue
            eintrag = self._state.embeddings.get((concept.id, model_key))
            if eintrag is None or eintrag[1] != concept.content_hash:
                offen.append(concept.id)
        return tuple(sorted(offen))

    def save(
        self, *, concept_id: str, model_key: str, vector: Sequence[float], source_hash: str
    ) -> None:
        self._state.embeddings[(concept_id, model_key)] = (tuple(vector), source_hash)

    def get(self, *, concept_id: str, model_key: str) -> tuple[float, ...] | None:
        eintrag = self._state.embeddings.get((concept_id, model_key))
        return None if eintrag is None else eintrag[0]

    def count(self, *, model_key: str, scope: str | None = None) -> int:
        return len(self._passende(model_key=model_key, scope=scope))

    def neighbours(
        self,
        *,
        concept_id: str,
        model_key: str,
        k: int,
        scope: str | None = None,
        min_similarity: float = 0.0,
    ) -> tuple[Neighbour, ...]:
        eigen = self.get(concept_id=concept_id, model_key=model_key)
        if eigen is None:
            return ()
        return self.search(
            vector=eigen,
            model_key=model_key,
            limit=k,
            scope=scope,
            exclude=(concept_id,),
            min_similarity=min_similarity,
        )

    def search(
        self,
        *,
        vector: Sequence[float],
        model_key: str,
        limit: int,
        scope: str | None = None,
        exclude: Sequence[str] = (),
        min_similarity: float = 0.0,
    ) -> tuple[Neighbour, ...]:
        ausgeschlossen = frozenset(exclude)
        treffer = [
            Neighbour(concept_id=kandidat, similarity=_kosinus(vector, eigen))
            for kandidat, eigen in self._passende(model_key=model_key, scope=scope).items()
            if kandidat not in ausgeschlossen
        ]
        treffer.sort(key=lambda hit: (-hit.similarity, hit.concept_id))
        return tuple(hit for hit in treffer[:limit] if hit.similarity >= min_similarity)

    def _passende(self, *, model_key: str, scope: str | None) -> dict[str, tuple[float, ...]]:
        """Alle Vektoren dieses Modells, deren Konzept lebt und im Scope liegt."""
        gefunden: dict[str, tuple[float, ...]] = {}
        for (concept_id, schluessel), (vektor, _) in self._state.embeddings.items():
            if schluessel != model_key:
                continue
            concept = self._state.concepts.get(concept_id)
            if concept is None or concept.status is ConceptStatus.TOMBSTONE:
                continue
            if scope is not None and concept.scope != scope:
                continue
            gefunden[concept_id] = vektor
        return gefunden


class MemoryClusterRepository:
    """Zentroide und Zuordnungskandidaten eines Stores im Speicher (§13.2, §13.3)."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def save_centroid(
        self, *, cluster_id: str, model_key: str, vector: Sequence[float], member_count: int
    ) -> None:
        self._state.centroids[cluster_id] = Centroid(
            cluster_id=cluster_id,
            model_key=model_key,
            vector=tuple(vector),
            member_count=member_count,
            updated_at=datetime.now(UTC),
        )

    def centroids(self, *, model_key: str) -> tuple[Centroid, ...]:
        return tuple(
            sorted(
                (item for item in self._state.centroids.values() if item.model_key == model_key),
                key=lambda item: item.cluster_id,
            )
        )

    def search_centroids(
        self, *, vector: Sequence[float], model_key: str, limit: int
    ) -> tuple[Neighbour, ...]:
        treffer = [
            Neighbour(concept_id=item.cluster_id, similarity=_kosinus(vector, item.vector))
            for item in self.centroids(model_key=model_key)
        ]
        treffer.sort(key=lambda hit: (-hit.similarity, hit.concept_id))
        return tuple(treffer[:limit])

    def similar_centroids(
        self, *, cluster_id: str, model_key: str, limit: int
    ) -> tuple[Neighbour, ...]:
        eigen = self._state.centroids.get(cluster_id)
        if eigen is None:
            return ()
        treffer = [
            Neighbour(concept_id=item.cluster_id, similarity=_kosinus(eigen.vector, item.vector))
            for item in self.centroids(model_key=model_key)
            if item.cluster_id != cluster_id
        ]
        treffer.sort(key=lambda hit: (-hit.similarity, hit.concept_id))
        return tuple(treffer[:limit])

    def bump(self, *, concept_id: str, cluster_id: str, score: float, run_id: UUID) -> int:
        schluessel = (concept_id, cluster_id)
        vorhanden = self._state.candidates.get(schluessel)
        if vorhanden is None:
            self._state.candidates[schluessel] = _Kandidat(
                score=score, seen_count=1, last_run=run_id, excluded=False
            )
            return 1
        # Wie das SQL-Pendant: derselbe Lauf zählt nicht zweimal.
        if vorhanden.last_run != run_id:
            vorhanden.seen_count += 1
        vorhanden.score = score
        vorhanden.last_run = run_id
        return vorhanden.seen_count

    def candidates(self, *, min_seen: int = 1) -> tuple[AssignmentCandidate, ...]:
        treffer = [
            AssignmentCandidate(
                concept_id=concept_id,
                cluster_id=cluster_id,
                score=eintrag.score,
                seen_count=eintrag.seen_count,
                excluded=eintrag.excluded,
            )
            for (concept_id, cluster_id), eintrag in self._state.candidates.items()
            if eintrag.seen_count >= min_seen
        ]
        treffer.sort(key=lambda item: (-item.seen_count, -item.score, item.concept_id))
        return tuple(treffer)

    def expire(self, *, run_id: UUID) -> int:
        veraltet = [
            schluessel
            for schluessel, eintrag in self._state.candidates.items()
            if eintrag.last_run != run_id and not eintrag.excluded
        ]
        for schluessel in veraltet:
            del self._state.candidates[schluessel]
        return len(veraltet)

    def exclude(self, *, concept_id: str, cluster_id: str) -> None:
        schluessel = (concept_id, cluster_id)
        vorhanden = self._state.candidates.get(schluessel)
        if vorhanden is None:
            self._state.candidates[schluessel] = _Kandidat(
                score=0.0, seen_count=0, last_run=UUID(int=0), excluded=True
            )
            return
        vorhanden.excluded = True

    def include(self, *, concept_id: str, cluster_id: str) -> bool:
        schluessel = (concept_id, cluster_id)
        vorhanden = self._state.candidates.get(schluessel)
        if vorhanden is None or not vorhanden.excluded:
            return False
        del self._state.candidates[schluessel]
        return True

    def exclusions(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            schluessel for schluessel, eintrag in self._state.candidates.items() if eintrag.excluded
        )


class MemoryModelCallRepository:
    """Die Modellaufrufe eines Stores im Speicher (§7.4, §11.6)."""

    def __init__(self, state: StoreState, store: str) -> None:
        self._state = state
        self._store = store

    @property
    def store(self) -> str:
        return self._store

    def record(self, call: ModelCall) -> None:
        self._state.model_calls.append(call)

    def usage(self, *, run_id: UUID | None = None, limit: int = 500) -> tuple[UsageSummary, ...]:
        gruppen: dict[tuple[str, str, str], list[ModelCall]] = {}
        for call in self._state.model_calls:
            if run_id is not None and call.run_id != run_id:
                continue
            gruppen.setdefault((call.task, call.provider, call.model), []).append(call)

        ergebnis = [
            UsageSummary(
                task=task,
                provider=provider,
                model=model,
                calls=len(calls),
                cache_hits=sum(1 for call in calls if call.cache_hit),
                tokens_in=sum(call.tokens_in or 0 for call in calls),
                tokens_out=sum(call.tokens_out or 0 for call in calls),
                cost_estimate_eur=sum(call.cost_estimate or 0.0 for call in calls),
                failures=sum(
                    1
                    for call in calls
                    if call.status not in (defaults.MODEL_CALL_OK, defaults.MODEL_CALL_CACHE_HIT)
                ),
            )
            for (task, provider, model), calls in gruppen.items()
        ]
        ergebnis.sort(key=lambda item: (-item.cost_estimate_eur, -item.calls))
        return tuple(ergebnis[:limit])

    def spent(self, run_id: UUID) -> tuple[int, float]:
        passend = [
            call
            for call in self._state.model_calls
            if call.run_id == run_id and call.status == defaults.MODEL_CALL_OK
        ]
        return len(passend), sum(call.cost_estimate or 0.0 for call in passend)


def _kosinus(links: Sequence[float], rechts: Sequence[float]) -> float:
    """Die Kosinusähnlichkeit zweier Vektoren; 0.0, wenn einer die Länge null hat."""
    produkt = sum(a * b for a, b in zip(links, rechts, strict=False))
    laenge = math.sqrt(sum(a * a for a in links)) * math.sqrt(sum(b * b for b in rechts))
    return 0.0 if laenge == 0.0 else produkt / laenge


class MemorySourceLocks:
    """Sperren je Quelle im Speicher — erfüllt den Port :class:`SourceLocks`.

    Sie prüft dieselbe Zusicherung wie der Advisory-Lock, nur innerhalb eines Prozesses: Ein
    zweiter Aufruf wird abgewiesen und wartet nicht. Was sie *nicht* kann, ist der Fall aus §10.5,
    auf den es im Betrieb ankommt — zwei Container. Dafür gibt es den Integrationstest gegen
    PostgreSQL.
    """

    def __init__(self) -> None:
        self.gehalten: set[tuple[str, str]] = set()

    @contextmanager
    def hold(self, *, store: str, name: str) -> Iterator[None]:
        schluessel = (store, name)
        if schluessel in self.gehalten:
            raise SourceBusy(name)
        self.gehalten.add(schluessel)
        try:
            yield
        finally:
            self.gehalten.discard(schluessel)


class MemoryUnitOfWork:
    """Eine Transaktion auf einem speicherresidenten Store."""

    def __init__(self, states: dict[str, StoreState], store: str) -> None:
        self._states = states
        self._store = store
        self._snapshot: StoreState | None = None

    @property
    def store(self) -> str:
        return self._store

    @property
    def _state(self) -> StoreState:
        return self._states[self._store]

    @property
    def concepts(self) -> MemoryConceptRepository:
        return MemoryConceptRepository(self._state, self._store)

    @property
    def edges(self) -> MemoryEdgeRepository:
        return MemoryEdgeRepository(self._state, self._store)

    @property
    def changes(self) -> MemoryChangeLogRepository:
        return MemoryChangeLogRepository(self._state, self._store)

    @property
    def runs(self) -> MemoryRunRepository:
        return MemoryRunRepository(self._state, self._store)

    @property
    def cursors(self) -> MemorySourceCursorRepository:
        return MemorySourceCursorRepository(self._state, self._store)

    @property
    def embeddings(self) -> MemoryEmbeddingRepository:
        return MemoryEmbeddingRepository(self._state, self._store)

    @property
    def clusters(self) -> MemoryClusterRepository:
        return MemoryClusterRepository(self._state, self._store)

    @property
    def model_calls(self) -> MemoryModelCallRepository:
        return MemoryModelCallRepository(self._state, self._store)

    def commit(self) -> None:
        self._snapshot = deepcopy(self._state)

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._states[self._store] = self._snapshot
            self._snapshot = deepcopy(self._snapshot)

    def __enter__(self) -> Self:
        self._snapshot = deepcopy(self._state)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        self._snapshot = None


class MemoryUnitOfWorkFactory:
    """Fabrik für speicherresidente Arbeitseinheiten — erfüllt den Port ``UnitOfWorkFactory``."""

    def __init__(self, stores: Sequence[str]) -> None:
        self.states: dict[str, StoreState] = {name: StoreState() for name in stores}

    def __call__(self, store: str) -> MemoryUnitOfWork:
        if store not in self.states:
            raise KeyError(f"Unbekannter Store '{store}'.")
        return MemoryUnitOfWork(self.states, store)

    def state(self, store: str) -> StoreState:
        """Der Inhalt eines Stores — für Zusicherungen im Test."""
        return self.states[store]
