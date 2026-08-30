"""Kanten — die zentrale Modellierungsentscheidung der Store-Trennung (§7.3, §7.7).

Eine Kante führt ihren Ziel-*Store* explizit mit. §7.3 begründet, warum: Ein Brücken-Konzept in
``personal`` verweist auf ein Konzept in ``shared``, und ein datenbankweiter Fremdschlüssel ist
dafür technisch unmöglich. Der Preis dafür ist, dass die Auflösung auf Anwendungsebene passiert —
das Feld ``resolved`` hält fest, ob sie beim letzten Versuch geglückt ist.

``resolved = false`` ist ausdrücklich kein Fehlerzustand (§8.5). Es heißt: "Das Ziel gab es beim
letzten Lauf noch nicht." Ein Sync-Lauf prüft solche Kanten erneut.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from wissensgraph.domain.base import DomainModel
from wissensgraph.domain.ids import validate_concept_id


class EdgeFields(DomainModel):
    """Die Felder einer Kante gemäß §7.4."""

    from_store: str = Field(min_length=1)
    from_id: str
    to_store: str = Field(min_length=1)
    to_id: str

    kind: str = Field(min_length=1)
    weight: float | None = Field(default=None, description="z. B. Kosinus-Ähnlichkeit.")
    confidence: float | None = Field(
        default=None, description="Modell-Confidence; NULL bei Code oder Mensch."
    )
    reasoning: str | None = Field(default=None, description="Ein Satz Begründung des Modells.")

    resolved: bool = Field(
        default=False,
        description="Ob das Zielkonzept beim letzten Auflösungsversuch gefunden wurde (§8.5).",
    )

    generated_by: str | None = Field(
        default=None,
        description=(
            "Erzeuger der Kante; NULL bedeutet manuell gesetzt. Läufe dürfen nur Kanten "
            "ersetzen, die sie selbst erzeugt haben (§10.4)."
        ),
    )
    generated_at: datetime | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None
    curated: bool = Field(
        default=False, description="Von Hand gesetzt oder bestätigt — bleibt unangetastet (§10.4)."
    )

    _check_ids = field_validator("from_id", "to_id")(validate_concept_id)

    @model_validator(mode="after")
    def _check_no_self(self) -> EdgeFields:
        """Spiegelt ``ck_edges_no_self`` (§7.4) in die Domäne.

        Die Prüfung steht an beiden Stellen mit Absicht: Die Datenbank schützt gegen jeden
        Schreibweg, die Domäne liefert die verständlichere Meldung.
        """
        if self.from_store == self.to_store and self.from_id == self.to_id:
            raise ValueError(
                f"Eine Kante darf nicht auf ihren Ausgangspunkt zeigen "
                f"('{self.from_id}' in '{self.from_store}')."
            )
        return self

    @property
    def triple(self) -> tuple[str, str, str, str, str]:
        """Die fünf Felder, die eine Kante eindeutig machen (``ux_edges_triple``, §7.4)."""
        return (self.from_store, self.from_id, self.to_store, self.to_id, self.kind)


class EdgeDraft(EdgeFields):
    """Eine vorgeschlagene Kante — ohne ID und ohne Anlagezeitpunkt."""


class Edge(EdgeFields):
    """Eine gespeicherte Kante."""

    id: UUID
    created_at: datetime


def new_edge_id() -> UUID:
    """Erzeugt die ID einer Kante.

    Die ID entsteht in der Anwendung und nicht über ``uuid_generate_v4()`` in der Datenbank: Der
    ``change_log`` hält zu jeder hinzugefügten Kante deren ``edge_id`` fest (§7.4), und dafür muss
    sie schon vor dem INSERT bekannt sein.
    """
    return uuid4()
