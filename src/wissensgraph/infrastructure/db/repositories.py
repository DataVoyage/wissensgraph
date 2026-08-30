"""Repositories je Store (§23, §20.1).

Jede Klasse hier arbeitet auf *einer* Verbindung und kennt genau *einen* Store. Sie committet
nie selbst — das tut die Arbeitseinheit, damit §10.2 Regel 5 gilt: "Der Aufruf ist transaktional:
Konzept, Kanten und change_log gemeinsam."

Die Repositories treffen keine fachlichen Entscheidungen. Was geschrieben wird, hat
:mod:`wissensgraph.domain.upsert` bereits entschieden; hier wird es nur noch abgelegt. Diese
Arbeitsteilung ist der Grund, warum sich die Regeln aus §10.2 ohne Datenbank testen lassen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.infrastructure.db.tables import change_log, concepts, edges


class StoreMismatchError(ValueError):
    """Es sollte etwas in einen anderen Store geschrieben werden als den des Repositories.

    Der Fehler ist die Anwendungsebene der Store-Trennung (§20.1). Er kann nur durch einen
    Programmierfehler entstehen — und genau deshalb soll er laut abbrechen statt die Zeile
    stillschweigend im falschen Store abzulegen.
    """

    def __init__(self, *, erwartet: str, erhalten: str, was: str) -> None:
        super().__init__(
            f"{was} gehört zum Store '{erhalten}', dieses Repository bedient aber "
            f"'{erwartet}'. Store-Auflösung läuft ausschließlich über die Registry (§20.1)."
        )


class _StoreBound:
    """Gemeinsame Basis: eine Verbindung, ein Store."""

    def __init__(self, connection: Connection, store: str) -> None:
        self._connection = connection
        self._store = store

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""
        return self._store


class SqlConceptRepository(_StoreBound):
    """Konzepte eines Stores in PostgreSQL."""

    def get(self, concept_id: str) -> Concept | None:
        """Das Konzept zu einer ID, oder ``None``."""
        row = (
            self._connection.execute(select(concepts).where(concepts.c.id == concept_id))
            .mappings()
            .first()
        )
        return None if row is None else Concept.model_validate(dict(row))

    def exists(self, concept_id: str) -> bool:
        """Ob es die ID in diesem Store gibt."""
        found = self._connection.execute(
            select(concepts.c.id).where(concepts.c.id == concept_id)
        ).first()
        return found is not None

    def existing_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        """Welche der angefragten IDs es in diesem Store gibt — in einer Abfrage."""
        if not concept_ids:
            return frozenset()
        rows = self._connection.execute(
            select(concepts.c.id).where(concepts.c.id.in_(tuple(concept_ids)))
        ).scalars()
        return frozenset(rows)

    def save(self, concept: Concept) -> None:
        """Legt ein Konzept an oder überschreibt es (``INSERT … ON CONFLICT DO UPDATE``).

        Raises:
            StoreMismatchError: Wenn das Konzept einem anderen Store zugeordnet ist.
        """
        if concept.store != self._store:
            raise StoreMismatchError(
                erwartet=self._store, erhalten=concept.store, was=f"Konzept '{concept.id}'"
            )

        werte = _konzept_zeile(concept)
        statement = insert(concepts).values(**werte)
        # ``created_at`` bleibt beim Update stehen: Wann ein Konzept zuerst gesehen wurde, ist
        # eine Tatsache und keine Eigenschaft des jeweils letzten Laufs.
        aktualisierbar = {
            name: statement.excluded[name] for name in werte if name not in {"id", "created_at"}
        }
        self._connection.execute(
            statement.on_conflict_do_update(index_elements=[concepts.c.id], set_=aktualisierbar)
        )


class SqlEdgeRepository(_StoreBound):
    """Kanten eines Stores in PostgreSQL."""

    def list_outgoing(self, concept_id: str) -> tuple[Edge, ...]:
        """Alle von einem Konzept dieses Stores ausgehenden Kanten."""
        rows = self._connection.execute(
            select(edges)
            .where(and_(edges.c.from_store == self._store, edges.c.from_id == concept_id))
            .order_by(edges.c.kind, edges.c.to_id)
        ).mappings()
        return tuple(Edge.model_validate(dict(row)) for row in rows)

    def replace_generated(
        self, *, from_id: str, generated_by: str, drafts: Sequence[EdgeDraft]
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        """Gleicht die Kanten eines Erzeugers ab und meldet Zugänge und Abgänge (§10.4)."""
        for draft in drafts:
            if draft.from_store != self._store:
                raise StoreMismatchError(
                    erwartet=self._store, erhalten=draft.from_store, was="Kante"
                )

        vorhanden = self.list_outgoing(from_id)
        # Fremde Kanten sind alle, die dieser Erzeuger nicht anfassen darf: von Hand gesetzte,
        # kuratierte und die eines anderen Laufs. Ein Entwurf, der auf ein solches Tripel trifft,
        # wird verworfen — "Kanten mit curated = true bleiben unangetastet" (§10.4).
        fremd = {
            edge.triple for edge in vorhanden if edge.curated or edge.generated_by != generated_by
        }
        eigene = {
            edge.triple: edge
            for edge in vorhanden
            if not edge.curated and edge.generated_by == generated_by
        }

        gewuenscht = {draft.triple: draft for draft in drafts if draft.triple not in fremd}

        hinzugefuegt = tuple(
            self._insert(draft) for triple, draft in gewuenscht.items() if triple not in eigene
        )
        entfernt = tuple(edge for triple, edge in eigene.items() if triple not in gewuenscht)
        if entfernt:
            self._connection.execute(
                delete(edges).where(edges.c.id.in_([edge.id for edge in entfernt]))
            )

        for triple, draft in gewuenscht.items():
            if triple in eigene:
                self._aktualisieren(eigene[triple], draft)

        return hinzugefuegt, entfernt

    def refresh_resolution(self) -> int:
        """Setzt ``resolved`` auf Kanten, deren Ziel inzwischen in diesem Store liegt (§8.5).

        Geprüft werden nur Kanten mit einem Ziel im *eigenen* Store: Über die Store-Grenze hinweg
        ist die Auflösung eine Abfrage an eine zweite Datenbank und gehört damit in die
        Brückenlogik der Stufe 5.

        Returns:
            Die Anzahl der Kanten, die dadurch auflösbar wurden.
        """
        result = self._connection.execute(
            update(edges)
            .where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.to_store == self._store,
                    edges.c.resolved.is_(False),
                    edges.c.to_id.in_(select(concepts.c.id)),
                )
            )
            .values(resolved=True)
        )
        return result.rowcount

    def _insert(self, draft: EdgeDraft) -> Edge:
        """Legt eine Kante an und gibt sie mit ihrer ID zurück."""
        werte: dict[str, Any] = draft.model_dump()
        werte["id"] = new_edge_id()
        row = (
            self._connection.execute(insert(edges).values(**werte).returning(edges))
            .mappings()
            .one()
        )
        return Edge.model_validate(dict(row))

    def _aktualisieren(self, vorhanden: Edge, draft: EdgeDraft) -> None:
        """Schreibt die veränderlichen Felder einer bestehenden generierten Kante fort.

        Das Tripel bleibt gleich — was sich ändern kann, ist die Bewertung: ob das Ziel
        inzwischen auflösbar ist, wie stark die Kante wiegt, wie das Modell sie begründet hat.
        """
        veraenderlich = {"resolved", "weight", "confidence", "reasoning", "generated_at"}
        neu = {name: getattr(draft, name) for name in veraenderlich}
        if all(getattr(vorhanden, name) == wert for name, wert in neu.items()):
            return
        self._connection.execute(update(edges).where(edges.c.id == vorhanden.id).values(**neu))


class SqlChangeLogRepository(_StoreBound):
    """Änderungsjournal eines Stores in PostgreSQL."""

    def append(self, entry: ChangeEntry) -> None:
        """Hängt einen Eintrag an."""
        werte: dict[str, Any] = {
            "concept_id": entry.concept_id,
            "edge_id": entry.edge_id,
            "change_type": str(entry.change_type),
            "actor": entry.actor,
            "run_id": entry.run_id,
            "detail": entry.detail,
        }
        if entry.changed_at is not None:
            werte["changed_at"] = entry.changed_at
        self._connection.execute(insert(change_log).values(**werte))

    def entries_for(self, concept_id: str) -> tuple[ChangeEntry, ...]:
        """Alle Einträge zu einem Konzept, neueste zuerst."""
        rows = self._connection.execute(
            select(change_log)
            .where(change_log.c.concept_id == concept_id)
            .order_by(change_log.c.changed_at.desc(), change_log.c.id.desc())
        ).mappings()
        return tuple(_journal_eintrag(dict(row)) for row in rows)

    def has_open_curation_conflict(self, *, concept_id: str, source_content_hash: str) -> bool:
        """Ob dieser Konflikt bereits vermerkt ist."""
        found = self._connection.execute(
            select(change_log.c.id).where(
                and_(
                    change_log.c.concept_id == concept_id,
                    change_log.c.change_type == str(ChangeType.CURATION_CONFLICT),
                    change_log.c.detail[CONFLICT_SOURCE_HASH_KEY].astext == source_content_hash,
                )
            )
        ).first()
        return found is not None


def _konzept_zeile(concept: Concept) -> dict[str, Any]:
    """Übersetzt ein Konzept in die Spaltenwerte seiner Zeile."""
    werte: dict[str, Any] = concept.model_dump()
    werte["status"] = str(concept.status)
    werte["tags"] = list(concept.tags)
    werte["audience"] = list(concept.audience)
    return werte


def _journal_eintrag(row: Mapping[str, Any]) -> ChangeEntry:
    """Baut einen Journaleintrag aus einer Zeile; die Spalte ``id`` gehört nicht ins Modell."""
    daten = {name: wert for name, wert in row.items() if name != "id"}
    return ChangeEntry.model_validate(daten)
