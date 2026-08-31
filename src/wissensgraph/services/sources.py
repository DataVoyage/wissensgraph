"""Vom ``SourceDocument`` zum Konzept — und ein Lauf über eine Quelle (§8.5, §10.1).

Hier kreuzen sich die beiden Hälften dieser Stufe. Der Adapter hat die Eigenheiten der Quelle
abgestreift und ein DTO geliefert; die Konfiguration sagt, welche Identität, welcher Scope und
welcher Typ daraus werden. Beides zusammen ergibt einen Konzeptentwurf, und ab da gilt die
Kernoperation aus §10.2 unverändert.

Was hier **nicht** passiert, ist die Orchestrierung: ``runs``- und ``source_cursors``-Verwaltung,
Löschbehandlung, Advisory-Locks, ``--dry-run`` und die Job-Queue stehen in
:mod:`wissensgraph.services.sync`. Die Trennung ist keine Schichtung um ihrer selbst willen: Der
Dokumentendurchlauf hier ist ohne jede Lauf-Buchführung prüfbar, und der ``SyncService`` ist
umgekehrt gegen einen beliebigen Durchlauf prüfbar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError

from wissensgraph.config import defaults
from wissensgraph.config.sources import SourceConfig
from wissensgraph.domain.concepts import ConceptDraft
from wissensgraph.domain.ids import source_concept_id, split_concept_id
from wissensgraph.domain.upsert import UpsertOutcome
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import Cursor, SourceAdapter, SourceDocument
from wissensgraph.services.concepts import (
    ConceptService,
    ConceptValidationError,
    UpsertResult,
)

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
            # Übersetzt wird das Ziel, die Art bleibt: Sie ist eine Aussage der Quelle über die
            # Beziehung und hat mit dem Nummernkreis nichts zu tun.
            references=tuple(
                verweis.model_copy(update={"target": self.resolve_reference(verweis.target)})
                for verweis in document.references
            ),
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
    errors: int = 0
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
            "errors": self.errors,
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
        on_progress: Callable[[dict[str, int]], None] | None = None,
    ) -> IngestReport:
        """Liest die Dokumente einer Quelle und schreibt sie als Konzepte fort.

        Der Cursor kommt herein und geht wieder hinaus, ohne gespeichert zu werden. Wo er zwischen
        zwei Läufen liegt, entscheidet :class:`~wissensgraph.services.sync.SyncService` — dieser
        Dienst bleibt der reine Dokumentendurchlauf.

        Ein einzelnes fehlerhaftes Quellobjekt beendet den Lauf nicht. §21.3 legt das fest:
        "Einzelnes Quellobjekt fehlerhaft → überspringen, in ``runs.stats.errors`` zählen, Lauf
        fortsetzen." Ein Ausfall der *Quelle* dagegen wird durchgereicht: Er betrifft nicht ein
        Objekt, sondern alle noch ausstehenden, und der Cursor darf dann nicht fortschreiten.

        Args:
            adapter: Der konfigurierte Adapter dieser Quelle.
            cfg: Die Konfiguration derselben Quelle.
            cursor: Fortschrittsmarke des vorigen Laufs; ``None`` für einen Vollabgleich.
            actor: Wer die Änderungen verantwortet.
            run_id: Der Lauf, zu dem die Journaleinträge gehören.
            on_progress: Wird gelegentlich mit dem Zwischenstand der Zähler aufgerufen — die
                Grundlage von ``runs.stats`` während eines laufenden Syncs (§16.3).

        Returns:
            Die Zahlen des Laufs und die neue Fortschrittsmarke.

        Raises:
            SourceError: Wenn die Quelle während der Iteration ausfällt.
        """
        mapper = self.mapper_for(cfg)
        zaehler: dict[str, int] = {
            "documents": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "conflicts": 0,
            "edges_added": 0,
            "edges_removed": 0,
            "errors": 0,
        }
        namen = {
            UpsertOutcome.CREATED: "created",
            UpsertOutcome.UPDATED: "updated",
            UpsertOutcome.UNCHANGED: "unchanged",
            UpsertOutcome.CONFLICT: "conflicts",
        }

        for document in adapter.iter_documents(cursor):
            zaehler["documents"] += 1
            try:
                ergebnis: UpsertResult = self._concepts.upsert(
                    mapper.to_draft(document), actor=actor, run_id=run_id
                )
            except (ConceptValidationError, ValidationError) as exc:
                zaehler["errors"] += 1
                _log.warning(
                    "quelle.dokument.uebersprungen",
                    source=cfg.name,
                    external_id=document.external_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            zaehler[namen[ergebnis.outcome]] += 1
            zaehler["edges_added"] += len(ergebnis.edges_added)
            zaehler["edges_removed"] += len(ergebnis.edges_removed)

            if (
                on_progress is not None
                and zaehler["documents"] % defaults.SYNC_PROGRESS_INTERVAL == 0
            ):
                on_progress(dict(zaehler))

        # §8.5: "…und bei jedem Lauf erneut geprüft." Der Schritt gehört ans Ende, weil ein Ziel
        # erst durch diesen Lauf entstanden sein kann — die Kante darauf wurde angelegt, als es
        # das Ziel noch nicht gab.
        store = self._concepts.store_of_scope(cfg.target.scope)
        aufgeloest = self._concepts.refresh_edge_resolution(store)

        bericht = IngestReport(
            source=cfg.name,
            edges_resolved=aufgeloest,
            cursor=adapter.next_cursor(),
            **zaehler,
        )
        _log.info("quelle.lauf", **bericht.as_dict())
        return bericht
