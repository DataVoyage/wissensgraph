"""Speicherresidente Umsetzung der Persistenz-Ports (§4.2).

Diese Fakes sind kein Zugeständnis an bequemere Tests, sondern der Beweis, dass die Ports
tragen: Wenn sich :class:`ConceptService` gegen eine Umsetzung ohne jede Datenbank betreiben
lässt, enthält er tatsächlich keine Infrastrukturannahmen (Leitprinzip 13).

Die Transaktionssemantik ist nachgebildet, nicht weggelassen — beim Betreten wird ein
Schnappschuss genommen und bei einer Ausnahme wiederhergestellt. Nur so lässt sich §10.2 Regel 5
("Konzept, Kanten und change_log gemeinsam") ohne PostgreSQL prüfen.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.ports.repositories import LexicalHit
from wissensgraph.ports.runs import SourceBusy, SourceCursorState
from wissensgraph.ports.sources import Cursor

#: Ersatzzeitpunkt, wenn ein Kantenentwurf keinen Erzeugungszeitpunkt trägt. In der Datenbank
#: setzt ``DEFAULT now()`` den Wert; hier braucht es einen deterministischen Ersatz.
_EPOCHE = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class StoreState:
    """Der gesamte Inhalt eines Stores — genau das, was eine Transaktion umfasst."""

    concepts: dict[str, Concept] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    changes: list[ChangeEntry] = field(default_factory=list)
    runs: dict[UUID, Run] = field(default_factory=dict)
    cursors: dict[str, SourceCursorState] = field(default_factory=dict)


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

    def append(self, entry: ChangeEntry) -> None:
        self._state.changes.append(entry)

    def entries_for(self, concept_id: str) -> tuple[ChangeEntry, ...]:
        return tuple(
            entry for entry in reversed(self._state.changes) if entry.concept_id == concept_id
        )

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
