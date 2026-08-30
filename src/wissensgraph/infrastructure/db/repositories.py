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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from wissensgraph.config import defaults
from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.domain.runs import Run, RunKind, RunStatus
from wissensgraph.infrastructure.db.tables import (
    change_log,
    concepts,
    edges,
    runs,
    source_cursors,
)
from wissensgraph.ports.repositories import LexicalHit
from wissensgraph.ports.runs import SourceCursorState
from wissensgraph.ports.sources import Cursor

#: Die Spalten der Konzepttabelle, wie sie in eine handgeschriebene Abfrage eingesetzt werden.
#: Sie stammen aus der Tabellendefinition und nicht aus einer zweiten Liste — ``search_tsv`` ist
#: dort bewusst nicht abgebildet (es ist eine generierte Spalte) und fehlt damit auch hier.
_KONZEPT_SPALTEN = ", ".join(f"c.{spalte.name}" for spalte in concepts.c)

#: Die lexikalische Suche aus §12.4. Sie steht als Text und nicht als SQLAlchemy-Ausdruck, weil
#: sie ``search_tsv``, ``ts_rank`` und ``similarity`` braucht — drei PostgreSQL-Eigenheiten, die
#: sich nur unter Verrenkungen abbilden ließen und dabei unlesbar würden.
_LEXIKALISCHE_SUCHE = f"""
WITH anfrage AS (
    SELECT plainto_tsquery('simple', :query) AS tsq
),
volltext AS (
    SELECT c.id,
           row_number() OVER (ORDER BY ts_rank(c.search_tsv, anfrage.tsq) DESC, c.id) AS rang
    FROM concepts c, anfrage
    WHERE c.status <> :tombstone AND c.search_tsv @@ anfrage.tsq
    LIMIT :limit
),
trigramm AS (
    SELECT c.id,
           row_number() OVER (
               ORDER BY similarity(coalesce(c.title, ''), :query) DESC, c.id
           ) AS rang
    FROM concepts c
    WHERE c.status <> :tombstone
      AND similarity(coalesce(c.title, ''), :query) >= :schwelle
    LIMIT :limit
),
vereint AS (
    SELECT id, sum(1.0 / (:k + rang)) AS score
    FROM (SELECT * FROM volltext UNION ALL SELECT * FROM trigramm) beide
    GROUP BY id
)
SELECT {_KONZEPT_SPALTEN}, vereint.score AS score
FROM concepts c
JOIN vereint ON vereint.id = c.id
ORDER BY vereint.score DESC, c.id
LIMIT :limit
"""


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

    def resolvable_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        """Welche der angefragten IDs hier liegen und keine Grabsteine sind (§7.6)."""
        if not concept_ids:
            return frozenset()
        rows = self._connection.execute(
            select(concepts.c.id).where(
                and_(
                    concepts.c.id.in_(tuple(concept_ids)),
                    concepts.c.status != str(ConceptStatus.TOMBSTONE),
                )
            )
        ).scalars()
        return frozenset(rows)

    def get_many(self, concept_ids: Sequence[str]) -> tuple[Concept, ...]:
        """Mehrere Konzepte in einer Abfrage (§12.1, Batch-Load je Hop)."""
        if not concept_ids:
            return ()
        rows = self._connection.execute(
            select(concepts).where(concepts.c.id.in_(tuple(concept_ids)))
        ).mappings()
        return tuple(Concept.model_validate(dict(row)) for row in rows)

    def search_lexical(self, query: str, *, limit: int) -> tuple[LexicalHit, ...]:
        """Volltext und Trigramm, über Reciprocal Rank Fusion zusammengeführt (§12.4).

        Zwei Verfahren mit zwei Stärken: ``search_tsv`` findet Wörter überall im Text, die
        Trigrammähnlichkeit auf dem Titel findet auch das Falschgeschriebene. Ihre Werte sind
        nicht vergleichbar — ein ``ts_rank`` von 0,08 und eine Ähnlichkeit von 0,42 sagen nichts
        übereinander. Zusammengeführt werden deshalb die *Plätze*: Jede Liste steuert
        ``1 / (k + Rang)`` bei. Das ist genau der Grund, warum §12.4 Reciprocal Rank Fusion nennt
        und keine gewichtete Summe.

        Grabsteine bleiben außen vor: Sie sind Erinnerung, kein Suchergebnis (§7.6).
        """
        if not query.strip():
            return ()
        rows = self._connection.execute(
            text(_LEXIKALISCHE_SUCHE),
            {
                "query": query,
                "limit": limit,
                "schwelle": defaults.SEARCH_TRIGRAM_THRESHOLD,
                "k": defaults.SEARCH_RRF_K,
                "tombstone": str(ConceptStatus.TOMBSTONE),
            },
        ).mappings()
        return tuple(
            LexicalHit(
                concept=Concept.model_validate(
                    {name: wert for name, wert in row.items() if name != "score"}
                ),
                score=float(row["score"]),
            )
            for row in rows
        )

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

    def list_incoming(self, concept_id: str) -> tuple[Edge, ...]:
        """Alle Kanten dieses Stores, die auf ein Konzept dieses Stores zeigen."""
        rows = self._connection.execute(
            select(edges)
            .where(and_(edges.c.to_store == self._store, edges.c.to_id == concept_id))
            .order_by(edges.c.kind, edges.c.from_id)
        ).mappings()
        return tuple(Edge.model_validate(dict(row)) for row in rows)

    def neighbourhood(self, concept_ids: Sequence[str]) -> tuple[Edge, ...]:
        """Alle Kanten dieses Stores, die eine der IDs berühren — in einer Abfrage (§12.1)."""
        if not concept_ids:
            return ()
        gesucht = tuple(concept_ids)
        rows = self._connection.execute(
            select(edges)
            .where(
                or_(
                    and_(edges.c.from_store == self._store, edges.c.from_id.in_(gesucht)),
                    and_(edges.c.to_store == self._store, edges.c.to_id.in_(gesucht)),
                )
            )
            .order_by(edges.c.from_id, edges.c.kind, edges.c.to_id)
        ).mappings()
        return tuple(Edge.model_validate(dict(row)) for row in rows)

    def bridges_into(self, *, to_store: str, to_ids: Sequence[str]) -> tuple[Edge, ...]:
        """Kanten aus diesem Store auf Konzepte eines anderen Stores (§12.1, Rückrichtung)."""
        if not to_ids:
            return ()
        rows = self._connection.execute(
            select(edges)
            .where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.to_store == to_store,
                    edges.c.to_id.in_(tuple(to_ids)),
                )
            )
            .order_by(edges.c.from_id, edges.c.kind, edges.c.to_id)
        ).mappings()
        return tuple(Edge.model_validate(dict(row)) for row in rows)

    def replace_generated(
        self, *, from_id: str, generated_by: Sequence[str], drafts: Sequence[EdgeDraft]
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        """Gleicht die Kanten mehrerer Erzeuger ab und meldet Zugänge und Abgänge (§10.4)."""
        for draft in drafts:
            if draft.from_store != self._store:
                raise StoreMismatchError(
                    erwartet=self._store, erhalten=draft.from_store, was="Kante"
                )

        besitz = frozenset(generated_by)
        vorhanden = self.list_outgoing(from_id)
        # Fremde Kanten sind alle, die dieser Aufruf nicht anfassen darf: von Hand gesetzte,
        # kuratierte und die eines anderen Erzeugers. Ein Entwurf, der auf ein solches Tripel
        # trifft, wird verworfen — "Kanten mit curated = true bleiben unangetastet" (§10.4).
        fremd = {
            edge.triple for edge in vorhanden if edge.curated or edge.generated_by not in besitz
        }
        eigene = {
            edge.triple: edge
            for edge in vorhanden
            if not edge.curated and edge.generated_by in besitz
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
        """Gleicht ``resolved`` für alle Kanten innerhalb dieses Stores ab (§8.5, §7.6).

        Innerhalb eines Stores ist das eine einzige Anweisung: Ziel und Kante liegen in derselben
        Datenbank, ein Unterabfrage-Join genügt. Über die Grenze hinweg geht das nicht — dafür
        gibt es :meth:`foreign_targets` und :meth:`set_foreign_resolution`.

        Geschrieben wird nur, wo sich etwas ändert. Das hält nicht nur die Zahl ehrlich, die
        zurückkommt; es vermeidet auch, bei jedem Lauf jede Kante der Datenbank anzufassen.

        Returns:
            Die Anzahl der Kanten, deren ``resolved`` sich geändert hat.
        """
        auffindbar = select(concepts.c.id).where(concepts.c.status != str(ConceptStatus.TOMBSTONE))
        soll = edges.c.to_id.in_(auffindbar)
        result = self._connection.execute(
            update(edges)
            .where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.to_store == self._store,
                    edges.c.resolved != soll,
                )
            )
            .values(resolved=soll)
        )
        return result.rowcount

    def foreign_targets(self) -> dict[str, frozenset[str]]:
        """Die Ziele aller Brückenkanten dieses Stores, gruppiert nach Zielstore (§12.1)."""
        rows = self._connection.execute(
            select(edges.c.to_store, edges.c.to_id)
            .where(and_(edges.c.from_store == self._store, edges.c.to_store != self._store))
            .distinct()
        )
        gruppiert: dict[str, set[str]] = {}
        for to_store, to_id in rows:
            gruppiert.setdefault(to_store, set()).add(to_id)
        return {store: frozenset(ids) for store, ids in gruppiert.items()}

    def unresolved_targets(self) -> frozenset[str]:
        """Die Ziel-IDs aller noch nicht aufgelösten Kanten dieses Stores (§8.5)."""
        rows = self._connection.execute(
            select(edges.c.to_id)
            .where(and_(edges.c.from_store == self._store, edges.c.resolved.is_(False)))
            .distinct()
        ).scalars()
        return frozenset(rows)

    def attach_to_store(self, *, to_store: str, to_ids: frozenset[str]) -> int:
        """Hängt unaufgelöste Kanten an den fremden Store, in dem ihr Ziel aufgetaucht ist."""
        if not to_ids:
            return 0
        andere = edges.alias("bestehend")
        result = self._connection.execute(
            update(edges)
            .where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.resolved.is_(False),
                    edges.c.to_store != to_store,
                    edges.c.to_id.in_(tuple(to_ids)),
                    ~select(andere.c.id)
                    .where(
                        and_(
                            andere.c.from_store == edges.c.from_store,
                            andere.c.from_id == edges.c.from_id,
                            andere.c.to_store == to_store,
                            andere.c.to_id == edges.c.to_id,
                            andere.c.kind == edges.c.kind,
                        )
                    )
                    .exists(),
                )
            )
            .values(to_store=to_store, resolved=True)
        )
        return result.rowcount

    def set_foreign_resolution(self, *, to_store: str, resolvable: frozenset[str]) -> int:
        """Schreibt das Ergebnis einer Auflösung über die Store-Grenze zurück (§12.1).

        Die auffindbaren IDs kommen von außen, weil sie aus einer anderen Datenbank stammen. Was
        hier passiert, ist nur noch der Abgleich — und auch der schreibt nur, wo sich etwas
        ändert.
        """
        # Eine leere Menge ist kein Sonderfall: SQLAlchemy übersetzt ein ``IN ()`` in einen
        # Ausdruck, der für jede Zeile falsch ist — genau das ist hier die richtige Aussage.
        soll = edges.c.to_id.in_(tuple(resolvable))
        result = self._connection.execute(
            update(edges)
            .where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.to_store == to_store,
                    edges.c.resolved != soll,
                )
            )
            .values(resolved=soll)
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
        ``generated_by`` gehört dazu, weil derselbe Verweis seine Herkunft wechseln kann: Ein
        Ziel, das die Quelle meldete, kann später im Fließtext als ``[[id]]`` auftauchen (§8.5).
        """
        veraenderlich = {
            "resolved",
            "weight",
            "confidence",
            "reasoning",
            "generated_at",
            "generated_by",
        }
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


class SqlRunRepository(_StoreBound):
    """Die Läufe eines Stores in PostgreSQL (§7.4)."""

    def create(self, run: Run) -> None:
        """Legt einen Lauf an."""
        self._connection.execute(insert(runs).values(**_lauf_zeile(run)))

    def get(self, run_id: UUID) -> Run | None:
        """Der Lauf zu einer ID, oder ``None``."""
        row = self._connection.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        return None if row is None else Run.model_validate(dict(row))

    def update(self, run: Run) -> None:
        """Schreibt die veränderlichen Felder eines Laufs fort.

        ``kind`` und ``params`` fehlen absichtlich: Womit ein Lauf gestartet wurde, ist eine
        Tatsache. Wäre es überschreibbar, könnte ein abgeschlossener Lauf nachträglich behaupten,
        er sei mit anderen Parametern gelaufen — und das Journal, das über ``run_id`` an ihm
        hängt, zeigte auf eine Geschichte, die es so nie gab.
        """
        self._connection.execute(
            update(runs)
            .where(runs.c.id == run.id)
            .values(
                status=str(run.status),
                started_at=run.started_at,
                finished_at=run.finished_at,
                progress=run.progress,
                stats=run.stats,
                error=run.error,
            )
        )

    def recent(self, *, kind: RunKind | None = None, limit: int = 20) -> tuple[Run, ...]:
        """Die zuletzt begonnenen Läufe, neueste zuerst.

        Sortiert wird über ``started_at`` mit ``NULLS FIRST``: Ein noch nicht gestarteter Lauf
        (``queued``) ist das Neueste, was es gibt — er wartet gerade.
        """
        statement = select(runs).order_by(runs.c.started_at.desc().nullsfirst()).limit(limit)
        if kind is not None:
            statement = statement.where(runs.c.kind == str(kind))
        rows = self._connection.execute(statement).mappings()
        return tuple(Run.model_validate(dict(row)) for row in rows)

    def active_for_source(self, source: str) -> Run | None:
        """Ein noch nicht abgeschlossener Sync-Lauf dieser Quelle (§10.5)."""
        offen = tuple(str(status) for status in RunStatus if not status.is_final)
        row = (
            self._connection.execute(
                select(runs)
                .where(
                    and_(
                        runs.c.kind == str(RunKind.SYNC),
                        runs.c.status.in_(offen),
                        runs.c.params[defaults.RUN_PARAM_SOURCE].astext == source,
                    )
                )
                .order_by(runs.c.started_at.desc().nullsfirst())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return None if row is None else Run.model_validate(dict(row))


class SqlSourceCursorRepository(_StoreBound):
    """Die Fortschrittsmarken der Quellen eines Stores in PostgreSQL (§7.4)."""

    def get(self, source_name: str) -> SourceCursorState | None:
        """Der gespeicherte Stand einer Quelle, oder ``None``."""
        row = (
            self._connection.execute(
                select(source_cursors).where(source_cursors.c.source_name == source_name)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        daten = dict(row)
        daten["cursor"] = Cursor(value=daten["cursor"] or {})
        return SourceCursorState.model_validate(daten)

    def save(
        self, source_name: str, cursor: Cursor, *, full_sync_at: datetime | None = None
    ) -> None:
        """Schreibt den Stand einer Quelle fort (``INSERT … ON CONFLICT DO UPDATE``)."""
        jetzt = datetime.now(UTC)
        werte: dict[str, Any] = {
            "source_name": source_name,
            "cursor": cursor.value,
            "updated_at": jetzt,
            "last_full_sync": full_sync_at,
        }
        statement = insert(source_cursors).values(**werte)
        # ``last_full_sync`` wird nur bei einem Vollabgleich überschrieben: Ein inkrementeller
        # Lauf soll nicht vergessen machen, wann zuletzt vollständig abgeglichen wurde.
        aktualisierbar: dict[str, Any] = {
            "cursor": statement.excluded.cursor,
            "updated_at": statement.excluded.updated_at,
        }
        if full_sync_at is not None:
            aktualisierbar["last_full_sync"] = statement.excluded.last_full_sync
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[source_cursors.c.source_name], set_=aktualisierbar
            )
        )

    def delete(self, source_name: str) -> bool:
        """Vergisst den Stand einer Quelle; der nächste Lauf ist ein Vollabgleich."""
        result = self._connection.execute(
            delete(source_cursors).where(source_cursors.c.source_name == source_name)
        )
        return result.rowcount > 0


def _lauf_zeile(run: Run) -> dict[str, Any]:
    """Übersetzt einen Lauf in die Spaltenwerte seiner Zeile."""
    werte: dict[str, Any] = run.model_dump()
    werte["kind"] = str(run.kind)
    werte["status"] = str(run.status)
    return werte


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
