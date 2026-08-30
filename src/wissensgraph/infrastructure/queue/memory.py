"""Job-Queue im Speicher — für Prozesse ohne Broker.

Sie ist kein Testhilfsmittel, sondern der Normalfall für einen Weg, den §19 ausdrücklich vorsieht:
``docker compose exec worker wg sync --source confluence-eng`` läuft *synchron* im aufrufenden
Prozess. Es gibt dabei niemanden, der einen Job entgegennimmt, und ein Redis-Zwang würde die CLI
von einem Dienst abhängig machen, den sie gar nicht braucht.

Die Warteschlange ist prozessweit und überlebt keinen Neustart. Genau deshalb ist sie für den
``worker``-Dienst untauglich, und genau deshalb wählt :mod:`wissensgraph.runtime` sie nur, wenn
keine ``broker_url`` konfiguriert ist.
"""

from __future__ import annotations

from collections import deque

from wissensgraph.ports.queue import Job


class MemoryJobQueue:
    """Eine FIFO-Warteschlange im Speicher; erfüllt den Port :class:`JobQueue`."""

    def __init__(self) -> None:
        self._jobs: deque[Job] = deque()

    def enqueue(self, job: Job) -> None:
        """Stellt einen Job ein."""
        self._jobs.append(job)

    def reserve(self, *, timeout_seconds: float) -> Job | None:
        """Nimmt den nächsten Job entgegen.

        Die Frist wird nicht abgewartet: In einem Prozess ohne zweiten Erzeuger kann nach einer
        leeren Warteschlange nichts mehr dazukommen. Ein ``sleep`` wäre reine Verzögerung.
        """
        return self._jobs.popleft() if self._jobs else None

    def size(self) -> int:
        """Wie viele Jobs warten."""
        return len(self._jobs)
