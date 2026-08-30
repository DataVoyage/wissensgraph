"""Sync-Läufe in einer echten Datenbank (§24 Abnahme Stufe 4, §10.5, §22.1).

Dieselben vier Abnahmekriterien laufen in ``tests/unit/test_sync_service.py`` gegen die
speicherresidenten Ports. Hier geht es um das, was ein Fake nicht zeigen kann:

* dass ``runs`` und ``source_cursors`` aus §7.4 die Läufe wirklich aufnehmen — mit JSONB-Params,
  JSONB-Stats und einem opaken Cursor, den niemand außer dem Adapter liest;
* dass der Advisory-Lock aus §10.5 über *Verbindungsgrenzen* wirkt und nicht nur innerhalb eines
  Objekts. Genau das ist der Fall, auf den es im Betrieb ankommt: zwei Container.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from support import quellen
from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import ConceptStatus
from wissensgraph.domain.runs import RunStatus
from wissensgraph.infrastructure.adapters import ConfluenceAdapter
from wissensgraph.infrastructure.db import StoreRegistry, upgrade_all
from wissensgraph.infrastructure.db.locks import SqlSourceLocks
from wissensgraph.infrastructure.db.tables import (
    change_log,
    concepts,
    edges,
    runs,
    source_cursors,
)
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.ports.runs import SourceBusy
from wissensgraph.services.sync import SyncRequest, SyncService

pytestmark = pytest.mark.integration

SEITEN = 120
GELOESCHT = "confluence:100003"


def nicht_warten(_seconds: float) -> None:
    """Der Backoff soll im Test keine Zeit kosten."""


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    """Beide Testdatenbanken auf dem Stand des Schemas aus §7.4."""
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


class Umgebung:
    """Mock-Server, Adapter und Sync-Dienst auf einer echten Datenbank."""

    def __init__(self, settings: Settings, registry: StoreRegistry) -> None:
        self.app = quellen.mock_app()
        self.settings = settings
        self.registry = registry
        self.cfg = quellen.quelle(
            "confluence-eng",
            adapter="confluence",
            id_prefix="confluence",
            base_url=quellen.CONFLUENCE_BASE,
            selection={"spaces": ["ENG", "ARCH"]},
        )
        self.adapter = ConfluenceAdapter(
            client_factory=quellen.client_factory(self.app), sleep=nicht_warten
        )
        self.adapter.configure(self.cfg)
        self.locks = SqlSourceLocks(registry)
        self.sync = SyncService(
            settings,
            UnitOfWorkFactory(registry),
            self.locks,
            known_prefixes=("confluence", "jira"),
        )

    def lauf(self, **kwargs: Any) -> Any:
        return self.sync.sync(self.adapter, self.cfg, SyncRequest(**kwargs))

    @property
    def steuerung(self) -> Any:
        return quellen.control(self.app)

    def zaehle(self, tabelle: Any, *bedingungen: Any) -> int:
        statement = select(func.count()).select_from(tabelle)
        for bedingung in bedingungen:
            statement = statement.where(bedingung)
        with self.registry.engine("shared").connect() as connection:
            return int(connection.execute(statement).scalar_one())

    def zeile(self, tabelle: Any, bedingung: Any) -> Any:
        with self.registry.engine("shared").connect() as connection:
            return connection.execute(select(tabelle).where(bedingung)).mappings().first()


@pytest.fixture
def umgebung(postgres_settings: Settings, migrated: StoreRegistry) -> Umgebung:
    return Umgebung(postgres_settings, migrated)


class TestLaufBuchfuehrung:
    def test_der_lauf_landet_in_runs(self, umgebung: Umgebung) -> None:
        run = umgebung.lauf()

        zeile = umgebung.zeile(runs, runs.c.id == run.id)
        assert zeile["status"] == "succeeded"
        assert zeile["kind"] == "sync"
        assert zeile["params"]["source"] == "confluence-eng"
        assert zeile["stats"]["created"] == SEITEN
        assert zeile["progress"] == 1.0
        assert zeile["started_at"] is not None and zeile["finished_at"] is not None

    def test_der_cursor_landet_in_source_cursors(self, umgebung: Umgebung) -> None:
        umgebung.lauf(full=True)

        zeile = umgebung.zeile(source_cursors, source_cursors.c.source_name == "confluence-eng")
        assert zeile["cursor"]
        assert zeile["last_full_sync"] is not None

    def test_das_journal_haengt_am_lauf(self, umgebung: Umgebung) -> None:
        """§7.4: Vom Lauf zur einzelnen Änderung ist ein Join, keine Rekonstruktion."""
        run = umgebung.lauf()

        assert umgebung.zaehle(change_log, change_log.c.run_id == run.id) > SEITEN

    def test_der_zweite_lauf_schreibt_nichts(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        journal_vorher = umgebung.zaehle(change_log)

        zweiter = umgebung.lauf()

        assert zweiter.stats["documents"] == 0
        assert umgebung.zaehle(change_log) == journal_vorher


class TestLoeschbehandlung:
    """ "Löschszenario setzt Tombstones ohne Kantenverlust" (§24, §7.6)."""

    def test_grabstein_ohne_kantenverlust(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        kanten_vorher = umgebung.zaehle(edges)
        eingehend_vorher = umgebung.zaehle(edges, edges.c.to_id == GELOESCHT)
        assert eingehend_vorher > 0

        umgebung.steuerung.post("/_control/scenario/deletion")
        run = umgebung.lauf()

        assert run.stats["deleted"] == 1
        assert umgebung.zeile(concepts, concepts.c.id == GELOESCHT)["status"] == str(
            ConceptStatus.TOMBSTONE
        )
        assert umgebung.zaehle(edges) == kanten_vorher
        assert umgebung.zaehle(edges, edges.c.to_id == GELOESCHT) == eingehend_vorher


class TestNebenlaeufigkeit:
    """ "Paralleler Start derselben Quelle wird abgewiesen" (§24, §10.5).

    Der Advisory-Lock hängt an der PostgreSQL-*Sitzung*. Ein zweiter Halter mit einer eigenen
    Verbindung ist deshalb der Fall, den ein Fake im Speicher nicht nachbilden kann — und der
    einzige, der über zwei Container etwas aussagt.
    """

    def test_ein_zweiter_halter_wird_abgewiesen(self, umgebung: Umgebung) -> None:
        with umgebung.locks.hold(store="shared", name="confluence-eng"):
            zweite = SqlSourceLocks(umgebung.registry)

            with (
                pytest.raises(SourceBusy, match="confluence-eng"),
                zweite.hold(store="shared", name="confluence-eng"),
            ):
                pytest.fail("Die zweite Sperre hätte nicht zu bekommen sein dürfen.")

    def test_eine_andere_quelle_sperrt_nicht(self, umgebung: Umgebung) -> None:
        """Die Sperre gilt je Quellname — Confluence blockiert Jira nicht."""
        with (
            umgebung.locks.hold(store="shared", name="confluence-eng"),
            SqlSourceLocks(umgebung.registry).hold(store="shared", name="jira-team"),
        ):
            pass

    def test_nach_dem_block_ist_die_sperre_frei(self, umgebung: Umgebung) -> None:
        with umgebung.locks.hold(store="shared", name="confluence-eng"):
            pass

        with SqlSourceLocks(umgebung.registry).hold(store="shared", name="confluence-eng"):
            pass

    def test_ein_lauf_gegen_eine_belegte_quelle_nennt_den_laufenden(
        self, umgebung: Umgebung
    ) -> None:
        laufend = umgebung.sync.prepare(umgebung.cfg, SyncRequest())

        with umgebung.locks.hold(store="shared", name="confluence-eng"):
            zweiter = SyncService(
                umgebung.settings,
                UnitOfWorkFactory(umgebung.registry),
                SqlSourceLocks(umgebung.registry),
            )
            with pytest.raises(SourceBusy) as info:
                zweiter.sync(umgebung.adapter, umgebung.cfg)

        assert info.value.run_id == laufend.id


class TestAusfall:
    """ "Netzwerkabbruch mitten im Lauf lässt den Cursor unverändert" (§24, §22.3)."""

    def test_der_cursor_bleibt_nach_einem_abbruch_stehen(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        vorher = umgebung.zeile(source_cursors, source_cursors.c.source_name == "confluence-eng")[
            "cursor"
        ]

        umgebung.steuerung.post("/_control/scenario/incremental_update")
        umgebung.steuerung.post(
            "/_control/fail", json={"status": 503, "count": 99, "after_requests": 1}
        )
        run = umgebung.lauf()

        assert run.status is RunStatus.FAILED
        nachher = umgebung.zeile(source_cursors, source_cursors.c.source_name == "confluence-eng")[
            "cursor"
        ]
        assert nachher == vorher

    def test_der_gescheiterte_lauf_steht_mit_grund_in_runs(self, umgebung: Umgebung) -> None:
        umgebung.steuerung.post("/_control/fail", json={"status": 503, "count": 99})

        run = umgebung.lauf()

        zeile = umgebung.zeile(runs, runs.c.id == run.id)
        assert zeile["status"] == "failed"
        assert "503" in zeile["error"]


class TestTrockenlauf:
    def test_ein_trockenlauf_hinterlaesst_die_datenbank_unveraendert(
        self, umgebung: Umgebung
    ) -> None:
        """Der stärkste Beleg: Alles ist wirklich geschrieben worden — und wieder weg."""
        run = umgebung.lauf(dry_run=True)

        assert run.stats["created"] == SEITEN
        assert umgebung.zaehle(concepts) == 0
        assert umgebung.zaehle(edges) == 0
        assert umgebung.zaehle(runs) == 0
        assert umgebung.zaehle(source_cursors) == 0


class TestQueueUndWorker:
    """Der asynchrone Weg aus §16.3: erst ``runs``, dann Job, dann Worker."""

    @pytest.fixture
    def laufzeit(self, postgres_settings: Settings, migrated: StoreRegistry, tmp_path: Any) -> Any:
        """Eine Laufzeit über eine Quelle ohne Netzwerk, mit Warteschlange im Speicher."""
        import yaml

        from wissensgraph.infrastructure.queue import MemoryJobQueue
        from wissensgraph.runtime import Runtime

        pfad = tmp_path / "sources.yaml"
        pfad.write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {
                            "name": "fixtures",
                            "adapter": "fixture-source",
                            "id_prefix": "fix",
                            "target": {
                                "scope": "engineering",
                                "default_type": "Confluence Page",
                            },
                            "selection": {
                                "documents": [
                                    {"external_id": "1", "title": "Eins"},
                                    {"external_id": "2", "title": "Zwei [[fix:1]]"},
                                ]
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with Runtime(postgres_settings, sources_file=pfad, queue=MemoryJobQueue()) as runtime:
            yield runtime

    def test_submit_legt_den_lauf_an_und_stellt_den_job_ein(self, laufzeit: Any) -> None:
        run = laufzeit.submit_sync("fixtures")

        assert run.status is RunStatus.QUEUED
        assert laufzeit.jobs.pending == 1
        assert laufzeit.sync.get_run(run.id, store="shared").status is RunStatus.QUEUED

    def test_der_worker_fuehrt_den_vorbereiteten_lauf_aus(self, laufzeit: Any) -> None:
        """Der Job trägt nur einen Verweis; der Zustand bleibt in ``runs`` (§16.3)."""
        run = laufzeit.submit_sync("fixtures")

        assert laufzeit.work(once=True) == 1

        danach = laufzeit.sync.get_run(run.id, store="shared")
        assert danach.id == run.id
        assert danach.status is RunStatus.SUCCEEDED
        assert danach.stats["created"] == 2

    def test_die_parameter_ueberleben_den_weg_durch_die_queue(self, laufzeit: Any) -> None:
        from wissensgraph.services.sync import SyncRequest

        run = laufzeit.submit_sync("fixtures", SyncRequest(full=True))
        laufzeit.work(once=True)

        danach = laufzeit.sync.get_run(run.id, store="shared")
        assert danach.params["full"] is True
        assert danach.status is RunStatus.SUCCEEDED

    def test_ein_lauf_ueber_alle_quellen(self, laufzeit: Any) -> None:
        laeufe = laufzeit.run_sync_all()

        assert [run.status for run in laeufe] == [RunStatus.SUCCEEDED]
