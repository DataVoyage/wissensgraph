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
from uuid import UUID

from wissensgraph.config.models import ModelsConfig, load_models
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourcesConfig, load_sources
from wissensgraph.domain.runs import Run, RunKind
from wissensgraph.infrastructure.adapters import AdapterRegistry, RegisteredSource
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.locks import SqlSourceLocks
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.infrastructure.models import (
    LangChainClients,
    MemoryResponseCache,
    RedisResponseCache,
)
from wissensgraph.infrastructure.queue import MemoryJobQueue, RedisJobQueue
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import ModelClientFactory, ResponseCache
from wissensgraph.ports.queue import Job, JobQueue
from wissensgraph.ports.repositories import UnitOfWorkFactory as PortUnitOfWorkFactory
from wissensgraph.services.catalog import CatalogService
from wissensgraph.services.clustering import ClusterService
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.curation import CurationService
from wissensgraph.services.embeddings import EmbeddingService
from wissensgraph.services.graph import GraphService
from wissensgraph.services.jobs import JobService
from wissensgraph.services.orphans import OrphanRequest, OrphanService
from wissensgraph.services.relations import RelationService
from wissensgraph.services.router import ModelRouterService
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
        models_file: Path | None = None,
        queue: JobQueue | None = None,
        clients: ModelClientFactory | None = None,
        unit_of_work: PortUnitOfWorkFactory | None = None,
        accounting_store: str | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration.
            sources_file: Abweichender Pfad der ``sources.yaml``; sonst aus ``WG_SOURCES_FILE``.
            models_file: Abweichender Pfad der ``models.yaml``; sonst aus ``WG_MODELS_FILE``.
            queue: Eine abweichende Job-Queue. Ohne Angabe wird sie aus der Konfiguration
                gewählt: Redis, wenn eine ``broker_url`` gesetzt ist, sonst eine Warteschlange im
                Speicher (siehe :meth:`_queue_waehlen`).
            clients: Eine abweichende Fabrik für Modell-Clients. Ohne Angabe die
                LangChain-Fabrik. Der Parameter ist der Weg, einen ganzen Lauf gegen den
                Fake-Provider aus :mod:`wissensgraph.testing.models` zu fahren — ohne Netz, ohne
                Schlüssel und ohne einen einzigen Token.
            unit_of_work: Eine abweichende Fabrik für Arbeitseinheiten. Ohne Angabe die über die
                Store-Registry. Zusammen mit ``clients`` ist das der Weg, die ganze Laufzeit —
                und damit auch die HTTP-API — ohne Datenbank zu betreiben.
            accounting_store: Der Store, in dem ``model_calls`` landen, wenn der betroffene selbst
                nicht beschreibbar ist. Nur der MCP-Server setzt ihn (§18.3); siehe
                :class:`~wissensgraph.services.router.ModelRouterService`.
        """
        self._settings = settings
        self._sources = load_sources(settings, path=sources_file)
        self._models = load_models(settings, path=models_file)
        self._stores = StoreRegistry(settings)
        self._uow: PortUnitOfWorkFactory = (
            unit_of_work if unit_of_work is not None else UnitOfWorkFactory(self._stores)
        )
        self._registry = AdapterRegistry()
        self._registered: dict[str, RegisteredSource] | None = None
        self._queue = queue if queue is not None else self._queue_waehlen()
        self._cache = self._cache_waehlen()

        self.router = ModelRouterService(
            settings,
            self._models,
            clients if clients is not None else LangChainClients(self._models),
            unit_of_work=self._uow,
            accounting_store=accounting_store,
            cache=self._cache,
        )
        self.sync = SyncService(
            settings,
            self._uow,
            SqlSourceLocks(self._stores),
            known_prefixes=[item.id_prefix for item in self._sources.sources],
        )
        self.jobs = JobService(self._queue)
        self.concepts = ConceptService(settings, self._uow)
        self.graph = GraphService(settings, self._uow, router=self.router)
        self.embeddings = EmbeddingService(settings, self._uow, self.router)
        self.clusters = ClusterService(settings, self._uow, self.router)
        self.relations = RelationService(settings, self._uow, self.router)
        self.orphans = OrphanService(settings, self._uow, self.router, relations=self.relations)
        self.catalog = CatalogService(settings, self._uow, router=self.router)
        self.curation = CurationService(settings, self._uow)

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
    def models(self) -> ModelsConfig:
        """Die geladene Router-Konfiguration."""
        return self._models

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

    # -- Läufe der semantischen Schicht (§13 bis §15) ----------------------------

    def run_embed(self, scope: str, *, rebuild: bool = False, run_id: UUID | None = None) -> Run:
        """Führt einen Embedding-Lauf aus und verbucht ihn (§13.1)."""
        return self._verbuchter_lauf(
            kind=RunKind.EMBED,
            scope=scope,
            params={"scope": scope, "rebuild": rebuild},
            arbeit=lambda lauf: self.embeddings.run(
                scope=scope, rebuild=rebuild, run_id=lauf
            ).as_dict(),
            run_id=run_id,
        )

    def run_cluster(self, scope: str, *, run_id: UUID | None = None) -> Run:
        """Führt einen Clustering-Lauf aus und verbucht ihn (§13.2)."""
        return self._verbuchter_lauf(
            kind=RunKind.CLUSTER,
            scope=scope,
            params={"scope": scope},
            arbeit=lambda lauf: self.clusters.run(scope=scope, run_id=lauf).as_dict(),
            run_id=run_id,
        )

    def run_relations(
        self, scope: str, *, dry_run: bool = False, run_id: UUID | None = None
    ) -> Run:
        """Führt einen Lauf der Kantenerkennung aus und verbucht ihn (§14)."""
        return self._verbuchter_lauf(
            kind=RunKind.RELATIONS,
            scope=scope,
            params={"scope": scope, "dry_run": dry_run},
            arbeit=lambda lauf: self.relations.run(
                scope=scope, run_id=lauf, dry_run=dry_run
            ).as_dict(),
            fluechtig=dry_run,
            run_id=run_id,
        )

    def run_orphans(self, request: OrphanRequest, *, run_id: UUID | None = None) -> Run:
        """Führt einen Vernetzungslauf über lose Knoten aus und verbucht ihn (§15)."""
        return self._verbuchter_lauf(
            kind=RunKind.LINK_ORPHANS,
            scope=request.scope,
            params={"scope": request.scope, "dry_run": request.dry_run},
            arbeit=lambda lauf: self.orphans.run(request, run_id=lauf).as_dict(),
            fluechtig=request.dry_run,
            run_id=run_id,
        )

    def _verbuchter_lauf(
        self,
        *,
        kind: RunKind,
        scope: str,
        params: dict[str, object],
        arbeit: Callable[[UUID], dict[str, object]],
        fluechtig: bool = False,
        run_id: UUID | None = None,
    ) -> Run:
        """Legt einen Lauf an, führt ihn aus und schreibt Zustand und Statistik fort (§7.4).

        ``fluechtig`` ist die Trockenlauf-Variante: Der Lauf bekommt eine ID und einen Bericht,
        aber keine Zeile in ``runs``. Ein ``--dry-run`` verspricht, nichts zu verändern, und eine
        Zeile wäre eine Veränderung — dieselbe Entscheidung wie beim Sync (§19).

        ``run_id`` ist der Weg des Workers: Über die API wurde der Lauf schon angelegt und hat der
        Oberfläche seine ID genannt (§16.3). Würde hier eine zweite Zeile entstehen, verfolgte die
        Oberfläche einen Lauf, der nie startet, während ein zweiter unbemerkt arbeitet.
        """
        from datetime import UTC, datetime

        from wissensgraph.domain.runs import RunStatus, new_run_id

        store = self._settings.store_of_scope(scope)
        jetzt = datetime.now(UTC)
        if run_id is not None:
            with self._uow(store) as uow:
                vorhanden = uow.runs.get(run_id)
                if vorhanden is None:
                    raise RunNotFound(run_id, store)
                if vorhanden.status is RunStatus.CANCELLED:
                    _log.info("lauf.abgebrochen_vor_start", run_id=str(run_id))
                    return vorhanden
                run = vorhanden.gestartet(jetzt)
                uow.runs.update(run)
        else:
            run = Run(id=new_run_id(), kind=kind, params=dict(params)).gestartet(jetzt)
            if not fluechtig:
                with self._uow(store) as uow:
                    uow.runs.create(run)

        try:
            stats = arbeit(run.id)
        except Exception as exc:
            beendet = run.beendet(
                status=RunStatus.FAILED,
                now=datetime.now(UTC),
                error=f"{type(exc).__name__}: {exc}",
            )
            if not fluechtig:
                with self._uow(store) as uow:
                    uow.runs.update(beendet)
            _log.warning("lauf.gescheitert", run_id=str(run.id), error=beendet.error)
            return beendet

        beendet = run.beendet(status=RunStatus.SUCCEEDED, now=datetime.now(UTC), stats=stats)
        if not fluechtig:
            with self._uow(store) as uow:
                uow.runs.update(beendet)
        return beendet

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

    def submit(self, kind: RunKind, *, scope: str, params: dict[str, object]) -> Run:
        """Legt einen Lauf der semantischen Schicht an und stellt ihn in die Queue (§16.3).

        Der Lauf entsteht *vor* dem Job und nicht im Worker. Das ist der Grund, warum die API mit
        ``202 Accepted`` und einer ID antworten kann: Der Zustand liegt in ``runs`` und nicht in
        der Warteschlange, und die Oberfläche kann ihn abonnieren, bevor der Worker ihn überhaupt
        entnommen hat.
        """
        from wissensgraph.domain.runs import RunStatus, new_run_id

        store = self._settings.store_of_scope(scope)
        run = Run(id=new_run_id(), kind=kind, params=dict(params), status=RunStatus.QUEUED)
        with self._uow(store) as uow:
            uow.runs.create(run)
        self.jobs.submit(Job(run_id=run.id, kind=kind, store=store, params=run.params))
        _log.info("lauf.eingestellt", run_id=str(run.id), kind=str(kind), scope=scope)
        return run

    def cancel(self, run_id: UUID) -> Run | None:
        """Bricht einen Lauf ab, sofern er noch nicht endgültig ist (§16.2).

        Abgebrochen wird ein *wartender* Lauf zuverlässig: Er ist noch niemandes Arbeit, und der
        Worker findet beim Entnehmen einen Lauf vor, der nicht mehr laufen soll. Ein bereits
        laufender wird als abgebrochen markiert; ihn mitten in einer Transaktion zu unterbrechen
        wäre der schlechtere Tausch — der Lauf schreibt ohnehin nur vollständige Einheiten
        (§10.2 Regel 5).

        Returns:
            Den abgebrochenen Lauf, oder ``None``, wenn es ihn nicht gibt oder er schon endete.
        """
        from datetime import UTC, datetime

        from wissensgraph.domain.runs import RunStatus

        for store in self._settings.stores:
            with self._uow(store) as uow:
                lauf = uow.runs.get(run_id)
                if lauf is None:
                    continue
                if lauf.is_final:
                    return None
                beendet = lauf.beendet(status=RunStatus.CANCELLED, now=datetime.now(UTC))
                uow.runs.update(beendet)
                return beendet
        return None

    def handle(self, job: Job) -> Run:
        """Führt einen entnommenen Job aus — die Zuordnung Job → Lauf im ``worker`` (§5.1).

        Raises:
            NotImplementedError: Für Lauf-Arten, die erst spätere Stufen umsetzen. Die Meldung
                nennt die Art; ein stilles Verwerfen wäre schlimmer als ein lauter Fehler,
                denn der Lauf stünde dann für immer auf ``queued``.
            RunNotFound: Wenn der Job auf einen Lauf zeigt, den es nicht gibt.
        """
        if job.kind is RunKind.EMBED:
            return self.run_embed(
                str(job.params["scope"]),
                rebuild=bool(job.params.get("rebuild", False)),
                run_id=job.run_id,
            )
        if job.kind is RunKind.CLUSTER:
            return self.run_cluster(str(job.params["scope"]), run_id=job.run_id)
        if job.kind is RunKind.RELATIONS:
            return self.run_relations(
                str(job.params["scope"]),
                dry_run=bool(job.params.get("dry_run", False)),
                run_id=job.run_id,
            )
        if job.kind is RunKind.LINK_ORPHANS:
            return self.run_orphans(
                OrphanRequest.from_params(job.params),
                run_id=job.run_id,
            )
        if job.kind is not RunKind.SYNC:
            raise NotImplementedError(
                f"Läufe der Art '{job.kind}' sind noch nicht umgesetzt. Umgesetzt sind: sync, "
                f"embed, cluster, relations, link_orphans."
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

    def _cache_waehlen(self) -> ResponseCache:
        """Redis, wenn ein Broker konfiguriert ist; sonst ein Zwischenspeicher im Prozess.

        Der Unterschied ist die Lebensdauer, nicht die Wirkung: Auch der prozesslokale Cache
        verhindert, dass ein einzelner Lauf denselben Text zweimal einbettet. Über Läufe hinweg
        wirkt nur der in Redis — dort ist die Ersparnis nach §14.5 am größten ("Wiederholungsläufe
        kosten fast nichts").
        """
        if self._settings.broker_url:
            return RedisResponseCache(self._settings.broker_url)
        return MemoryResponseCache()

    def close(self) -> None:
        """Gibt Verbindungspools, Broker- und Cache-Verbindung frei."""
        for teil in (self._queue, self._cache):
            schliessen = getattr(teil, "close", None)
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


__all__ = ["OrphanRequest", "RunNotFound", "Runtime", "SyncRequest", "UnknownSourceError"]
