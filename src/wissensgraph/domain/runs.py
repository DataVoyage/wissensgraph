"""Läufe — die Buchführung über jede Hintergrundarbeit (§7.4, §10.1, §16.3).

Ein Lauf ist der Gegenstand, der einen Sync, ein Embedding oder ein Clustering
*nachvollziehbar* macht. §24 nennt für Stufe 4 drei Eigenschaften, und alle drei hängen an
diesem Modell:

* **wiederholbar** — die Parameter stehen im Lauf, nicht nur im Aufruf. Was ein Lauf getan hat,
  lässt sich aus seiner Zeile rekonstruieren.
* **nachvollziehbar** — jeder Journaleintrag trägt die ``run_id`` (§7.4). Vom Lauf zur einzelnen
  Änderung und zurück ist damit ein Join und keine Rekonstruktion aus Zeitstempeln.
* **abbrechbar** — ein Lauf hat einen Endzustand, und ``failed`` ist einer davon. Ein Abbruch
  hinterlässt eine Zeile mit Grund statt einer Lücke.

Das Modell ist wie alle Domänenmodelle unveränderlich. Ein Lauf schreitet deshalb über
:meth:`Run.gestartet`, :meth:`Run.fortschritt` und :meth:`Run.beendet` voran — jede Stufe ein
neues Objekt. Das ist kein Selbstzweck: So kann kein Codepfad einen bereits abgeschlossenen Lauf
nachträglich umschreiben, ohne dass es im Aufrufer sichtbar wird.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import Field

from wissensgraph.domain.base import DomainModel


class RunKind(StrEnum):
    """Die Arten eines Laufs (§7.4, Spalte ``runs.kind``).

    Vollständig aufgezählt, obwohl erst ``sync`` umgesetzt ist: Die Spalte hat keinen
    CHECK-Constraint, und eine Aufzählung, die mit jeder Stufe wächst, ließe sich in einer
    Auswertung über ältere Läufe nicht mehr sicher lesen.
    """

    SYNC = "sync"
    EMBED = "embed"
    CLUSTER = "cluster"
    RELATIONS = "relations"
    LINK_ORPHANS = "link_orphans"
    EXPORT = "export"


class RunStatus(StrEnum):
    """Der Zustand eines Laufs (§7.4, Spalte ``runs.status``)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        """Ob dieser Zustand das Ende des Laufs ist.

        Grundlage der Nebenläufigkeitsprüfung aus §10.5: Blockierend sind genau die Läufe, die
        noch keinen Endzustand erreicht haben.
        """
        return self in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


class Run(DomainModel):
    """Ein Lauf mit Parametern, Zustand, Fortschritt und Statistik (§7.4)."""

    id: UUID
    kind: RunKind
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Womit der Lauf aufgerufen wurde — Quellname, '--full', '--dry-run' (§19).",
    )
    status: RunStatus = RunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Zähler des Laufs. Ausschließlich Zahlen und Namen, nie Inhalte — die Statistik "
            "erscheint in Log, CLI und UI (§21.1)."
        ),
    )
    error: str | None = None

    @property
    def is_final(self) -> bool:
        """Ob der Lauf abgeschlossen ist."""
        return self.status.is_final

    @property
    def duration_seconds(self) -> float | None:
        """Die Dauer des Laufs, sobald sie feststeht — Grundlage von ``wg_run_duration_seconds``."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def gestartet(self, now: datetime) -> Self:
        """Der Lauf im Zustand ``running``."""
        return self.model_copy(update={"status": RunStatus.RUNNING, "started_at": now})

    def fortschritt(self, stats: dict[str, Any]) -> Self:
        """Zwischenstand ohne Zustandswechsel (§16.3: "schreibt Fortschritt und Statistik").

        ``progress`` bleibt dabei bewusst unberührt. Ein Anteil setzt eine bekannte Gesamtmenge
        voraus, und die hat ein Quell-Adapter nicht: :meth:`iter_documents` ist ein Generator und
        weiß selbst nicht, wie viele Objekte noch kommen (§8.2). Eine geschätzte Prozentzahl wäre
        eine Behauptung; die Zahl der bisher verarbeiteten Dokumente ist eine Tatsache.
        """
        return self.model_copy(update={"stats": dict(stats)})

    def beendet(
        self,
        *,
        status: RunStatus,
        now: datetime,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Self:
        """Der abgeschlossene Lauf.

        ``progress`` steht am Ende auf 1.0 genau dann, wenn der Lauf erfolgreich war. Ein
        gescheiterter Lauf behält seinen letzten Stand — er *ist* nicht fertig geworden, und die
        Anzeige soll das zeigen.
        """
        return self.model_copy(
            update={
                "status": status,
                "finished_at": now,
                "progress": 1.0 if status is RunStatus.SUCCEEDED else self.progress,
                "stats": dict(stats) if stats is not None else self.stats,
                "error": error,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für Log, CLI und ``GET /api/v1/runs`` (§16.2)."""
        return self.model_dump(mode="json")


def new_run_id() -> UUID:
    """Die ID eines neuen Laufs.

    Als eigene Funktion und nicht als Default im Modell: Ein Lauf bekommt seine ID an genau einer
    Stelle, und ein Test kann sie ersetzen, ohne das Modell anzufassen.
    """
    return uuid4()
