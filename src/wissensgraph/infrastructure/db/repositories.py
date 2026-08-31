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

from sqlalchemy import Connection, and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from wissensgraph.config import defaults
from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.domain.runs import Run, RunKind, RunStatus
from wissensgraph.infrastructure.db.tables import (
    change_log,
    cluster_assignment_candidates,
    cluster_centroids,
    concept_embeddings,
    concepts,
    edge_rejections,
    edges,
    loose_concepts,
    model_calls,
    runs,
    source_cursors,
)
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

    def in_scope(self, scope: str, *, concept_type: str | None = None) -> tuple[Concept, ...]:
        """Alle lebenden Konzepte eines Scopes, wahlweise auf einen Typ eingeschränkt."""
        statement = select(concepts).where(
            and_(
                concepts.c.scope == scope,
                concepts.c.status != str(ConceptStatus.TOMBSTONE),
            )
        )
        if concept_type is not None:
            statement = statement.where(concepts.c.type == concept_type)
        rows = self._connection.execute(statement.order_by(concepts.c.id)).mappings()
        return tuple(Concept.model_validate(dict(row)) for row in rows)

    def loose(self, *, threshold: int, scope: str | None = None) -> tuple[LooseConcept, ...]:
        """Die losen Knoten aus ``v_loose_concepts`` (§15.1)."""
        statement = select(loose_concepts).where(loose_concepts.c.semantic_degree < threshold)
        if scope is not None:
            statement = statement.where(loose_concepts.c.scope == scope)
        rows = self._connection.execute(
            statement.order_by(loose_concepts.c.semantic_degree, loose_concepts.c.id)
        ).mappings()
        return tuple(
            LooseConcept(
                id=row["id"],
                scope=row["scope"],
                type=row["type"],
                title=row["title"],
                semantic_degree=int(row["semantic_degree"]),
            )
            for row in rows
        )

    def page(self, filter: ConceptFilter, *, limit: int, cursor: str | None = None) -> Page:
        """Ein gefilterter Ausschnitt des Bestands, cursor-basiert über die ID (§16.1, §16.2)."""
        statement = select(concepts).where(self._filterbedingung(filter))
        if cursor is not None:
            statement = statement.where(concepts.c.id > cursor)
        # Eine Zeile mehr als angefragt: Sie beantwortet, ob es weitergeht, ohne eine zweite
        # Zählabfrage über denselben Filter.
        rows = self._connection.execute(
            statement.order_by(concepts.c.id).limit(limit + 1)
        ).mappings()
        gefunden = [Concept.model_validate(dict(row)) for row in rows]
        weiter = len(gefunden) > limit
        seite = tuple(gefunden[:limit])
        return Page(items=seite, next_cursor=seite[-1].id if weiter and seite else None)

    def _filterbedingung(self, filter: ConceptFilter) -> Any:
        """Übersetzt die Facetten aus §16.2 in eine WHERE-Bedingung."""
        bedingungen = []
        if not filter.include_tombstones:
            bedingungen.append(concepts.c.status != str(ConceptStatus.TOMBSTONE))
        if filter.scope is not None:
            bedingungen.append(concepts.c.scope == filter.scope)
        if filter.concept_type is not None:
            bedingungen.append(concepts.c.type == filter.concept_type)
        if filter.status is not None:
            bedingungen.append(concepts.c.status == filter.status)
        if filter.source_name is not None:
            bedingungen.append(concepts.c.source_name == filter.source_name)
        if filter.curated is not None:
            bedingungen.append(concepts.c.curated.is_(filter.curated))
        if filter.unverified is not None:
            pruefung = concepts.c.verified_at.is_(None)
            bedingungen.append(
                pruefung if filter.unverified else concepts.c.verified_at.is_not(None)
            )
        if filter.query:
            # ``ILIKE`` und nicht die Volltextsuche: Das hier ist die Facettenfilterung des
            # Dokumentenbrowsers (§17.2), also ein Einschränken einer Liste. Die Suche nach
            # Bedeutung ist ein anderer Endpunkt (§16.2, ``/graph/search``) mit einem anderen
            # Versprechen — beides in einen Parameter zu legen, verwischte den Unterschied.
            muster = f"%{filter.query}%"
            bedingungen.append(
                or_(
                    concepts.c.title.ilike(muster),
                    concepts.c.description.ilike(muster),
                )
            )
        if filter.cluster_id is not None:
            bedingungen.append(
                select(edges.c.id)
                .where(
                    and_(
                        edges.c.from_store == self._store,
                        edges.c.from_id == filter.cluster_id,
                        edges.c.kind == defaults.EDGE_KIND_MEMBER,
                        edges.c.to_id == concepts.c.id,
                    )
                )
                .exists()
            )
        if filter.orphan is not None:
            lose = (
                select(loose_concepts.c.id)
                .where(
                    and_(
                        loose_concepts.c.id == concepts.c.id,
                        loose_concepts.c.semantic_degree < filter.loose_threshold,
                    )
                )
                .exists()
            )
            bedingungen.append(lose if filter.orphan else ~lose)
        return and_(*bedingungen) if bedingungen else text("TRUE")

    def counts(self) -> tuple[ConceptCount, ...]:
        """Die Bestandszahlen nach Scope, Typ und Status (§16.2)."""
        rows = self._connection.execute(
            select(
                concepts.c.scope,
                concepts.c.type,
                concepts.c.status,
                func.count().label("anzahl"),
            )
            .group_by(concepts.c.scope, concepts.c.type, concepts.c.status)
            .order_by(concepts.c.scope, concepts.c.type, concepts.c.status)
        ).mappings()
        return tuple(
            ConceptCount(
                scope=row["scope"],
                type=row["type"],
                status=row["status"],
                count=int(row["anzahl"]),
            )
            for row in rows
        )

    def delete(self, concept_id: str) -> bool:
        """Entfernt ein Konzept vollständig (§17.3, Undo einer Anlage)."""
        result = self._connection.execute(delete(concepts).where(concepts.c.id == concept_id))
        return result.rowcount > 0

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

    def add(self, draft: EdgeDraft) -> Edge | None:
        """Legt eine einzelne Kante an, sofern es ihr Tripel noch nicht gibt (§14.2 Schritt 5)."""
        if draft.from_store != self._store:
            raise StoreMismatchError(erwartet=self._store, erhalten=draft.from_store, was="Kante")

        werte: dict[str, Any] = draft.model_dump()
        werte["id"] = new_edge_id()
        # ``ON CONFLICT DO NOTHING`` statt einer vorherigen Abfrage: Zwischen Prüfen und Schreiben
        # läge sonst ein Zeitfenster, und ``ux_edges_triple`` würde den zweiten Lauf mit einem
        # Datenbankfehler abbrechen statt mit einem "gibt es schon".
        row = (
            self._connection.execute(
                insert(edges)
                .values(**werte)
                .on_conflict_do_nothing(
                    index_elements=[
                        edges.c.from_store,
                        edges.c.from_id,
                        edges.c.to_store,
                        edges.c.to_id,
                        edges.c.kind,
                    ]
                )
                .returning(edges)
            )
            .mappings()
            .first()
        )
        return None if row is None else Edge.model_validate(dict(row))

    def kinds_between(self, *, from_id: str, to_id: str) -> frozenset[str]:
        """Die Kantenarten zwischen zwei Konzepten dieses Stores — in beiden Richtungen."""
        rows = self._connection.execute(
            select(edges.c.kind).where(
                and_(
                    edges.c.from_store == self._store,
                    edges.c.to_store == self._store,
                    or_(
                        and_(edges.c.from_id == from_id, edges.c.to_id == to_id),
                        and_(edges.c.from_id == to_id, edges.c.to_id == from_id),
                    ),
                )
            )
        ).scalars()
        return frozenset(rows)

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

    # -- Kuration (§16.2, §17.2) -------------------------------------------------

    def count(self) -> int:
        """Wie viele Kanten in diesem Store beginnen (§16.2, ``/stats``)."""
        anzahl = self._connection.execute(
            select(func.count()).select_from(edges).where(edges.c.from_store == self._store)
        ).scalar_one()
        return int(anzahl)

    def get(self, edge_id: UUID) -> Edge | None:
        """Die Kante zu einer ID, oder ``None``."""
        row = (
            self._connection.execute(select(edges).where(edges.c.id == edge_id)).mappings().first()
        )
        return None if row is None else Edge.model_validate(dict(row))

    def remove(self, edge_id: UUID) -> Edge | None:
        """Entfernt eine Kante und gibt sie zurück, damit der Aufrufer sie journalisieren kann."""
        row = (
            self._connection.execute(delete(edges).where(edges.c.id == edge_id).returning(edges))
            .mappings()
            .first()
        )
        return None if row is None else Edge.model_validate(dict(row))

    def verify(self, *, edge_id: UUID, actor: str, now: datetime) -> Edge | None:
        """Bestätigt eine Kante: ``verified_by``, ``verified_at`` und ``curated = true`` (§16.2).

        ``curated`` gehört dazu, weil die Bestätigung sonst wirkungslos wäre: §10.4 erlaubt Läufen,
        generierte Kanten zu ersetzen, und schützt genau die kuratierten. Eine bestätigte Kante,
        die ein Folgelauf wieder wegräumen darf, wäre keine Bestätigung.
        """
        row = (
            self._connection.execute(
                update(edges)
                .where(edges.c.id == edge_id)
                .values(verified_by=actor, verified_at=now, curated=True)
                .returning(edges)
            )
            .mappings()
            .first()
        )
        return None if row is None else Edge.model_validate(dict(row))

    def unverified(self, *, limit: int, kinds: Sequence[str] = ()) -> tuple[Edge, ...]:
        """Generierte, noch nicht bestätigte Kanten — die Warteschlange aus §17.2.

        Nach Confidence absteigend, wie §17.2 es verlangt. Kanten ohne ``generated_by`` bleiben
        außen vor: Was ein Mensch gesetzt hat, wartet auf keine Bestätigung.
        """
        statement = select(edges).where(
            and_(
                edges.c.from_store == self._store,
                edges.c.generated_by.is_not(None),
                edges.c.curated.is_(False),
                edges.c.verified_at.is_(None),
            )
        )
        if kinds:
            statement = statement.where(edges.c.kind.in_(tuple(kinds)))
        rows = self._connection.execute(
            statement.order_by(
                edges.c.confidence.desc().nullslast(), edges.c.created_at.desc(), edges.c.id
            ).limit(limit)
        ).mappings()
        return tuple(Edge.model_validate(dict(row)) for row in rows)

    def retarget(self, *, from_id: str, to_id: str, kind: str | None = None) -> int:
        """Hängt Kanten von einem Konzept dieses Stores auf ein anderes um (§16.2, Verschmelzen).

        Beide Richtungen: Ein verschmolzenes Cluster ist Ausgangspunkt seiner ``member``-Kanten und
        Ziel der ``related``-Kanten anderer Cluster. Tripel, die es am Ziel schon gibt, werden
        gelöscht statt umgehängt — ``ux_edges_triple`` (§7.4) ließe die Doppelung ohnehin nicht zu,
        und ein Abbruch mitten in einer Verschmelzung wäre der schlechtere Ausgang.
        """
        umgehaengt = 0
        for spalte_id, spalte_store in (
            (edges.c.from_id, edges.c.from_store),
            (edges.c.to_id, edges.c.to_store),
        ):
            bedingung = and_(spalte_store == self._store, spalte_id == from_id)
            if kind is not None:
                bedingung = and_(bedingung, edges.c.kind == kind)
            for row in self._connection.execute(select(edges).where(bedingung)).mappings().all():
                kante = Edge.model_validate(dict(row))
                neu = (
                    kante.model_copy(update={"from_id": to_id})
                    if kante.from_id == from_id and kante.from_store == self._store
                    else kante.model_copy(update={"to_id": to_id})
                )
                self._connection.execute(delete(edges).where(edges.c.id == kante.id))
                if neu.from_id == neu.to_id and neu.from_store == neu.to_store:
                    continue
                bereits = self._connection.execute(
                    select(edges.c.id).where(
                        and_(
                            edges.c.from_store == neu.from_store,
                            edges.c.from_id == neu.from_id,
                            edges.c.to_store == neu.to_store,
                            edges.c.to_id == neu.to_id,
                            edges.c.kind == neu.kind,
                        )
                    )
                ).first()
                if bereits is not None:
                    continue
                werte = neu.model_dump()
                self._connection.execute(insert(edges).values(**werte))
                umgehaengt += 1
        return umgehaengt

    def reject(self, *, edge: Edge, actor: str, reason: str | None, now: datetime) -> None:
        """Vermerkt ein Kantentripel als verworfen (§16.2, Migration 0003)."""
        self._connection.execute(
            insert(edge_rejections)
            .values(
                from_store=edge.from_store,
                from_id=edge.from_id,
                to_store=edge.to_store,
                to_id=edge.to_id,
                kind=edge.kind,
                rejected_by=actor,
                reason=reason,
                rejected_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    edge_rejections.c.from_store,
                    edge_rejections.c.from_id,
                    edge_rejections.c.to_store,
                    edge_rejections.c.to_id,
                    edge_rejections.c.kind,
                ],
                set_={"rejected_by": actor, "reason": reason, "rejected_at": now},
            )
        )

    def rejected_kinds(self, *, from_id: str, to_id: str) -> frozenset[str]:
        """Die verworfenen Kantenarten zwischen zwei Konzepten — in beiden Richtungen.

        Beide Richtungen, obwohl der Vermerk gerichtet ist: Die Kantenerkennung fragt, *bevor* sie
        ein Paar an ein Modell gibt (§14.5), und zu diesem Zeitpunkt kennt sie die Richtung noch
        nicht. Ein Mensch, der eine Beziehung verworfen hat, soll dazu nicht zweimal gefragt
        werden — einmal je Richtung.
        """
        rows = self._connection.execute(
            select(edge_rejections.c.kind).where(
                or_(
                    and_(
                        edge_rejections.c.from_store == self._store,
                        edge_rejections.c.from_id == from_id,
                        edge_rejections.c.to_id == to_id,
                    ),
                    and_(
                        edge_rejections.c.from_store == self._store,
                        edge_rejections.c.from_id == to_id,
                        edge_rejections.c.to_id == from_id,
                    ),
                )
            )
        ).scalars()
        return frozenset(rows)

    def unreject(
        self, *, from_store: str, from_id: str, to_store: str, to_id: str, kind: str
    ) -> bool:
        """Nimmt einen Negativvermerk zurück — der Undo eines ``reject`` (§17.3).

        Returns:
            Ob es einen Vermerk zum Zurücknehmen gab.
        """
        result = self._connection.execute(
            delete(edge_rejections).where(
                and_(
                    edge_rejections.c.from_store == from_store,
                    edge_rejections.c.from_id == from_id,
                    edge_rejections.c.to_store == to_store,
                    edge_rejections.c.to_id == to_id,
                    edge_rejections.c.kind == kind,
                )
            )
        )
        return result.rowcount > 0

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


