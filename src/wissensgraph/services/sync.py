"""Die Sync-Orchestrierung (§10.1, §10.5, §21.3).

§10.1 zeichnet den Ablauf eines Laufs als Flussdiagramm. Dieser Dienst ist genau dieses Diagramm,
in derselben Reihenfolge:

``run anlegen`` → ``Adapter, Cursor laden`` → ``iter_documents`` → ``Mapping, Upsert, Kanten`` →
``list_deleted → Tombstones`` → ``next_cursor speichern`` → ``run abschließen, stats schreiben``.

Vier Entscheidungen tragen dabei mehr Gewicht, als ihre Codemenge vermuten lässt:

**Die Reihenfolge am Ende ist keine Geschmacksfrage.** Der Cursor wird *nach* dem vollständigen
Durchlauf gespeichert und nur dann. §21.3 sagt für eine nicht erreichbare Quelle: "Lauf endet mit
``failed``, Cursor bleibt unverändert, Wiederholung ist gefahrlos." Ein Cursor, der schon
unterwegs fortgeschrieben würde, ließe den Rest des Bestands stillschweigend verschwinden.

**Die Sperre umschließt alles.** Sie wird vor dem Lauf genommen und nach dem Abschluss des Laufs
freigegeben — einschließlich des Speicherns von Cursor und Statistik. Läge sie enger, könnte ein
zweiter Lauf zwischen dem letzten Dokument und dem Cursor starten und mit dem *alten* Cursor
loslaufen.

**Ein Trockenlauf tut alles und verwirft es.** ``--dry-run`` täuscht nichts vor: Der Lauf öffnet
eine Transaktion, führt jedes Dokument wirklich durch die Kernoperation und rollt am Ende zurück.
Eine Vorschau, die den Schreibpfad umgeht, sagt genau über den Schreibpfad nichts aus — und das
ist die Frage, die ein Trockenlauf beantworten soll.

**Löschung setzt Grabsteine und rührt keine Kante an.** §7.6 begründet das: Persönliche Notizen,
die auf ein gelöschtes Objekt verweisen, sollen nachvollziehbar bleiben.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourceConfig
from wissensgraph.domain.runs import Run, RunKind, RunStatus, new_run_id
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.repositories import UnitOfWork, UnitOfWorkFactory
from wissensgraph.ports.runs import SourceBusy, SourceLocks
from wissensgraph.ports.sources import Cursor, SourceAdapter, SourceError
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.sources import IngestReport, SourceIngestService, SourceMapper

_log = get_logger(__name__)


class RunNotFound(LookupError):
    """Zu einer Lauf-ID gibt es in diesem Store keinen Eintrag."""

    def __init__(self, run_id: UUID, store: str) -> None:
        self.run_id = run_id
        super().__init__(f"Kein Lauf mit der ID {run_id} im Store '{store}'.")


@dataclass(frozen=True)
class SyncRequest:
    """Was ein Sync-Lauf über die Quelle hinaus braucht (§19).

    Als eigenes Objekt und nicht als vier Parameter, weil dieselbe Anfrage über drei Wege kommt:
    aus der CLI, aus der API (``POST /runs/sync``) und aus einem Job der Queue. Sie soll auf allen
    dreien dasselbe bedeuten.
    """

    full: bool = False
    """Vollabgleich: Der gespeicherte Cursor wird ignoriert (§19: ``--full``)."""

    dry_run: bool = False
    """Alles ausführen und am Ende verwerfen (§19: ``--dry-run``)."""

    actor: str = defaults.ACTOR_SYNC

    def as_params(self, source: str) -> dict[str, Any]:
        """Die Parameter, wie sie in ``runs.params`` stehen (§7.4)."""
        return {
            defaults.RUN_PARAM_SOURCE: source,
            defaults.RUN_PARAM_FULL: self.full,
            defaults.RUN_PARAM_DRY_RUN: self.dry_run,
        }

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> SyncRequest:
        """Die Anfrage zurück aus den Parametern eines angelegten Laufs — der Weg des Workers."""
        return cls(
            full=bool(params.get(defaults.RUN_PARAM_FULL, False)),
            dry_run=bool(params.get(defaults.RUN_PARAM_DRY_RUN, False)),
        )


class SyncService:
    """Führt Sync-Läufe aus und verbucht sie (§10.1)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        locks: SourceLocks,
        *,
        known_prefixes: Iterable[str] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration; liefert die Zuordnung Scope → Store.
            unit_of_work: Fabrik für Transaktionen je Store.
            locks: Die gegenseitige Ausschließung je Quelle (§10.5).
            known_prefixes: Die ``id_prefix``-Werte aller Quellen — Grundlage der
                quellübergreifenden Referenzauflösung (§8.5).
            clock: Zeitquelle; als Parameter, damit ein Test Dauer und Zeitstempel bestimmen kann.
        """
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._locks = locks
        self._known = tuple(known_prefixes)
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- Lesen ------------------------------------------------------------------

    def store_of(self, cfg: SourceConfig) -> str:
        """Der Store, in den eine Quelle schreibt und in dem ihre Läufe verbucht werden.

        Er folgt aus dem Scope, nie aus einem Feld der Quelle (§20.1, §8.4).
        """
        return self._settings.store_of_scope(cfg.target.scope)

    def get_run(self, run_id: UUID, *, store: str) -> Run:
        """Ein Lauf zu seiner ID.

        Raises:
            RunNotFound: Wenn es ihn in diesem Store nicht gibt.
        """
        with self._unit_of_work(store) as uow:
            run = uow.runs.get(run_id)
        if run is None:
            raise RunNotFound(run_id, store)
        return run

    def recent_runs(
        self, *, store: str, kind: RunKind | None = None, limit: int = defaults.RUNS_LIST_LIMIT
    ) -> tuple[Run, ...]:
        """Die zuletzt begonnenen Läufe eines Stores, neueste zuerst."""
        with self._unit_of_work(store) as uow:
            return uow.runs.recent(kind=kind, limit=limit)

    def cursor_of(self, source: str, *, store: str) -> Cursor:
        """Der gespeicherte Stand einer Quelle; leer, wenn sie noch nie gelaufen ist."""
        with self._unit_of_work(store) as uow:
            stand = uow.cursors.get(source)
        return Cursor() if stand is None else stand.cursor

    def forget_cursor(self, source: str, *, store: str) -> bool:
        """Vergisst den Stand einer Quelle; der nächste Lauf ist ein Vollabgleich."""
        with self._unit_of_work(store) as uow:
            return uow.cursors.delete(source)

    # -- Anlegen und Ausführen ---------------------------------------------------

    def prepare(self, cfg: SourceConfig, request: SyncRequest) -> Run:
        """Legt einen Lauf im Zustand ``queued`` an, ohne ihn auszuführen (§16.3).

        Der Weg für die Job-Queue: Erst steht der Lauf in der Datenbank, dann geht der Job los.
        Die umgekehrte Reihenfolge ließe einen Job auf einen Lauf zeigen, den es noch nicht gibt.
        """
        run = Run(
            id=new_run_id(),
            kind=RunKind.SYNC,
            params=request.as_params(cfg.name),
            status=RunStatus.QUEUED,
        )
        store = self.store_of(cfg)
        with self._unit_of_work(store) as uow:
            uow.runs.create(run)
        _log.info("lauf.angelegt", run_id=str(run.id), kind=str(run.kind), source=cfg.name)
        return run

    def sync(
        self,
        adapter: SourceAdapter,
        cfg: SourceConfig,
        request: SyncRequest | None = None,
        *,
        run_id: UUID | None = None,
    ) -> Run:
        """Führt einen Sync-Lauf über eine Quelle aus — der Ablauf aus §10.1.

        Args:
            adapter: Der konfigurierte, benutzbare Adapter der Quelle.
            cfg: Die Konfiguration derselben Quelle.
            request: Vollabgleich, Trockenlauf, Akteur.
            run_id: Ein bereits angelegter Lauf, der jetzt ausgeführt wird — der Weg des Workers.
                Ohne Angabe wird ein Lauf angelegt.

        Returns:
            Den abgeschlossenen Lauf mit Zustand, Statistik und gegebenenfalls Fehler. Ein
            gescheiterter Lauf ist ein *Rückgabewert*, keine Ausnahme: Er ist ein Ergebnis, das
            angezeigt, verglichen und wiederholt werden will.

        Raises:
            SourceBusy: Wenn für diese Quelle bereits ein Lauf läuft (§10.5).
            RunNotFound: Wenn ``run_id`` in diesem Store unbekannt ist.
        """
        request = request or SyncRequest()
        store = self.store_of(cfg)

        with self._sperre(cfg.name, store=store):
            run = self._lauf_beginnen(cfg, request, run_id=run_id, store=store)
            return self._ausfuehren(adapter=adapter, cfg=cfg, request=request, run=run, store=store)

    # -- innere Abläufe ----------------------------------------------------------

    @contextmanager
    def _sperre(self, source: str, *, store: str) -> Iterator[None]:
        """Hält die Sperre der Quelle und reichert eine Abweisung um die laufende ID an (§10.5)."""
        try:
            with self._locks.hold(store=store, name=source):
                yield
        except SourceBusy as exc:
            if exc.run_id is not None:
                raise
            with self._unit_of_work(store) as uow:
                laufend = uow.runs.active_for_source(source)
            raise SourceBusy(source, None if laufend is None else laufend.id) from exc

    def _lauf_beginnen(
        self, cfg: SourceConfig, request: SyncRequest, *, run_id: UUID | None, store: str
    ) -> Run:
        """Legt den Lauf an oder übernimmt einen vorbereiteten und setzt ihn auf ``running``.

        Bei einem Trockenlauf entsteht der Lauf nur im Speicher: ``--dry-run`` verspricht, nichts
        zu verändern, und eine Zeile in ``runs`` wäre eine Veränderung. Der Bericht ist derselbe,
        nur nirgends abgelegt.
        """
        jetzt = self._clock()

        if request.dry_run:
            run = Run(id=new_run_id(), kind=RunKind.SYNC, params=request.as_params(cfg.name))
            return run.gestartet(jetzt)

        with self._unit_of_work(store) as uow:
            if run_id is None:
                run = Run(
                    id=new_run_id(), kind=RunKind.SYNC, params=request.as_params(cfg.name)
                ).gestartet(jetzt)
                uow.runs.create(run)
            else:
                vorbereitet = uow.runs.get(run_id)
                if vorbereitet is None:
                    raise RunNotFound(run_id, store)
                run = vorbereitet.gestartet(jetzt)
                uow.runs.update(run)

        _log.info(
            "lauf.gestartet",
            run_id=str(run.id),
            source=cfg.name,
            store=store,
            full=request.full,
        )
        return run

    def _ausfuehren(
        self,
        *,
        adapter: SourceAdapter,
        cfg: SourceConfig,
        request: SyncRequest,
        run: Run,
        store: str,
    ) -> Run:
        """Der eigentliche Durchlauf, mit oder ohne Rückrollen am Ende."""
        cursor = None if request.full else self._cursor_laden(cfg.name, store=store)

        with self._arbeitsraum(store, dry_run=request.dry_run) as fabrik:
            concepts = ConceptService(self._settings, fabrik, clock=self._clock)
            ingest = SourceIngestService(concepts, known_prefixes=self._known)
            try:
                bericht = ingest.ingest(
                    adapter,
                    cfg,
                    cursor=cursor,
                    actor=request.actor,
                    run_id=run.id,
                    # Im Trockenlauf gibt es keine Zeile in ``runs``, die einen Zwischenstand
                    # aufnehmen könnte — und schreiben soll er ohnehin nichts.
                    on_progress=(
                        None
                        if request.dry_run
                        else (lambda zahlen: self._zwischenstand(run, zahlen, store=store))
                    ),
                )
                geloescht = self._loeschungen(
                    adapter=adapter,
                    cfg=cfg,
                    concepts=concepts,
                    cursor=cursor,
                    request=request,
                    run=run,
                    store=store,
                )
            except SourceError as exc:
                return self._lauf_scheitern(run, exc, store=store, dry_run=request.dry_run)

        stats = {**bericht.as_dict(), "deleted": geloescht, "dry_run": request.dry_run}

        # Erst nach dem vollständigen Durchlauf, und nie im Trockenlauf (§22.3).
        if not request.dry_run:
            stats["bridges_resolved"] = self._bruecken_aufloesen(store)
            self._cursor_speichern(cfg.name, bericht.cursor, request=request, store=store)

        return self._lauf_abschliessen(run, stats, store=store, dry_run=request.dry_run)

    @contextmanager
    def _arbeitsraum(self, store: str, *, dry_run: bool) -> Iterator[UnitOfWorkFactory]:
        """Die Arbeitseinheiten-Fabrik dieses Laufs.

        Im Regelfall die echte: Jedes Dokument bekommt seine eigene Transaktion, damit ein Abbruch
        das bereits Verarbeitete stehen lässt (§21.3, "Wiederholung ist gefahrlos").

        Im Trockenlauf eine einzige, offen gehaltene Transaktion, die am Ende zurückgerollt wird.
        Der Unterschied ist genau der, den ``--dry-run`` ausmachen soll — und er steht an einer
        Stelle statt als ``if dry_run`` in jedem Schreibpfad.
        """
        if not dry_run:
            yield self._unit_of_work
            return

        einheit = self._unit_of_work(store)
        with einheit:
            fabrik = _Probelauf(einheit, store, self._unit_of_work)
            try:
                yield fabrik
            finally:
                einheit.rollback()
                _log.info("lauf.trocken.verworfen", store=store)

    def _loeschungen(
        self,
        *,
        adapter: SourceAdapter,
        cfg: SourceConfig,
        concepts: ConceptService,
        cursor: Cursor | None,
        request: SyncRequest,
        run: Run,
        store: str,
    ) -> int:
        """Setzt Grabsteine für die von der Quelle gemeldeten Löschungen (§10.1, §7.6).

        Die Fähigkeit wird am Flag abgelesen und nicht an einer Ausnahme erprobt — §8.2 Regel 3:
        "Der ``SyncService`` fragt Flags ab, nicht Ausnahmen."
        """
        if not adapter.capabilities.deletions:
            return 0

        mapper = SourceMapper(cfg, known_prefixes=self._known)
        anzahl = 0
        for external_id in adapter.list_deleted(cursor):
            if concepts.mark_source_deleted(
                mapper.concept_id(external_id),
                store=store,
                actor=request.actor,
                run_id=run.id,
            ):
                anzahl += 1
        if anzahl:
            _log.info("lauf.loeschungen", run_id=str(run.id), source=cfg.name, count=anzahl)
        return anzahl

    def _bruecken_aufloesen(self, store: str) -> int:
        """Prüft die Brücken, die auf den soeben veränderten Store zeigen (§12.1).

        Ein Sync speist immer nur einen Store. Die Kanten, die dadurch auflösbar werden oder es
        aufhören zu sein, liegen aber womöglich im anderen: Eine persönliche Notiz zeigt auf eine
        Confluence-Seite, die es jetzt erst gibt — oder die gerade zum Grabstein wurde. Ohne
        diesen Schritt bliebe sie bis zum nächsten Schreibvorgang *in ihrem eigenen* Store falsch
        beschriftet, und den kann es lange nicht geben.

        Der Schritt läuft außerhalb der Arbeitseinheit des Laufs, weil er einen anderen Store
        betrifft, und nie im Trockenlauf: Dessen Rückrollen umfasst genau eine Transaktion.
        """
        concepts = ConceptService(self._settings, self._unit_of_work, clock=self._clock)
        return concepts.refresh_bridges_into(store)

    def _cursor_laden(self, source: str, *, store: str) -> Cursor | None:
        """Der gespeicherte Stand einer Quelle; ``None`` heißt "noch nie gelaufen"."""
        stand = self.cursor_of(source, store=store)
        return None if stand.is_empty else stand

    def _cursor_speichern(
        self, source: str, cursor: Cursor, *, request: SyncRequest, store: str
    ) -> None:
        """Schreibt die neue Fortschrittsmarke fort (§7.4)."""
        with self._unit_of_work(store) as uow:
            uow.cursors.save(source, cursor, full_sync_at=self._clock() if request.full else None)

    def _zwischenstand(self, run: Run, zahlen: dict[str, int], *, store: str) -> None:
        """Schreibt den Zwischenstand eines laufenden Syncs (§16.3).

        In einer eigenen, sofort abgeschlossenen Transaktion: Sonst wäre der Zwischenstand bis
        zum Ende des Laufs unsichtbar — und damit genau dann, wenn ihn niemand mehr braucht.
        """
        with self._unit_of_work(store) as uow:
            uow.runs.update(run.fortschritt(dict(zahlen)))

    def _lauf_abschliessen(
        self, run: Run, stats: dict[str, Any], *, store: str, dry_run: bool
    ) -> Run:
        """Beendet einen Lauf erfolgreich und schreibt die Statistik (§10.1)."""
        beendet = run.beendet(status=RunStatus.SUCCEEDED, now=self._clock(), stats=stats)
        if not dry_run:
            with self._unit_of_work(store) as uow:
                uow.runs.update(beendet)
        _log.info(
            "lauf.beendet",
            run_id=str(beendet.id),
            status=str(beendet.status),
            duration_seconds=beendet.duration_seconds,
            **stats,
        )
        return beendet

    def _lauf_scheitern(self, run: Run, exc: SourceError, *, store: str, dry_run: bool) -> Run:
        """Beendet einen Lauf mit ``failed`` — ohne den Cursor anzufassen (§21.3, §22.3).

        Der Fehlertext geht in ``runs.error`` und nennt Typ und Meldung. Er enthält keinen
        Inhalt und keine Zugangsdaten: Was ein Adapter in seine Ausnahme schreibt, ist die URL
        eines Pfades und ein Statuscode (§21.1).
        """
        beendet = run.beendet(
            status=RunStatus.FAILED, now=self._clock(), error=f"{type(exc).__name__}: {exc}"
        )
        if not dry_run:
            with self._unit_of_work(store) as uow:
                uow.runs.update(beendet)
        _log.warning("lauf.gescheitert", run_id=str(beendet.id), error=beendet.error)
        return beendet


class _Probelauf:
    """Eine Fabrik, die immer dieselbe offene Arbeitseinheit herausgibt (Trockenlauf, §19).

    Sie erfüllt die Ports :class:`UnitOfWorkFactory` und :class:`UnitOfWork` zugleich, weil der
    aufrufende Dienst beides benutzt, ohne es zu wissen: Er ruft die Fabrik und betritt das
    Ergebnis als Kontextmanager. Betreten und Verlassen sind hier folgenlos — die Transaktion
    beginnt einmal außen und endet einmal außen, mit einem Rückrollen.

    Der Zweck ist nicht Bequemlichkeit, sondern Aussagekraft: So durchläuft ein Trockenlauf
    denselben Code wie ein echter Lauf, bis hinunter zum ``INSERT``. Eine Vorschau, die den
    Schreibpfad umgeht, könnte über ihn nichts sagen.
    """

    def __init__(self, einheit: UnitOfWork, store: str, echte: UnitOfWorkFactory) -> None:
        self._einheit = einheit
        self._store = store
        self._echte = echte

    def __call__(self, store: str) -> Any:
        """Der eigene Store als stillgelegte Einheit; jeder andere als gewöhnliche Einheit.

        Ein fremder Store wird während eines Laufs nur *gelesen*: Die Auflösung einer Referenz
        über die Grenze fragt, welche IDs dort auffindbar sind (§12.1). Diese Frage muss auch ein
        Trockenlauf stellen dürfen, sonst sagte er über eine Notiz mit Brücken nichts aus.
        Geschrieben wird ausschließlich in den einen Store, dessen Transaktion am Ende zurückgeht.
        """
        if store != self._store:
            return self._echte(store)
        return self

    # Die Arbeitseinheit selbst wird durchgereicht; nur ihr Lebenszyklus ist stillgelegt.
    @property
    def store(self) -> str:
        return self._einheit.store

    @property
    def concepts(self) -> Any:
        return self._einheit.concepts

    @property
    def edges(self) -> Any:
        return self._einheit.edges

    @property
    def changes(self) -> Any:
        return self._einheit.changes

    @property
    def runs(self) -> Any:
        return self._einheit.runs

    @property
    def cursors(self) -> Any:
        return self._einheit.cursors

    def commit(self) -> None:
        """Folgenlos: Der Trockenlauf schreibt am Ende nichts fest."""

    def rollback(self) -> None:
        """Folgenlos: Das Rückrollen besorgt der äußere Block, und zwar genau einmal."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


__all__ = ["IngestReport", "RunNotFound", "SourceBusy", "SyncRequest", "SyncService"]
