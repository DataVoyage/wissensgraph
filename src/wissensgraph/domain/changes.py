"""Änderungsjournal (§7.4, Tabelle ``change_log``).

Das Journal ersetzt, was mit dem Verzicht auf ein Dateibundle verloren ging: die git-native
Historie (§7.8). Jede Änderung am Graphen hinterlässt hier eine Zeile mit Akteur und Lauf.

Zwei Änderungsarten stehen nicht in der Aufzählung von §7.4, werden aber an anderer Stelle des
Dokuments ausdrücklich verlangt. Sie sind deshalb hier ergänzt:

* ``curation_conflict`` — §10.2 Regel 4: "der Konflikt landet als 'curation_conflict' im
  change_log".
* ``verification_reset`` — §10.4: ``verified_*`` "wird bei inhaltlicher Änderung zurückgesetzt,
  mit change_log-Eintrag". Eine bestätigte Beziehung gilt für einen bestimmten Inhaltsstand;
  ändert sich der Inhalt, ist die Bestätigung nicht mehr gedeckt.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from wissensgraph.domain.base import DomainModel

#: Schlüssel, unter dem der Hash des abgewehrten Quellinhalts im ``detail`` eines
#: Kurationskonflikts steht. Er macht den Konflikt identifizierbar: Solange die Quelle denselben
#: Inhalt liefert, ist es derselbe Konflikt und nicht bei jedem Lauf ein neuer.
CONFLICT_SOURCE_HASH_KEY = "source_content_hash"


class ChangeType(StrEnum):
    """Die Arten einer Änderung (§7.4, ergänzt um §10.2 Regel 4 und §10.4)."""

    CREATED = "created"
    UPDATED = "updated"
    SOURCE_DELETED = "source_deleted"
    CLUSTER_ASSIGNED = "cluster_assigned"
    CLUSTER_REMOVED = "cluster_removed"
    EDGE_ADDED = "edge_added"
    EDGE_REMOVED = "edge_removed"
    VERIFIED = "verified"
    REJECTED = "rejected"
    STATUS_CHANGED = "status_changed"
    MERGED = "merged"

    CURATION_CONFLICT = "curation_conflict"
    VERIFICATION_RESET = "verification_reset"


class ChangeEntry(DomainModel):
    """Ein Eintrag im Änderungsjournal.

    ``detail`` hält bewusst nur Metadaten fest — Feldnamen, Hashes, Zähler —, niemals Inhalte.
    Das Journal liegt zwar im selben Store wie das Konzept und unterliegt damit derselben
    Trennung, aber ein zweiter Ablageort für Bodies wäre eine zweite Stelle, an der ein Export
    oder ein Log versehentlich Inhalte nach außen trägt (§21.1).
    """

    id: int | None = Field(
        default=None,
        description=(
            "Laufende Nummer aus der Datenbank (``BIGSERIAL``, §7.4); ``None``, solange der "
            "Eintrag noch nicht geschrieben ist. Sie ist der Bezugspunkt des Undo aus §17.3: "
            "Rückgängig gemacht wird ein *bestimmter* Eintrag, nicht 'die letzte Änderung'."
        ),
    )
    change_type: ChangeType
    actor: str = Field(
        min_length=1,
        description="'system:sync', 'system:cluster', 'user:<id>' oder 'agent:<id>' (§7.4).",
    )
    concept_id: str | None = None
    edge_id: UUID | None = None
    run_id: UUID | None = None
    changed_at: datetime | None = Field(
        default=None,
        description="Wird beim Schreiben gesetzt; die Datenbank hat einen Default auf now().",
    )
    detail: dict[str, object] | None = None

    @model_validator(mode="after")
    def _check_target(self) -> ChangeEntry:
        if self.concept_id is None and self.edge_id is None:
            raise ValueError(
                "Ein Journaleintrag ohne concept_id und ohne edge_id ist nicht zuzuordnen und "
                "damit wertlos."
            )
        return self
