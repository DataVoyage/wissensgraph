"""Speicherresidente Umsetzung der Persistenz-Ports (§4.2).

Diese Fakes sind kein Zugeständnis an bequemere Tests, sondern der Beweis, dass die Ports
tragen: Wenn sich :class:`ConceptService` gegen eine Umsetzung ohne jede Datenbank betreiben
lässt, enthält er tatsächlich keine Infrastrukturannahmen (Leitprinzip 13).

Die Transaktionssemantik ist nachgebildet, nicht weggelassen — beim Betreten wird ein
Schnappschuss genommen und bei einer Ausnahme wiederhergestellt. Nur so lässt sich §10.2 Regel 5
("Konzept, Kanten und change_log gemeinsam") ohne PostgreSQL prüfen.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id

#: Ersatzzeitpunkt, wenn ein Kantenentwurf keinen Erzeugungszeitpunkt trägt. In der Datenbank
#: setzt ``DEFAULT now()`` den Wert; hier braucht es einen deterministischen Ersatz.
_EPOCHE = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class StoreState:
    """Der gesamte Inhalt eines Stores — genau das, was eine Transaktion umfasst."""

    concepts: dict[str, Concept] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    changes: list[ChangeEntry] = field(default_factory=list)


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

    def replace_generated(
        self, *, from_id: str, generated_by: str, drafts: Sequence[EdgeDraft]
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        vorhanden = self.list_outgoing(from_id)
        fremd = {
            edge.triple for edge in vorhanden if edge.curated or edge.generated_by != generated_by
        }
        eigene = {
            edge.triple: edge
            for edge in vorhanden
            if not edge.curated and edge.generated_by == generated_by
        }
        gewuenscht = {draft.triple: draft for draft in drafts if draft.triple not in fremd}

        hinzugefuegt: list[Edge] = []
        for triple, draft in gewuenscht.items():
            if triple in eigene:
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

    def refresh_resolution(self) -> int:
        anzahl = 0
        for index, edge in enumerate(self._state.edges):
            passt = (
                edge.from_store == self._store
                and edge.to_store == self._store
                and not edge.resolved
                and edge.to_id in self._state.concepts
            )
            if passt:
                self._state.edges[index] = edge.model_copy(update={"resolved": True})
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