class SqlEmbeddingRepository(_StoreBound):
    """Die Vektoren eines Stores in PostgreSQL (§7.4, §13.1).

    Die Kosinus*distanz* von pgvector wird an dieser Grenze in eine Ähnlichkeit umgerechnet
    (``1 - distanz``). Weiter innen gibt es dann nur noch eine Richtung: größer ist ähnlicher.
    Jede Schwelle aus §13 und §15 ist so formuliert, und eine zweite Konvention wäre eine
    Fehlerquelle, die niemand beim Lesen bemerkt.
    """

    def outdated(self, *, model_key: str, scope: str | None = None) -> tuple[str, ...]:
        """Konzepte ohne Vektor oder mit veraltetem ``source_hash`` (§13.1)."""
        vorhanden = concept_embeddings.alias("e")
        statement = (
            select(concepts.c.id)
            .select_from(
                concepts.outerjoin(
                    vorhanden,
                    and_(
                        vorhanden.c.concept_id == concepts.c.id,
                        vorhanden.c.model_key == model_key,
                    ),
                )
            )
            .where(
                and_(
                    concepts.c.status != str(ConceptStatus.TOMBSTONE),
                    or_(
                        vorhanden.c.concept_id.is_(None),
                        vorhanden.c.source_hash != concepts.c.content_hash,
                    ),
                )
            )
            .order_by(concepts.c.id)
        )
        if scope is not None:
            statement = statement.where(concepts.c.scope == scope)
        return tuple(self._connection.execute(statement).scalars())

    def save(
        self, *, concept_id: str, model_key: str, vector: Sequence[float], source_hash: str
    ) -> None:
        """Legt einen Vektor ab oder ersetzt ihn."""
        werte: dict[str, Any] = {
            "concept_id": concept_id,
            "model_key": model_key,
            "dim": len(vector),
            "embedding": list(vector),
            "source_hash": source_hash,
            "created_at": datetime.now(UTC),
        }
        statement = insert(concept_embeddings).values(**werte)
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[concept_embeddings.c.concept_id, concept_embeddings.c.model_key],
                set_={
                    "dim": statement.excluded.dim,
                    "embedding": statement.excluded.embedding,
                    "source_hash": statement.excluded.source_hash,
                    "created_at": statement.excluded.created_at,
                },
            )
        )

    def get(self, *, concept_id: str, model_key: str) -> tuple[float, ...] | None:
        """Der abgelegte Vektor eines Konzepts, oder ``None``."""
        row = self._connection.execute(
            select(concept_embeddings.c.embedding).where(
                and_(
                    concept_embeddings.c.concept_id == concept_id,
                    concept_embeddings.c.model_key == model_key,
                )
            )
        ).first()
        return None if row is None else tuple(float(zahl) for zahl in row[0])

    def count(self, *, model_key: str, scope: str | None = None) -> int:
        """Wie viele Konzepte unter diesem Modellschlüssel eingebettet sind."""
        statement = (
            select(func.count())
            .select_from(
                concept_embeddings.join(concepts, concepts.c.id == concept_embeddings.c.concept_id)
            )
            .where(concept_embeddings.c.model_key == model_key)
        )
        if scope is not None:
            statement = statement.where(concepts.c.scope == scope)
        return int(self._connection.execute(statement).scalar_one())

    def neighbours(
        self,
        *,
        concept_id: str,
        model_key: str,
        k: int,
        scope: str | None = None,
        min_similarity: float = 0.0,
    ) -> tuple[Neighbour, ...]:
        """Die k nächsten Nachbarn eines Konzepts (§13.2 Schritt 1)."""
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
        """Die ähnlichsten Konzepte zu einem Vektor — über den HNSW-Index (§12.4, §15.2b)."""
        if limit < 1:
            return ()
        distanz = concept_embeddings.c.embedding.cosine_distance(list(vector))
        statement = (
            select(concept_embeddings.c.concept_id, distanz.label("distanz"))
            .select_from(
                concept_embeddings.join(concepts, concepts.c.id == concept_embeddings.c.concept_id)
            )
            .where(
                and_(
                    concept_embeddings.c.model_key == model_key,
                    concepts.c.status != str(ConceptStatus.TOMBSTONE),
                )
            )
            .order_by(distanz)
            .limit(limit)
        )
        if scope is not None:
            statement = statement.where(concepts.c.scope == scope)
        if exclude:
            statement = statement.where(concepts.c.id.notin_(tuple(exclude)))

        treffer = [
            Neighbour(concept_id=row[0], similarity=1.0 - float(row[1]))
            for row in self._connection.execute(statement)
        ]
        return tuple(hit for hit in treffer if hit.similarity >= min_similarity)


