"""Job-Vermittlung und Worker-Schleife (§5.1, §16.3, §23).

§16.3 trennt das Anstoßen vom Ausführen: Die API legt einen Lauf an, stellt einen Job ein und
antwortet mit ``202 Accepted``; der ``worker`` führt aus. Dieser Dienst ist die Mitte zwischen
beidem, und er ist bewusst dünn — er kennt weder Quellen noch den Graphen. Was ein Job bedeutet,
entscheidet der ``handler``, den der Aufrufer mitgibt.

Die Schleife hält zwei Zusicherungen, die im Betrieb mehr wert sind als jede Zusatzfunktion:

* **Ein Fehler beendet den Worker nicht.** Ein Job, der scheitert, wird geloggt und gezählt; der
  nächste läuft. Ein Worker, der am ersten kaputten Job stirbt, sieht im Compose aus wie ein
  Neustartproblem, und der eigentliche Grund steht drei Neustarts weiter oben.
* **Der Worker lässt sich beenden.** :meth:`JobService.work` prüft zwischen zwei Wartezeiten ein
  Abbruchsignal. Deshalb ist die Frist beim Entnehmen Pflicht (§ports/queue).
"""

from __future__ import annotations

from collections.abc import Callable

from wissensgraph.config import defaults
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.queue import Job, JobQueue

_log = get_logger(__name__)

#: Was mit einem entnommenen Job zu tun ist. Der Rückgabewert ist bewusst ``None``: Das Ergebnis
#: eines Laufs steht in ``runs``, nicht in der Queue (§16.3).
JobHandler = Callable[[Job], None]


class JobService:
    """Stellt Jobs ein und arbeitet sie ab."""

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    @property
    def pending(self) -> int:
        """Wie viele Jobs warten (§21.2)."""
        return self._queue.size()

    def submit(self, job: Job) -> None:
        """Stellt einen Job ein.

        Der zugehörige Lauf muss bereits in ``runs`` stehen (§16.3). Diese Reihenfolge ist die
        eigentliche Zusicherung: Ein Job zeigt nie auf einen Lauf, den es nicht gibt.
        """
        self._queue.enqueue(job)

    def work_once(
        self,
        handler: JobHandler,
        *,
        timeout_seconds: float = defaults.QUEUE_RESERVE_TIMEOUT_SECONDS,
    ) -> bool:
        """Nimmt höchstens einen Job entgegen und führt ihn aus.

        Returns:
            Ob ein Job bearbeitet wurde — auch dann ``True``, wenn er gescheitert ist. Die Frage
            ist "war Arbeit da?", nicht "ging sie gut aus?"; die zweite beantwortet ``runs``.
        """
        job = self._queue.reserve(timeout_seconds=timeout_seconds)
        if job is None:
            return False

        _log.info("job.entnommen", run_id=str(job.run_id), kind=str(job.kind), store=job.store)
        try:
            handler(job)
        # Ein kaputter Job darf den Worker nicht beenden (siehe Modulkopf).
        except Exception as exc:
            _log.error(
                "job.gescheitert",
                run_id=str(job.run_id),
                kind=str(job.kind),
                error=f"{type(exc).__name__}: {exc}",
            )
        return True

    def work(
        self,
        handler: JobHandler,
        *,
        stop: Callable[[], bool] | None = None,
        timeout_seconds: float = defaults.QUEUE_RESERVE_TIMEOUT_SECONDS,
    ) -> int:
        """Arbeitet Jobs ab, bis das Abbruchsignal kommt — die Schleife des ``worker`` (§5.1).

        Args:
            handler: Was mit einem Job zu tun ist.
            stop: Wird zwischen zwei Wartezeiten gefragt. Ohne Angabe läuft die Schleife, bis der
                Prozess unterbrochen wird.
            timeout_seconds: Wie lange je Runde auf einen Job gewartet wird.

        Returns:
            Die Zahl der bearbeiteten Jobs.
        """
        anhalten = stop or (lambda: False)
        erledigt = 0
        _log.info("worker.gestartet", timeout_seconds=timeout_seconds)
        while not anhalten():
            if self.work_once(handler, timeout_seconds=timeout_seconds):
                erledigt += 1
        _log.info("worker.beendet", jobs=erledigt)
        return erledigt
