"""Vom ``SourceDocument`` zum Konzept — und ein Lauf über eine Quelle (§8.5, §10.1).

Hier kreuzen sich die beiden Hälften dieser Stufe. Der Adapter hat die Eigenheiten der Quelle
abgestreift und ein DTO geliefert; die Konfiguration sagt, welche Identität, welcher Scope und
welcher Typ daraus werden. Beides zusammen ergibt einen Konzeptentwurf, und ab da gilt die
Kernoperation aus §10.2 unverändert.

Was hier **nicht** passiert, gehört zu Stufe 4 und ist absichtlich ausgespart: ``runs``- und
``source_cursors``-Verwaltung, Löschbehandlung, Advisory-Locks, ``--dry-run``, Job-Queue. Der
Lauf in :class:`SourceIngestService` ist der kürzeste Weg von einer Quelle in den Graphen — genug,
um die Abnahme der Stufe 3 zu belegen ("der Fixture-Korpus ist vollständig als Konzepte
abgebildet"), und bewusst zu wenig, um schon eine Orchestrierung zu sein.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.config.sources import SourceConfig
from wissensgraph.domain.concepts import ConceptDraft
from wissensgraph.domain.ids import source_concept_id, split_concept_id
from wissensgraph.domain.upsert import UpsertOutcome
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import Cursor, SourceAdapter, SourceDocument
from wissensgraph.services.concepts import ConceptService, UpsertResult

_log = get_logger(__name__)


class SourceMapper:
    """Übersetzt die Dokumente einer Quelle in Konzeptentwürfe (§8.4, §8.5).

    Der Mapper trifft genau drei Entscheidungen, und alle drei stehen in der Konfiguration:
    welche ID (``id_prefix``), welcher Scope und welcher Typ (``target``). Nichts davon darf aus
    dem Dokument selbst kommen — sonst könnte eine Quelle über ein Feld bestimmen, in welche
    Datenbank sie schreibt (§20.1). Die einzige Ausnahme ist ``type_hint``, und die ist eng: Sie
    wählt einen anderen *Typ*, nie einen anderen Scope.
    """

    def __init__(self, cfg: SourceConfig, *, known_prefixes: Iterable[str] = ()) -> None:
        """
        Args:
            cfg: Die Konfiguration dieser Quellinstanz.
            known_prefixes: Präfixe, die als bereits interne IDs gelten (siehe
                :meth:`resolve_reference`). Üblicherweise die ``id_prefix``-Werte aller
                konfigurierten Quellen.
        """
        self._cfg = cfg
        self._known = frozenset(known_prefixes) | {cfg.id_prefix}

    @property
    def config(self) -> SourceConfig:
        """Die Konfiguration dieser Quellinstanz."""
        return self._cfg

    def concept_id(self, external_id: str) -> str:
        """Die interne ID eines Quellobjekts (§7.5)."""
        return source_concept_id(self._cfg.id_prefix, external_id)

    def resolve_reference(self, reference: str) -> str:
        """Übersetzt eine vom Adapter gemeldete Referenz in eine interne ID (§8.5).

        §8.5 legt den Regelfall fest: "Ein Adapter liefert Referenzen als externe IDs. Der
        ``SyncService`` übersetzt sie über das Präfix der Quelle in interne IDs."

        Der Sonderfall daneben ist die quellübergreifende Referenz: Ein Jira-Vorgang, der auf eine
        Confluence-Seite zeigt, kann das mit einer externen ID nicht ausdrücken — die gehört einem
        anderen System. Nennt eine Referenz deshalb bereits ein *bekanntes* Präfix, wird sie
        unverändert übernommen. Die Einschränkung auf bekannte Präfixe ist wesentlich: Sonst
        würde eine externe ID, die zufällig einen Doppelpunkt enthält, stillschweigend als
        fremde Konzept-ID gelesen und zeigte auf ein Konzept, das es nie geben wird.
        """
        try:
            praefix, _ = split_concept_id(reference)
        except ValueError:
            return self.concept_id(reference)
        if praefix in self._known:
            return reference
        return self.concept_id(reference)

    def to_draft(self, document: SourceDocument) -> ConceptDraft:
        """Baut den Konzeptentwurf zu einem Quelldokument."""
        ziel = self._cfg.target
        return ConceptDraft(
            id=self.concept_id(document.external_id),
            scope=ziel.scope,
            type=document.type_hint or ziel.default_type,
            title=document.title,
            description=document.description,
            body=document.body,
            resource=document.resource,
            tags=document.tags,
            # Der Quellname ist der Name der *Instanz* (``confluence-eng``) und nicht der des
            # Adapters. Zwei Instanzen desselben Adapters mit verschiedenen Zielen sind
            # ausdrücklich vorgesehen (§8.4); erst der Instanzname sagt, woher ein Konzept kommt.
            source_name=self._cfg.name,
            external_id=document.external_id,
            source_updated_at=document.updated_at,
            references=tuple(self.resolve_reference(item) for item in document.references),
        )


@dataclass(frozen=True)
class IngestReport:
    """Das Ergebnis eines Laufs über eine Quelle — die Vorform der Lauf-Statistik aus §10.1."""

    source: str
    documents: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    edges_resolved: int = 0
    cursor: Cursor = field(default_factory=Cursor)

    @property
    def changed(self) -> int:
        """Wie viele Konzepte tatsächlich geschrieben wurden."""
        return self.created + self.updated

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für Log und CLI — ohne ein einziges Inhaltsfeld (§21.1)."""
        return {
            "source": self.source,
            "documents": self.documents,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "conflicts": self.conflicts,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "edges_resolved": self.edges_resolved,
        }