class SqlClusterRepository(_StoreBound):
    """Zentroide und Zuordnungskandidaten eines Stores in PostgreSQL (§13.2, §13.3)."""

    def save_centroid(
        self, *, cluster_id: str, model_key: str, vector: Sequence[float], member_count: int
    ) -> None:
        """Legt den Mittelpunkt eines Clusters ab oder ersetzt ihn."""
        werte: dict[str, Any] = {
            "cluster_id": cluster_id,
            "model_key": model_key,
            "embedding": list(vector),
            "member_count": member_count,
            "updated_at": datetime.now(UTC),
        }
        statement = insert(cluster_centroids).values(**werte)
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[cluster_centroids.c.cluster_id],
                set_={
                    "model_key": statement.excluded.model_key,
                    "embedding": statement.excluded.embedding,
                    "member_count": statement.excluded.member_count,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    def centroids(self, *, model_key: str) -> tuple[Centroid, ...]:
        """Alle Zentroide dieses Stores unter einem Modellschlüssel."""
        rows = self._connection.execute(
            select(cluster_centroids)
            .where(cluster_centroids.c.model_key == model_key)
            .order_by(cluster_centroids.c.cluster_id)
        ).mappings()
        return tuple(
            Centroid(
                cluster_id=row["cluster_id"],
                model_key=row["model_key"],
                vector=tuple(float(zahl) for zahl in row["embedding"]),
                member_count=int(row["member_count"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    def search_centroids(
        self, *, vector: Sequence[float], model_key: str, limit: int
    ) -> tuple[Neighbour, ...]:
        """Die ähnlichsten Zentroide zu einem freien Vektor — Stufe 1 der Suche (§12.4)."""
        if limit < 1:
            return ()
        distanz = cluster_centroids.c.embedding.cosine_distance(list(vector))
        rows = self._connection.execute(
            select(cluster_centroids.c.cluster_id, distanz.label("distanz"))
            .where(cluster_centroids.c.model_key == model_key)
            .order_by(distanz)
            .limit(limit)
        )
        return tuple(Neighbour(concept_id=row[0], similarity=1.0 - float(row[1])) for row in rows)

    def similar_centroids(
        self, *, cluster_id: str, model_key: str, limit: int
    ) -> tuple[Neighbour, ...]:
        """Die ähnlichsten anderen Zentroide (§13.2 Schritt 6)."""
        if limit < 1:
            return ()
        eigen = (
            select(cluster_centroids.c.embedding)
            .where(cluster_centroids.c.cluster_id == cluster_id)
            .scalar_subquery()
        )
        distanz = cluster_centroids.c.embedding.cosine_distance(eigen)
        rows = self._connection.execute(
            select(cluster_centroids.c.cluster_id, distanz.label("distanz"))
            .where(
                and_(
                    cluster_centroids.c.model_key == model_key,
                    cluster_centroids.c.cluster_id != cluster_id,
                )
            )
            .order_by(distanz)
            .limit(limit)
        )
        return tuple(Neighbour(concept_id=row[0], similarity=1.0 - float(row[1])) for row in rows)

    def bump(self, *, concept_id: str, cluster_id: str, score: float, run_id: UUID) -> int:
        """Zählt eine beobachtete Zuordnung hoch und meldet den neuen Stand (§13.3).

        ``seen_count`` wächst nur, wenn der Lauf ein anderer ist als der letzte. Sonst brächte ein
        Lauf, der dieselbe Zuordnung zweimal sieht — etwa über zwei Nachbarn desselben Clusters —,
        die Schwelle im Alleingang zum Auslösen, und die Bedingung "über mehrere Läufe hinweg"
        wäre nicht mehr das, was sie sagt.
        """
        jetzt = datetime.now(UTC)
        statement = insert(cluster_assignment_candidates).values(
            concept_id=concept_id,
            cluster_id=cluster_id,
            score=score,
            seen_count=1,
            first_seen_run=run_id,
            last_seen_run=run_id,
            last_seen_at=jetzt,
            excluded=False,
        )
        row = (
            self._connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        cluster_assignment_candidates.c.concept_id,
                        cluster_assignment_candidates.c.cluster_id,
                    ],
                    set_={
                        "score": statement.excluded.score,
                        "seen_count": case(
                            (
                                cluster_assignment_candidates.c.last_seen_run == run_id,
                                cluster_assignment_candidates.c.seen_count,
                            ),
                            else_=cluster_assignment_candidates.c.seen_count + 1,
                        ),
                        "last_seen_run": statement.excluded.last_seen_run,
                        "last_seen_at": statement.excluded.last_seen_at,
                    },
                ).returning(cluster_assignment_candidates.c.seen_count)
            )
            .mappings()
            .one()
        )
        return int(row["seen_count"])

    def candidates(self, *, min_seen: int = 1) -> tuple[AssignmentCandidate, ...]:
        """Die vorgemerkten Zuordnungen, meistbestätigte zuerst."""
        rows = self._connection.execute(
            select(cluster_assignment_candidates)
            .where(cluster_assignment_candidates.c.seen_count >= min_seen)
            .order_by(
                cluster_assignment_candidates.c.seen_count.desc(),
                cluster_assignment_candidates.c.score.desc(),
                cluster_assignment_candidates.c.concept_id,
            )
        ).mappings()
        return tuple(
            AssignmentCandidate(
                concept_id=row["concept_id"],
                cluster_id=row["cluster_id"],
                score=float(row["score"]),
                seen_count=int(row["seen_count"]),
                excluded=bool(row["excluded"]),
            )
            for row in rows
        )

    def expire(self, *, run_id: UUID) -> int:
        """Verwirft Kandidaten, die dieser Lauf nicht bestätigt hat (§13.3)."""
        result = self._connection.execute(
            delete(cluster_assignment_candidates).where(
                and_(
                    cluster_assignment_candidates.c.last_seen_run != run_id,
                    cluster_assignment_candidates.c.excluded.is_(False),
                )
            )
        )
        return result.rowcount

    def exclude(self, *, concept_id: str, cluster_id: str) -> None:
        """Vermerkt eine von Hand entfernte Zuordnung als gesperrt (§13.4).

        Der Ausschluss braucht keine Beobachtung, auf der er aufsetzt: Er kann für ein Paar
        entstehen, das nie Kandidat war — etwa wenn jemand eine von Hand angelegte Mitgliedschaft
        wieder entfernt.
        """
        jetzt = datetime.now(UTC)
        leer = UUID(int=0)
        statement = insert(cluster_assignment_candidates).values(
            concept_id=concept_id,
            cluster_id=cluster_id,
            score=0.0,
            seen_count=0,
            first_seen_run=leer,
            last_seen_run=leer,
            last_seen_at=jetzt,
            excluded=True,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    cluster_assignment_candidates.c.concept_id,
                    cluster_assignment_candidates.c.cluster_id,
                ],
                set_={"excluded": True, "last_seen_at": statement.excluded.last_seen_at},
            )
        )

    def include(self, *, concept_id: str, cluster_id: str) -> bool:
        """Hebt einen Ausschluss auf (§13.4, §17.3).

        Die Zeile wird gelöscht und nicht auf ``excluded = false`` gesetzt: Ein Kandidat mit
        ``seen_count = 0`` wäre eine Beobachtung, die nie stattgefunden hat, und der nächste Lauf
        würde von ihr aus zählen (§13.3).
        """
        result = self._connection.execute(
            delete(cluster_assignment_candidates).where(
                and_(
                    cluster_assignment_candidates.c.concept_id == concept_id,
                    cluster_assignment_candidates.c.cluster_id == cluster_id,
                    cluster_assignment_candidates.c.excluded.is_(True),
                )
            )
        )
        return result.rowcount > 0

    def exclusions(self) -> frozenset[tuple[str, str]]:
        """Alle gesperrten Paare aus Konzept und Cluster."""
        rows = self._connection.execute(
            select(
                cluster_assignment_candidates.c.concept_id,
                cluster_assignment_candidates.c.cluster_id,
            ).where(cluster_assignment_candidates.c.excluded.is_(True))
        )
        return frozenset((row[0], row[1]) for row in rows)


