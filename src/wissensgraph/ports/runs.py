"""Ports der Lauf-Orchestrierung: ``runs``, ``source_cursors`` und die Sperre je Quelle (§10).

Drei Protokolle, drei getrennte Fragen:

* :class:`RunRepository` — was ist gelaufen, was läuft gerade, wie ist es ausgegangen (§7.4).
* :class:`SourceCursorRepository` — wo stand eine Quelle beim letzten Mal (§7.4, §22.3).
* :class:`SourceLocks` — läuft dieselbe Quelle schon irgendwo (§10.5).

Die Sperre ist ein eigener Port und kein Nebenprodukt des Repositories, weil sie eine andere
Lebensdauer hat: Sie muss über den *ganzen* Lauf gehalten werden, während eine Arbeitseinheit
genau eine Transaktion umfasst. Eine Sperre, die mit dem ersten Commit fällt, schützt nichts —
sie wäre genau bis zum ersten geschriebenen Dokument wirksam.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from wissensgraph.domain.base import DomainModel
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.ports.sources import Cursor


class SourceBusy(RuntimeError):
    """Für diese Quelle läuft bereits ein Sync (§10.5).

    §10.5: "Pro Quelle läuft höchstens ein Sync gleichzeitig … Ein zweiter Startversuch liefert
    ``409 Conflict`` mit der ID des laufenden Runs." Die ID kennt die Sperre selbst nicht — sie
    weiß nur, dass jemand hält. Der Dienst reicht die Ausnahme deshalb mit ``run_id`` angereichert
    weiter, sobald er den laufenden Eintrag nachgeschlagen hat.
    """

    def __init__(self, source: str, run_id: UUID | None = None) -> None:
        self.source = source
        self.run_id = run_id
        laufend = f" Laufender Lauf: {run_id}." if run_id is not None else ""
        super().__init__(
            f"Für die Quelle '{source}' läuft bereits ein Sync.{laufend} "
            f"Pro Quelle ist höchstens ein Lauf gleichzeitig zulässig (§10.5)."
        )


class SourceCursorState(DomainModel):
    """Der gespeicherte Stand einer Quelle (§7.4, Tabelle ``source_cursors``).

    ``last_full_sync`` steht neben dem Cursor und nicht darin: Der Cursor ist opak und gehört dem
    Adapter (§8.2), der Zeitpunkt des letzten Vollabgleichs dagegen dem Kern. Wer beides
    vermischte, müsste in einen Wert hineinsehen, den er laut Kontrakt nicht lesen darf.
    """

    source_name: str = Field(min_length=1)
    cursor: Cursor = Field(default_factory=Cursor)
    last_full_sync: datetime | None = None
    updated_at: datetime | None = None


@runtime_checkable
class RunRepository(Protocol):
    """Die Läufe genau eines Stores (§7.4).

    Ein Lauf wird dort verbucht, wo er schreibt. Für einen Sync ist das der Store des Ziel-Scopes
    — ein Lauf über eine persönliche Quelle hinterlässt damit keine Spur im geteilten Store
    (Leitprinzip 2).
    """

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def create(self, run: Run) -> None:
        """Legt einen Lauf an."""

    def get(self, run_id: UUID) -> Run | None:
        """Der Lauf zu einer ID, oder ``None``."""

    def update(self, run: Run) -> None:
        """Schreibt Zustand, Fortschritt, Statistik und Fehler eines Laufs fort.

        ``kind`` und ``params`` bleiben unberührt: Womit ein Lauf gestartet wurde, ist eine
        Tatsache und ändert sich nicht mehr.
        """

    def recent(self, *, kind: RunKind | None = None, limit: int = 20) -> tuple[Run, ...]:
        """Die zuletzt begonnenen Läufe, neueste zuerst."""

    def active_for_source(self, source: str) -> Run | None:
        """Ein noch nicht abgeschlossener Sync-Lauf dieser Quelle, falls es einen gibt (§10.5).

        Liefert die ID, die eine abgewiesene zweite Anfrage nennen soll. Bewusst *ohne* eigene
        Sperrwirkung: Die Abweisung entscheidet der Advisory-Lock, nicht diese Abfrage. Wäre es
        umgekehrt, entstünde zwischen Abfrage und Anlage genau das Zeitfenster, in dem zwei
        Läufe zugleich starten könnten.
        """


@runtime_checkable
class SourceCursorRepository(Protocol):
    """Die Fortschrittsmarken der Quellen genau eines Stores (§7.4)."""

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def get(self, source_name: str) -> SourceCursorState | None:
        """Der gespeicherte Stand einer Quelle, oder ``None`` bei noch nie gelaufen."""

    def save(
        self, source_name: str, cursor: Cursor, *, full_sync_at: datetime | None = None
    ) -> None:
        """Schreibt den Stand einer Quelle fort.

        Args:
            source_name: Der Name der Quellinstanz.
            cursor: Die neue Fortschrittsmarke; wird unverändert als JSONB abgelegt.
            full_sync_at: Zeitpunkt eines soeben abgeschlossenen Vollabgleichs. ``None`` lässt
                ``last_full_sync`` stehen — ein inkrementeller Lauf soll den letzten
                Vollabgleich nicht vergessen machen.
        """

    def delete(self, source_name: str) -> bool:
        """Vergisst den Stand einer Quelle; der nächste Lauf ist damit ein Vollabgleich.

        Returns:
            Ob überhaupt ein Stand gespeichert war.
        """


class SourceLocks(Protocol):
    """Die gegenseitige Ausschließung je Quelle (§10.5)."""

    def hold(self, *, store: str, name: str) -> AbstractContextManager[None]:
        """Hält die Sperre einer Quelle für die Dauer des Blocks.

        Raises:
            SourceBusy: Wenn die Sperre bereits gehalten wird. Es wird *nicht* gewartet: Ein
                zweiter Startversuch soll abgewiesen werden und nicht in eine Warteschlange
                geraten, die niemand beobachtet (§10.5).
        """


#: Die Iterator-Form, die ein ``@contextmanager`` einer Umsetzung von :class:`SourceLocks`
#: zurückgibt. Nur zur Lesbarkeit der Signaturen.
LockScope = Iterator[None]
