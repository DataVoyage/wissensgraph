"""Port der Job-Queue (§5.1, §16.3, §23).

§16.3 legt den asynchronen Weg fest: "Alle ``POST /runs/*`` legen einen Eintrag in ``runs`` an,
stellen einen Job in die Redis-Queue und antworten mit ``202 Accepted``." Der ``worker`` führt
aus.

Der Job trägt deshalb **keine Nutzlast**, sondern nur einen Verweis: die ID des bereits
angelegten Laufs, seine Art und seine Parameter. Der Zustand liegt in der Datenbank, nicht in der
Queue. Das ist der Unterschied zwischen einer Queue, die Arbeit *anstößt*, und einer, die Arbeit
*enthält* — geht ein Job verloren, steht der Lauf immer noch als ``queued`` in ``runs`` und lässt
sich erneut einstellen. Umgekehrt wäre die Queue eine zweite Wahrheit über den Zustand des
Systems.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from wissensgraph.domain.base import DomainModel
from wissensgraph.domain.runs import RunKind


class Job(DomainModel):
    """Der Auftrag, einen bereits angelegten Lauf auszuführen (§16.3)."""

    run_id: UUID
    kind: RunKind
    store: str = Field(
        min_length=1,
        description=(
            "Der Store, in dem der Lauf verbucht ist. Ohne ihn müsste der Worker beide Stores "
            "nach der Lauf-ID durchsuchen — und damit auch den personal-Store anfassen, wenn "
            "der Lauf im geteilten liegt (§20.1)."
        ),
    )
    params: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class JobQueue(Protocol):
    """Eine Warteschlange für Jobs — im Betrieb Redis, im Test ein Fake."""

    def enqueue(self, job: Job) -> None:
        """Stellt einen Job ein."""

    def reserve(self, *, timeout_seconds: float) -> Job | None:
        """Nimmt den nächsten Job entgegen, oder ``None``, wenn die Frist ohne einen verstreicht.

        Die Frist ist Pflicht und nicht optional: Ein Worker, der unbegrenzt blockiert, lässt sich
        nicht sauber herunterfahren — ein ``docker compose down`` müsste ihn abschießen.
        """

    def size(self) -> int:
        """Wie viele Jobs warten. Für ``wg doctor`` und die Kennzahlen (§21.2)."""