class SqlModelCallRepository(_StoreBound):
    """Die Modellaufrufe eines Stores in PostgreSQL (§7.4, §11.6)."""

    def record(self, call: ModelCall) -> None:
        """Hängt einen Aufruf an."""
        self._connection.execute(
            insert(model_calls).values(
                run_id=call.run_id,
                task=call.task,
                provider=call.provider,
                model=call.model,
                store=call.store,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                latency_ms=call.latency_ms,
                cost_estimate=call.cost_estimate,
                cache_hit=call.cache_hit,
                attempt=call.attempt,
                status=call.status,
                created_at=call.created_at or datetime.now(UTC),
            )
        )

    def usage(
        self, *, run_id: UUID | None = None, limit: int = defaults.MODEL_USAGE_LIMIT
    ) -> tuple[UsageSummary, ...]:
        """Die Auswertung, gruppiert nach Aufgabe und Modell, teuerste zuerst."""
        kosten = func.coalesce(func.sum(model_calls.c.cost_estimate), 0)
        statement = (
            select(
                model_calls.c.task,
                model_calls.c.provider,
                model_calls.c.model,
                func.count().label("calls"),
                func.count().filter(model_calls.c.cache_hit.is_(True)).label("cache_hits"),
                func.coalesce(func.sum(model_calls.c.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(model_calls.c.tokens_out), 0).label("tokens_out"),
                kosten.label("cost_estimate"),
                func.count()
                .filter(model_calls.c.status != defaults.MODEL_CALL_OK)
                .filter(model_calls.c.status != defaults.MODEL_CALL_CACHE_HIT)
                .label("failures"),
            )
            .group_by(model_calls.c.task, model_calls.c.provider, model_calls.c.model)
            .order_by(kosten.desc(), func.count().desc())
            .limit(limit)
        )
        if run_id is not None:
            statement = statement.where(model_calls.c.run_id == run_id)

        rows = self._connection.execute(statement).mappings()
        return tuple(
            UsageSummary(
                task=row["task"],
                provider=row["provider"],
                model=row["model"],
                calls=int(row["calls"]),
                cache_hits=int(row["cache_hits"]),
                tokens_in=int(row["tokens_in"]),
                tokens_out=int(row["tokens_out"]),
                cost_estimate_eur=float(row["cost_estimate"]),
                failures=int(row["failures"]),
            )
            for row in rows
        )

    def spent(self, run_id: UUID) -> tuple[int, float]:
        """Aufrufzahl und geschätzte Kosten eines Laufs — die Eingabe des Budget-Wächters (§11.6).

        Gezählt werden nur Aufrufe, die wirklich hinausgingen: Ein Cache-Treffer hat nichts
        verbraucht, und ein wegen des Budgets abgewiesener Aufruf würde den Wächter sonst gegen
        sich selbst zählen lassen.
        """
        row = self._connection.execute(
            select(
                func.count(),
                func.coalesce(func.sum(model_calls.c.cost_estimate), 0),
            ).where(
                and_(
                    model_calls.c.run_id == run_id,
                    model_calls.c.status == defaults.MODEL_CALL_OK,
                )
            )
        ).one()
        return int(row[0]), float(row[1])


class SqlChangeLogRepository(_StoreBound):
    """Änderungsjournal eines Stores in PostgreSQL."""

    def append(self, entry: ChangeEntry) -> ChangeEntry:
        """Hängt einen Eintrag an und gibt ihn mit vergebener ID zurück."""
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
        row = (
            self._connection.execute(insert(change_log).values(**werte).returning(change_log))
            .mappings()
            .first()
        )
        assert row is not None
        return _journal_eintrag(dict(row))

    def entries_for(self, concept_id: str) -> tuple[ChangeEntry, ...]:
        """Alle Einträge zu einem Konzept, neueste zuerst."""
        rows = self._connection.execute(
            select(change_log)
            .where(change_log.c.concept_id == concept_id)
            .order_by(change_log.c.changed_at.desc(), change_log.c.id.desc())
        ).mappings()
        return tuple(_journal_eintrag(dict(row)) for row in rows)

    def get(self, entry_id: int) -> ChangeEntry | None:
        """Der Eintrag zu einer ID, oder ``None`` — der Bezugspunkt des Undo (§17.3)."""
        row = (
            self._connection.execute(select(change_log).where(change_log.c.id == entry_id))
            .mappings()
            .first()
        )
        return None if row is None else _journal_eintrag(dict(row))

    def for_edge(self, edge_id: UUID) -> tuple[ChangeEntry, ...]:
        """Alle Einträge zu einer Kante, neueste zuerst."""
        rows = self._connection.execute(
            select(change_log)
            .where(change_log.c.edge_id == edge_id)
            .order_by(change_log.c.changed_at.desc(), change_log.c.id.desc())
        ).mappings()
        return tuple(_journal_eintrag(dict(row)) for row in rows)

    def recent(self, *, limit: int) -> tuple[ChangeEntry, ...]:
        """Die jüngsten Einträge dieses Stores — die Journalspalte der Betriebsansicht (§17.2)."""
        rows = self._connection.execute(
            select(change_log)
            .order_by(change_log.c.changed_at.desc(), change_log.c.id.desc())
            .limit(limit)
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
    """Baut einen Journaleintrag aus einer Zeile.

    Die Spalte ``id`` gehört seit Stufe 11 dazu: §17.3 verlangt "Undo über den
    ``change_log``-Eintrag", und dafür muss der Eintrag benennbar sein.
    """
    return ChangeEntry.model_validate(dict(row))
