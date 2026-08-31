"""Konzepte — das OKF-Feldschema als Domänenmodell (§7.1, §7.4, §7.6).

Zwei Modelle, weil es zwei Rollen gibt:

* :class:`ConceptDraft` ist, was ein Adapter, die UI oder ein Agent *vorschlägt*. Er kennt keinen
  Store, keinen Content-Hash und keine Zeitstempel der Datenbank — all das entsteht erst beim
  Schreiben. Ein Entwurf, der seinen Store selbst mitbrächte, wäre ein Weg, die Store-Auflösung
  an der Registry vorbei zu treffen (§20.1).
* :class:`Concept` ist, was in der Datenbank steht: derselbe Inhalt plus Store, Hash und
  Zeitstempel.

Die Typen-Taxonomie (§7.2) wird hier bewusst *nicht* geprüft. Sie steht in der Konfiguration,
nicht im Code; welcher Typ in welchem Store zulässig ist, entscheidet der Dienst anhand von
``Settings`` (§6.1 Regel 1).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from wissensgraph.config import defaults
from wissensgraph.domain.base import DomainModel, unique_strings
from wissensgraph.domain.hashing import content_hash
from wissensgraph.domain.ids import validate_concept_id
from wissensgraph.domain.references import (
    SourceReference,
    extract_references,
    normalize_references,
)


class ConceptStatus(StrEnum):
    """Die Zustände des Konzept-Lebenszyklus (§7.6).

    ``TOMBSTONE`` ist der Ersatz für ein ``DELETE``: Wird ein Objekt in der Quelle gelöscht,
    bleiben Inhalt und Kanten erhalten, damit persönliche Notizen, die darauf verlinkt haben,
    nachvollziehbar bleiben. Das ist laut §7.6 der wichtigste Grund gegen echtes Löschen.
    """

    DRAFT = "draft"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    TOMBSTONE = "tombstone"


class ConceptFields(DomainModel):
    """Die Felder, die Entwurf und gespeichertes Konzept gemeinsam haben (§7.1)."""

    id: str
    scope: str = Field(min_length=1)
    type: str = Field(min_length=1)

    title: str | None = None
    description: str | None = None
    body: str | None = None
    resource: str | None = None

    tags: tuple[str, ...] = ()
    audience: tuple[str, ...] = Field(
        default=(),
        description=(
            "'role:'/'team:'-Werte für die spätere Ambient-Filterung. Ausdrücklich KEIN "
            "Zugriffsschutz (§7.1) — der liegt allein in der Store-Trennung."
        ),
    )

    status: ConceptStatus = ConceptStatus(defaults.CONCEPT_STATUS_DEFAULT)
    stale_after: datetime | None = None

    source_name: str | None = None
    external_id: str | None = None
    source_updated_at: datetime | None = None

    generated_by: str | None = None
    generated_at: datetime | None = None

    curated: bool = Field(
        default=False,
        description="Von Hand angelegt oder verändert. Steuert Regel 4 aus §10.2.",
    )

    _normalize_tags = field_validator("tags", "audience", mode="before")(unique_strings)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_concept_id(value)

    @model_validator(mode="after")
    def _check_provenance(self) -> ConceptFields:
        """§7.1: ``generated_by``/``generated_at`` sind "bei Generiertem Pflicht"."""
        if (self.generated_by is None) != (self.generated_at is None):
            raise ValueError(
                "generated_by und generated_at gehören zusammen: Entweder beide sind gesetzt "
                "(dann ist die Provenienz vollständig) oder beide nicht (§7.1)."
            )
        if (self.source_name is None) != (self.external_id is None):
            raise ValueError(
                "source_name und external_id gehören zusammen. Der eindeutige Index "
                "ux_concepts_source (§7.4) setzt beide voraus."
            )
        return self

    @property
    def is_from_source(self) -> bool:
        """Ob das Konzept aus einem Quellsystem stammt — Grundlage der Kurationsregeln (§10.4)."""
        return self.source_name is not None


class ConceptDraft(ConceptFields):
    """Ein vorgeschlagenes Konzept, noch ohne Store und Zeitstempel (§10.2)."""

    references: tuple[SourceReference, ...] = Field(
        default=(),
        description=(
            "Referenzen, die der Adapter zusätzlich zum body-Text mitliefert (§8.5). Sie werden "
            "mit den '[[id]]'-Referenzen aus dem body vereinigt. Eine blanke ID gilt als "
            "gewöhnliche 'references'-Kante."
        ),
    )

    _lift_references = field_validator("references", mode="before")(normalize_references)

    @property
    def content_hash(self) -> str:
        """Der Hash über die drei Inhaltsfelder (§10.3)."""
        return content_hash(title=self.title, description=self.description, body=self.body)

    @property
    def body_references(self) -> tuple[str, ...]:
        """Die ``[[id]]``-Referenzen aus dem Fließtext, ohne Dubletten und ohne Selbstbezug.

        Der Selbstbezug fällt hier und nicht erst in der Datenbank weg: ``ck_edges_no_self``
        (§7.4) würde ihn als Fehler abweisen, und ein Konzept, das sich selbst erwähnt, ist kein
        Fehler, sondern gewöhnlicher Text.
        """
        return self._ohne_selbstbezug(extract_references(self.body))

    @property
    def source_references(self) -> tuple[SourceReference, ...]:
        """Die vom Adapter gemeldeten Referenzen, die *nicht* schon im Fließtext stehen (§8.5).

        Die Trennung ist keine Buchhaltung, sondern eine Frage der Herkunft: §8.5 verlangt für
        Referenzen aus der Quelle ``generated_by: 'code:source-reference'``, für die aus dem Text
        gilt ``code:body-reference``. Steht derselbe Verweis in beidem, gewinnt der Text — er ist
        der belegbare Nachweis, und zwei Kanten mit demselben Tripel kann es ohnehin nicht geben.

        "Derselbe Verweis" heißt: dasselbe Ziel **und** dieselbe Kantenart. Ein Vorgang, der im
        Text auf sein Epic zeigt und in den Feldern als dessen Mitglied geführt wird, sagt zwei
        verschiedene Dinge über dasselbe Paar; ``ux_edges_triple`` (§7.4) lässt beide Kanten zu,
        weil die Art Teil des Tripels ist.
        """
        aus_dem_text = set(self.body_references)
        gesehen: set[tuple[str, str]] = set()
        result: list[SourceReference] = []
        for verweis in self.references:
            schluessel = (verweis.target, verweis.kind)
            if verweis.target == self.id or schluessel in gesehen:
                continue
            if verweis.kind == defaults.EDGE_KIND_REFERENCES and verweis.target in aus_dem_text:
                continue
            gesehen.add(schluessel)
            result.append(verweis)
        return tuple(result)

    @property
    def all_references(self) -> tuple[str, ...]:
        """Beide Herkünfte zusammen, Text zuerst — für Prüfungen, denen die Herkunft egal ist.

        Nur die Ziele: Wer diese Eigenschaft benutzt, fragt "auf was zeigt dieses Konzept", nicht
        "auf welche Weise".
        """
        aus_der_quelle: list[str] = []
        for verweis in self.source_references:
            if verweis.target not in aus_der_quelle:
                aus_der_quelle.append(verweis.target)
        return (*self.body_references, *aus_der_quelle)

    def _ohne_selbstbezug(self, kandidaten: tuple[str, ...]) -> tuple[str, ...]:
        """Entdoppelt unter Beibehaltung der Reihenfolge und wirft den Selbstbezug weg."""
        result: list[str] = []
        for candidate in kandidaten:
            if candidate != self.id and candidate not in result:
                result.append(candidate)
        return tuple(result)


class Concept(ConceptFields):
    """Ein gespeichertes Konzept: Inhalt plus Store, Hash und Zeitstempel (§7.4)."""

    store: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    verified_by: str | None = None
    verified_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    @property
    def is_verified(self) -> bool:
        """Ob eine Kuration den aktuellen Stand bestätigt hat (§10.4)."""
        return self.verified_by is not None or self.verified_at is not None