class SourceIngestService:
    """Führt die Dokumente einer Quelle in den Graphen über (§10.1, ohne die Orchestrierung)."""

    def __init__(self, concepts: ConceptService, *, known_prefixes: Iterable[str] = ()) -> None:
        """
        Args:
            concepts: Der Dienst, der die Kernoperation aus §10.2 ausführt.
            known_prefixes: Die ``id_prefix``-Werte aller konfigurierten Quellen — Grundlage der
                quellübergreifenden Referenzauflösung.
        """
        self._concepts = concepts
        self._known = tuple(known_prefixes)

    def mapper_for(self, cfg: SourceConfig) -> SourceMapper:
        """Der Mapper einer Quelle, mit den Präfixen aller Quellen im Rücken."""
        return SourceMapper(cfg, known_prefixes=self._known)

    def ingest(
        self,
        adapter: SourceAdapter,
        cfg: SourceConfig,
        *,
        cursor: Cursor | None = None,
        actor: str = defaults.ACTOR_SYNC,
        run_id: UUID | None = None,
    ) -> IngestReport:
        """Liest die Dokumente einer Quelle und schreibt sie als Konzepte fort.

        Der Cursor kommt herein und geht wieder hinaus, ohne gespeichert zu werden: Wo er
        zwischen zwei Läufen liegt, entscheidet Stufe 4 (``source_cursors``). Hier bleibt er ein
        Wert, damit der inkrementelle Lauf schon jetzt prüfbar ist.

        Args:
            adapter: Der konfigurierte Adapter dieser Quelle.
            cfg: Die Konfiguration derselben Quelle.
            cursor: Fortschrittsmarke des vorigen Laufs; ``None`` für einen Vollabgleich.
            actor: Wer die Änderungen verantwortet.
            run_id: Der Lauf, zu dem die Journaleinträge gehören.

        Returns:
            Die Zahlen des Laufs und die neue Fortschrittsmarke.
        """
        mapper = self.mapper_for(cfg)
        zaehler = {
            UpsertOutcome.CREATED: 0,
            UpsertOutcome.UPDATED: 0,
            UpsertOutcome.UNCHANGED: 0,
            UpsertOutcome.CONFLICT: 0,
        }
        dokumente = 0
        hinzugefuegt = 0
        entfernt = 0

        for document in adapter.iter_documents(cursor):
            dokumente += 1
            ergebnis: UpsertResult = self._concepts.upsert(
                mapper.to_draft(document), actor=actor, run_id=run_id
            )
            zaehler[ergebnis.outcome] += 1
            hinzugefuegt += len(ergebnis.edges_added)
            entfernt += len(ergebnis.edges_removed)

        # §8.5: "…und bei jedem Lauf erneut geprüft." Der Schritt gehört ans Ende, weil ein Ziel
        # erst durch diesen Lauf entstanden sein kann — die Kante darauf wurde angelegt, als es
        # das Ziel noch nicht gab.
        store = self._concepts.store_of_scope(cfg.target.scope)
        aufgeloest = self._concepts.refresh_edge_resolution(store)

        bericht = IngestReport(
            source=cfg.name,
            documents=dokumente,
            created=zaehler[UpsertOutcome.CREATED],
            updated=zaehler[UpsertOutcome.UPDATED],
            unchanged=zaehler[UpsertOutcome.UNCHANGED],
            conflicts=zaehler[UpsertOutcome.CONFLICT],
            edges_added=hinzugefuegt,
            edges_removed=entfernt,
            edges_resolved=aufgeloest,
            cursor=adapter.next_cursor(),
        )
        _log.info("quelle.lauf", **bericht.as_dict())
        return bericht
