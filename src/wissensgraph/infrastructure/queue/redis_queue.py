"""Job-Queue auf Redis (§5.1, §16.3).

Eine Redis-Liste, zwei Befehle: ``RPUSH`` zum Einstellen, ``BLPOP`` zum Entnehmen. Der Umfang ist
Absicht — was hier gebraucht wird, ist eine Warteschlange und kein Task-Framework.

Der Job trägt nur einen Verweis auf den Lauf, nie Nutzlast (§16.3). Das macht die einzige
Schwäche eines ``BLPOP`` erträglich: Es ist ein *at-most-once*-Entnehmen. Stürzt der Worker
zwischen dem Entnehmen und dem Abschluss ab, ist der Job weg — der Lauf aber steht weiterhin als
``queued`` oder ``running`` in ``runs`` und ist sichtbar. Nichts geht verloren außer der
Anstoß, und den kann ein Mensch oder ein späterer Zeitplan wiederholen. Die Alternative wäre eine
zweite Liste als Zwischenablage samt Aufräumlauf für verwaiste Einträge — Zustandshaltung, die
die Datenbank bereits leistet.
"""

from __future__ import annotations

import math
from typing import Any

from redis.exceptions import TimeoutError as RedisTimeoutError

from wissensgraph.config import defaults
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.queue import Job

_log = get_logger(__name__)


class BrokerUnavailable(RuntimeError):
    """Der Broker ist nicht erreichbar oder nicht konfiguriert."""


class RedisJobQueue:
    """Eine Warteschlange auf einer Redis-Liste; erfüllt den Port :class:`JobQueue`."""

    def __init__(
        self, url: str | None, *, key: str = defaults.QUEUE_KEY, client: Any | None = None
    ) -> None:
        """
        Args:
            url: Die Broker-URL aus ``WG_BROKER_URL``. ``None`` ist ein Fehler — wer diese
                Umsetzung wählt, will einen Broker.
            key: Der Schlüssel der Liste. Aus den Defaults, damit zwei Installationen auf
                derselben Redis-Instanz sich nicht in die Quere kommen (§6.1 Regel 1).
            client: Ein fertiger Redis-Client. Als Parameter, damit ein Test die Befehlsfolge
                prüfen kann, ohne einen Broker zu starten.

        Raises:
            BrokerUnavailable: Wenn weder URL noch Client vorliegen.
        """
        if client is None:
            if not url:
                raise BrokerUnavailable(
                    "Für die Job-Queue ist keine Broker-URL konfiguriert. Sie kommt aus "
                    "WG_BROKER_URL und zeigt im Container auf 'redis://broker:6379/0' (§5.1)."
                )
            from redis import Redis

            client = Redis.from_url(url)
        self._client = client
        self._key = key

    def enqueue(self, job: Job) -> None:
        """Stellt einen Job ans Ende der Liste."""
        self._client.rpush(self._key, job.model_dump_json())
        _log.info("job.eingestellt", run_id=str(job.run_id), kind=str(job.kind))

    def reserve(self, *, timeout_seconds: float) -> Job | None:
        """Wartet bis zur Frist auf den nächsten Job.

        ``BLPOP`` nimmt die Frist in ganzen Sekunden und deutet 0 als "unbegrenzt". Deshalb wird
        aufgerundet und nach unten auf 1 begrenzt: Ein Aufruf mit 0,3 s soll kurz warten und
        nicht für immer.
        """
        frist = max(1, math.ceil(timeout_seconds))
        try:
            antwort = self._client.blpop([self._key], timeout=frist)
        except RedisTimeoutError:
            # redis-py benutzt die Blockierfrist von BLPOP zugleich als Lesefrist des Sockets.
            # Beide laufen damit im selben Augenblick ab, und ob die leere Antwort des Servers
            # noch rechtzeitig ankommt, ist ein Wettlauf: Mal kommt ``None`` zurück, mal fliegt
            # eine TimeoutError. Für ein blockierendes Entnehmen bedeuten beide dasselbe —
            # in dieser Runde kam kein Job. Ein echtes Verbindungsproblem meldet sich dagegen
            # als ConnectionError und wird nicht abgefangen.
            return None
        if antwort is None:
            return None
        _, nutzlast = antwort
        return Job.model_validate_json(nutzlast)

    def size(self) -> int:
        """Wie viele Jobs warten."""
        return int(self._client.llen(self._key))

    def close(self) -> None:
        """Gibt die Verbindung frei. Beim Herunterfahren eines Prozesses aufzurufen."""
        schliessen = getattr(self._client, "close", None)
        if schliessen is not None:
            schliessen()
