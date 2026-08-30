"""Job-Queue und Worker-Schleife (§5.1, §16.3).

Zwei Umsetzungen desselben Ports werden geprüft: die Warteschlange im Speicher, die ``wg sync``
benutzt, und die auf Redis, die der ``worker`` benutzt. Die zweite gegen einen nachgebildeten
Client — was hier zu prüfen ist, ist die Befehlsfolge und die Serialisierung, nicht Redis.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from wissensgraph.domain.runs import RunKind
from wissensgraph.infrastructure.queue import BrokerUnavailable, MemoryJobQueue, RedisJobQueue
from wissensgraph.ports.queue import Job
from wissensgraph.services.jobs import JobService

pytestmark = pytest.mark.unit


def job(**kwargs: Any) -> Job:
    daten: dict[str, Any] = {
        "run_id": uuid4(),
        "kind": RunKind.SYNC,
        "store": "shared",
        "params": {"source": "confluence-eng"},
    }
    daten.update(kwargs)
    return Job.model_validate(daten)


class FakeRedis:
    """Das Wenige an Redis, das die Queue benutzt: ``rpush``, ``blpop``, ``llen``, ``close``."""

    def __init__(self) -> None:
        self.listen: dict[str, list[bytes]] = {}
        self.fristen: list[int] = []
        self.geschlossen = False
        self.timeout_werfen = False

    def rpush(self, key: str, value: str) -> int:
        self.listen.setdefault(key, []).append(value.encode("utf-8"))
        return len(self.listen[key])

    def blpop(self, keys: list[str], timeout: int) -> tuple[bytes, bytes] | None:
        self.fristen.append(timeout)
        if self.timeout_werfen:
            raise RedisTimeoutError("Timeout reading from socket")
        for key in keys:
            eintraege = self.listen.get(key)
            if eintraege:
                return key.encode("utf-8"), eintraege.pop(0)
        return None

    def llen(self, key: str) -> int:
        return len(self.listen.get(key, []))

    def close(self) -> None:
        self.geschlossen = True


class TestMemoryJobQueue:
    def test_fifo(self) -> None:
        queue = MemoryJobQueue()
        erster, zweiter = job(), job()

        queue.enqueue(erster)
        queue.enqueue(zweiter)

        assert queue.size() == 2
        assert queue.reserve(timeout_seconds=1).run_id == erster.run_id  # type: ignore[union-attr]
        assert queue.reserve(timeout_seconds=1).run_id == zweiter.run_id  # type: ignore[union-attr]

    def test_leere_warteschlange_wartet_nicht(self) -> None:
        """In einem Prozess ohne zweiten Erzeuger wäre die Frist reine Verzögerung."""
        assert MemoryJobQueue().reserve(timeout_seconds=30) is None


class TestRedisJobQueue:
    def test_ein_job_geht_als_json_in_die_liste(self) -> None:
        client = FakeRedis()
        queue = RedisJobQueue(None, client=client, key="wg:test")
        auftrag = job()

        queue.enqueue(auftrag)

        assert queue.size() == 1
        assert str(auftrag.run_id).encode() in client.listen["wg:test"][0]

    def test_der_job_kommt_unveraendert_zurueck(self) -> None:
        client = FakeRedis()
        queue = RedisJobQueue(None, client=client)
        auftrag = job()

        queue.enqueue(auftrag)
        zurueck = queue.reserve(timeout_seconds=1)

        assert zurueck == auftrag

    def test_eine_leere_liste_liefert_nichts(self) -> None:
        assert RedisJobQueue(None, client=FakeRedis()).reserve(timeout_seconds=1) is None

    def test_die_frist_wird_auf_ganze_sekunden_aufgerundet(self) -> None:
        """``BLPOP`` deutet 0 als "unbegrenzt" — ein Aufruf mit 0,3 s soll kurz warten."""
        client = FakeRedis()

        RedisJobQueue(None, client=client).reserve(timeout_seconds=0.3)

        assert client.fristen == [1]

    def test_ein_abgelaufenes_blpop_bedeutet_kein_job(self) -> None:
        """redis-py benutzt die Blockierfrist zugleich als Lesefrist des Sockets.

        Beide laufen im selben Augenblick ab; ob die leere Antwort noch rechtzeitig ankommt, ist
        ein Wettlauf. Für ein blockierendes Entnehmen bedeuten beide Ausgänge dasselbe — und ohne
        diesen Fall beendete der Worker sich beim ersten leeren Durchlauf mit einem Traceback.
        """
        client = FakeRedis()
        client.timeout_werfen = True

        assert RedisJobQueue(None, client=client).reserve(timeout_seconds=5) is None

    def test_ohne_broker_url_ist_das_ein_fehler(self) -> None:
        with pytest.raises(BrokerUnavailable, match="WG_BROKER_URL"):
            RedisJobQueue(None)

    def test_close_gibt_die_verbindung_frei(self) -> None:
        client = FakeRedis()

        RedisJobQueue(None, client=client).close()

        assert client.geschlossen is True


class TestJobService:
    def test_work_once_fuehrt_einen_job_aus(self) -> None:
        queue = MemoryJobQueue()
        dienst = JobService(queue)
        auftrag = job()
        dienst.submit(auftrag)
        gesehen: list[Job] = []

        assert dienst.work_once(gesehen.append, timeout_seconds=0.1) is True
        assert gesehen == [auftrag]

    def test_ohne_job_passiert_nichts(self) -> None:
        assert JobService(MemoryJobQueue()).work_once(lambda _: None, timeout_seconds=0.1) is False

    def test_ein_gescheiterter_job_beendet_den_worker_nicht(self) -> None:
        """Sonst sähe ein einziger kaputter Job im Compose wie ein Neustartproblem aus."""
        dienst = JobService(MemoryJobQueue())
        dienst.submit(job())

        def kaputt(_job: Job) -> None:
            raise RuntimeError("etwas ging schief")

        assert dienst.work_once(kaputt, timeout_seconds=0.1) is True

    def test_die_schleife_endet_mit_dem_abbruchsignal(self) -> None:
        dienst = JobService(MemoryJobQueue())
        for _ in range(3):
            dienst.submit(job())
        runden = iter([False, False, False, True])

        erledigt = dienst.work(lambda _: None, stop=lambda: next(runden), timeout_seconds=0.1)

        assert erledigt == 3

    def test_pending_meldet_die_wartenden(self) -> None:
        dienst = JobService(MemoryJobQueue())
        dienst.submit(job())

        assert dienst.pending == 1
