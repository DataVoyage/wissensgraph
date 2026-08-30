"""Die Zusammenstellung eines lauffähigen Systems — Ports treffen auf Umsetzungen.

Alle vorigen Module halten sich strikt an ihre Schicht: Die Dienste kennen nur Ports, die
Adapter kennen den Graphen nicht, die Domäne kennt niemanden. Irgendwo muss trotzdem entschieden
werden, *welche* Umsetzung ein Port bekommt — und genau das steht hier, an einer Stelle, statt
verstreut in CLI, API und Worker.

Der Nutzen zeigt sich an ``wg sync``, ``POST /runs/sync`` und dem Worker-Prozess: Alle drei sind
Wege zu demselben Lauf, und alle drei stecken ihn hier zusammen. Es gibt deshalb keinen Weg, auf
dem ein Lauf mit anderen Regeln liefe als auf einem anderen (Leitprinzip 14).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self

from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourcesConfig, load_sources
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.infrastructure.adapters import AdapterRegistry, RegisteredSource
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.locks import SqlSourceLocks
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.infrastructure.queue import MemoryJobQueue, RedisJobQueue
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.queue import Job, JobQueue
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.graph import GraphService
from wissensgraph.services.jobs import JobService
from wissensgraph.services.sync import RunNotFound, SyncRequest, SyncService

_log = get_logger(__name__)


class UnknownSourceError(KeyError):
    """Es wurde eine Quelle angefragt, die nicht konfiguriert ist."""

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        self.name = name
        super().__init__(
            f"Unbekannte Quelle '{name}'. Eingeschaltet sind: {', '.join(known) or '(keine)'}. "
            f"Quellen stehen in sources.yaml (§8.4)."
        )


class Runtime:
    """Alles, was ein Lauf braucht, an einer Stelle zusammengesteckt.

    Als Kontextmanager zu benutzen: Beim Verlassen werden Verbindungspools und Broker-Verbindung
    freigegeben. Ein Prozess, der das vergisst, hinterlässt offene PostgreSQL-Sitzungen — und
    damit unter Umständen einen gehaltenen Advisory-Lock (§10.5).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        sources_file: Path | None = None,
        queue: JobQueue | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration.
            sources_file: Abweichender Pfad der ``sources.yaml``; sonst aus ``WG_SOURCES_FILE``.
            queue: Eine abweichende Job-Queue. Ohne Angabe wird sie aus der Konfiguration
                gewählt: Redis, wenn eine ``broker_url`` gesetzt ist, sonst eine Warteschlange im
                Speicher (siehe :meth:`_queue_waehlen`).
        """
        self._settings = settings
        self._sources = load_sources(settings, path=sources_file)
        self._stores = StoreRegistry(settings)
        self._uow = UnitOfWorkFactory(self._stores)
        self._registry = AdapterRegistry()
        self._registered: dict[str, RegisteredSource] | None = None
        self._queue = queue if queue is not None else self._queue_waehlen()

        self.sync = SyncService(
            settings,
            self._uow,
            SqlSourceLocks(self._stores),
            known_prefixes=[item.id_prefix for item in self._sources.sources],
        )
        self.jobs = JobService(self._queue)
        self.concepts = ConceptService(settings, self._uow)
        self.graph = GraphService(settings, self._uow)

    # -- Bestandteile ------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """Die geprüfte Konfiguration."""
        return self._settings

    @property
    def stores(self) -> StoreRegistry:
        """Die Store-Registry — der einzige Weg zu einer Verbindung (§20.1)."""
        return self._stores

    @property
    def sources(self) -> SourcesConfig:
        """Die geladene Quellkonfiguration."""
        return self._sources

    @property
    def registered(self) -> tuple[RegisteredSource, ...]:
        """Die registrierten, eingeschalteten Quellen — einmal gebaut, dann wiederverwendet.

        Das Zwischenspeichern ist nicht nur Sparsamkeit: :meth:`AdapterRegistry.build_all` ruft
        ``health()`` auf und damit die Quelle an. Ein ``wg sync --all`` würde sonst je Quelle
        zweimal anfragen, bevor überhaupt ein Dokument gelesen ist.
        """
        if self._registered is None:
            self._registered = {item.name: item for item in self._registry.build_all(self._sources)}
        return tuple(self._registered.values())

    def source(self, name: str) -> RegisteredSource:
        """Eine registrierte Quelle zu ihrem Namen.

        Raises:
            UnknownSourceError: Wenn es sie nicht gibt oder sie ausgeschaltet ist.
        """
        self.registered  # noqa: B018 — füllt den Zwischenspeicher
        assert self._registered is not None
        try:
            return self._registered[name]
        except KeyError as exc:
            raise UnknownSourceError(name, tuple(self._registered)) from exc

    # -- Läufe -------------------------------------------------------------------

    def run_sync(self, name: str, request: SyncRequest | None = None) -> Run:
        """Führt einen Sync-Lauf über eine Quelle synchron aus — der Weg von ``wg sync`` (§19).

        Raises:
            UnknownSourceError: Wenn die Quelle nicht konfiguriert ist.
            RuntimeError: Wenn die Quelle als ``unhealthy`` gilt (§8.3).
            SourceBusy: Wenn bereits ein Lauf über diese Quelle läuft (§10.5).
        """
        quelle = self.source(name)
        return self.sync.sync(quelle.require(), quelle.config, request)

    def run_sync_all(self, request: SyncRequest | None = None) -> tuple[Run, ...]:
        """Läuft über alle benutzbaren Quellen — ``wg sync --all`` (§19).

        Eine unbenutzbare Quelle wird übersprungen und nicht zum Abbruch: §8.3 verlangt, dass ein
        fehlerhafter Adapter die anderen nicht mitreißt.
        """
        laeufe: list[Run] = []
        for quelle in self.registered:
            if not quelle.usable:
                _log.warning("lauf.uebersprungen", source=quelle.name, detail=quelle.health.detail)
                continue
            laeufe.append(self.sync.sync(quelle.require(), quelle.config, request))
        return tuple(laeufe)

    def submit_sync(self, name: str, request: SyncRequest | None = None) -> Run:
        """Legt einen Lauf an und stellt ihn in die Queue — der Weg aus §16.3.

        Returns:
            Den angelegten Lauf im Zustand ``queued``. Seine ID ist das, was ``202 Accepted`` im
            ``Location``-Header nennt.
        """
        quelle = self.source(name)
        request = request or SyncRequest()
        run = self.sync.prepare(quelle.config, request)
        self.jobs.submit(
            Job(
                run_id=run.id,
                kind=RunKind.SYNC,
                store=self.sync.store_of(quelle.config),
                params=run.params,
            )
        )
        return run

    def handle(self, job: Job) -> Run:
        """Führt einen entnommenen Job aus — die Zuordnung Job → Lauf im ``worker`` (§5.1).

        Raises:
            NotImplementedError: Für Lauf-Arten, die erst spätere Stufen umsetzen. Die Meldung
                nennt die Art; ein stilles Verwerfen wäre schlimmer als ein lauter Fehler,
                denn der Lauf stünde dann für immer auf ``queued``.
            RunNotFound: Wenn der Job auf einen Lauf zeigt, den es nicht gibt.
        """
        if job.kind is not RunKind.SYNC:
            raise NotImplementedError(
                f"Läufe der Art '{job.kind}' sind noch nicht umgesetzt; Stufe 4 deckt 'sync' ab."
            )

        name = str(job.params.get("source", ""))
        quelle = self.source(name)
        return self.sync.sync(
            quelle.require(),
            quelle.config,
            SyncRequest.from_params(job.params),
            run_id=job.run_id,
        )

    def work(self, *, once: bool = False, stop: Callable[[], bool] | None = None) -> int:
        """Die Worker-Schleife (§5.1).

        Args:
            once: Nur einen Job (oder eine Wartezeit) abarbeiten. Für Tests und für einen
                einmaligen Anstoß von Hand.
            stop: Abbruchsignal — ein Objekt, das aufrufbar ``True`` liefert, wenn Schluss ist.

        Returns:
            Die Zahl der bearbeiteten Jobs.
        """
        if once:
            return 1 if self.jobs.work_once(self._handle_quiet) else 0
        return self.jobs.work(self._handle_quiet, stop=stop)

    def _handle_quiet(self, job: Job) -> None:
        """:meth:`handle` ohne Rückgabewert — die Form, die :class:`JobService` erwartet."""
        self.handle(job)

    # -- Lebenszyklus ------------------------------------------------------------

    def _queue_waehlen(self) -> JobQueue:
        """Redis, wenn ein Broker konfiguriert ist; sonst eine Warteschlange im Speicher.

        Ohne diese Wahl bräuchte ``wg sync`` auf einem Entwicklerrechner einen laufenden Redis,
        obwohl es synchron arbeitet und nie einen Job einstellt (§19).
        """
        if self._settings.broker_url:
            return RedisJobQueue(self._settings.broker_url)
        _log.debug("queue.im_speicher")
        return MemoryJobQueue()

    def close(self) -> None:
        """Gibt Verbindungspools und Broker-Verbindung frei."""
        schliessen = getattr(self._queue, "close", None)
        if schliessen is not None:
            schliessen()
        self._stores.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["RunNotFound", "Runtime", "SyncRequest", "UnknownSourceError"]
