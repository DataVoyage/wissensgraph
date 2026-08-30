"""Die Sync-Orchestrierung — die Abnahme der Stufe 4 ohne Datenbank (§24, §10.1, §10.5, §21.3).

Alle vier Abnahmekriterien der Stufe stehen hier, gegen die speicherresidenten Ports und den
echten Mock-Server im selben Prozess:

1. vollständiger und inkrementeller Lauf über den Mock,
2. Löschszenario setzt Tombstones ohne Kantenverlust,
3. paralleler Start derselben Quelle wird abgewiesen,
4. Netzwerkabbruch mitten im Lauf lässt den Cursor unverändert.

Dieselben vier laufen in ``tests/integration/test_sync_postgres.py`` noch einmal gegen echtes
PostgreSQL — das dritte davon erst dort wirklich: Der Advisory-Lock aus §10.5 wirkt über
Prozessgrenzen, die Speicher-Sperre nur innerhalb eines Prozesses.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from support import quellen
from support.memory import MemorySourceLocks, MemoryUnitOfWorkFactory
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourceConfig
from wissensgraph.domain.concepts import ConceptStatus
from wissensgraph.domain.runs import RunKind, RunStatus
from wissensgraph.infrastructure.adapters import ConfluenceAdapter
from wissensgraph.ports.runs import SourceBusy
from wissensgraph.services.sync import RunNotFound, SyncRequest, SyncService

pytestmark = pytest.mark.unit

SEITEN = 120

#: Die Seite, die das Löschszenario aus ``fixtures/scenarios/deletion.json`` entfernt.
GELOESCHT = "confluence:100003"


def nicht_warten(_seconds: float) -> None:
    """Der Backoff soll im Test keine Zeit kosten."""


class Umgebung:
    """Mock-Server, Adapter und Dienste für einen Lauf über die Confluence-Quelle."""

    def __init__(self, settings: Settings) -> None:
        self.app = quellen.mock_app()
        self.factory = MemoryUnitOfWorkFactory(["shared", "personal"])
        self.locks = MemorySourceLocks()
        self.cfg: SourceConfig = quellen.quelle(
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
        self.sync = SyncService(
            settings, self.factory, self.locks, known_prefixes=("confluence", "jira")
        )

    def lauf(self, **kwargs: Any) -> Any:
        """Ein Sync-Lauf über die Confluence-Quelle."""
        return self.sync.sync(self.adapter, self.cfg, SyncRequest(**kwargs))

    @property
    def steuerung(self) -> TestClient:
        return quellen.control(self.app)

    @property
    def shared(self) -> Any:
        return self.factory.state("shared")


@pytest.fixture
def umgebung(settings: Settings) -> Umgebung:
    return Umgebung(settings)


class TestVollstaendigerUndInkrementellerLauf:
    """Abnahmekriterium 1 (§24, Stufe 4)."""

    def test_der_erste_lauf_bildet_den_korpus_ab(self, umgebung: Umgebung) -> None:
        run = umgebung.lauf()

        assert run.status is RunStatus.SUCCEEDED
        assert run.kind is RunKind.SYNC
        assert run.stats["documents"] == SEITEN
        assert run.stats["created"] == SEITEN
        assert run.progress == 1.0
        assert len(umgebung.shared.concepts) == SEITEN

    def test_der_lauf_steht_danach_in_runs(self, umgebung: Umgebung) -> None:
        run = umgebung.lauf()

        gespeichert = umgebung.sync.get_run(run.id, store="shared")
        assert gespeichert.status is RunStatus.SUCCEEDED
        assert gespeichert.params["source"] == "confluence-eng"
        assert gespeichert.stats["created"] == SEITEN

    def test_der_cursor_wird_gespeichert(self, umgebung: Umgebung) -> None:
        umgebung.lauf()

        cursor = umgebung.sync.cursor_of("confluence-eng", store="shared")
        assert not cursor.is_empty

    def test_der_zweite_lauf_liest_nur_geaendertes(self, umgebung: Umgebung) -> None:
        """§10.3: Der gespeicherte Cursor macht aus dem zweiten Lauf einen inkrementellen."""
        umgebung.lauf()
        umgebung.steuerung.post("/_control/scenario/incremental_update")

        zweiter = umgebung.lauf()

        assert zweiter.stats["documents"] == 1
        assert zweiter.stats["updated"] == 1
        assert zweiter.stats["created"] == 0

    def test_ohne_aenderung_schreibt_der_zweite_lauf_nichts(self, umgebung: Umgebung) -> None:
        umgebung.lauf()

        zweiter = umgebung.lauf()

        assert zweiter.stats["documents"] == 0
        assert zweiter.stats["created"] == 0
        assert zweiter.stats["updated"] == 0

    def test_full_ignoriert_den_cursor(self, umgebung: Umgebung) -> None:
        """``--full`` ist der Weg zurück zum Vollabgleich, ohne den Cursor löschen zu müssen."""
        umgebung.lauf()

        zweiter = umgebung.lauf(full=True)

        assert zweiter.stats["documents"] == SEITEN
        # Gelesen ja, geschrieben nein: Der Hash entscheidet weiterhin (§10.2 Regel 3).
        assert zweiter.stats["unchanged"] == SEITEN
        assert zweiter.stats["updated"] == 0

    def test_full_vermerkt_den_vollabgleich(self, umgebung: Umgebung) -> None:
        umgebung.lauf(full=True)

        stand = umgebung.factory.state("shared").cursors["confluence-eng"]
        assert stand.last_full_sync is not None

    def test_ein_inkrementeller_lauf_vergisst_den_vollabgleich_nicht(
        self, umgebung: Umgebung
    ) -> None:
        umgebung.lauf(full=True)
        vorher = umgebung.factory.state("shared").cursors["confluence-eng"].last_full_sync

        umgebung.lauf()

        assert umgebung.factory.state("shared").cursors["confluence-eng"].last_full_sync == vorher

    def test_cursor_vergessen_erzwingt_den_vollabgleich(self, umgebung: Umgebung) -> None:
        umgebung.lauf()

        assert umgebung.sync.forget_cursor("confluence-eng", store="shared") is True
        assert umgebung.lauf().stats["documents"] == SEITEN

    def test_cursor_vergessen_meldet_wenn_es_nichts_zu_vergessen_gab(
        self, umgebung: Umgebung
    ) -> None:
        assert umgebung.sync.forget_cursor("confluence-eng", store="shared") is False


class TestLoeschbehandlung:
    """Abnahmekriterium 2: "Löschszenario setzt Tombstones ohne Kantenverlust" (§7.6, §24)."""

    def test_die_geloeschte_seite_wird_zum_grabstein(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        umgebung.steuerung.post("/_control/scenario/deletion")

        run = umgebung.lauf()

        assert run.stats["deleted"] == 1
        assert umgebung.shared.concepts[GELOESCHT].status is ConceptStatus.TOMBSTONE

    def test_die_kanten_auf_die_geloeschte_seite_bleiben(self, umgebung: Umgebung) -> None:
        """Der eigentliche Punkt aus §7.6: Eine Notiz darauf soll nachvollziehbar bleiben."""
        umgebung.lauf()
        vorher = [edge for edge in umgebung.shared.edges if edge.to_id == GELOESCHT]
        assert vorher, "Die Fixture muss eine Kante auf die zu löschende Seite enthalten."

        umgebung.steuerung.post("/_control/scenario/deletion")
        umgebung.lauf()

        nachher = [edge for edge in umgebung.shared.edges if edge.to_id == GELOESCHT]
        assert {edge.id for edge in nachher} == {edge.id for edge in vorher}

    def test_der_inhalt_bleibt_erhalten(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        umgebung.steuerung.post("/_control/scenario/deletion")
        umgebung.lauf()

        assert umgebung.shared.concepts[GELOESCHT].body

    def test_die_loeschung_steht_im_journal(self, umgebung: Umgebung) -> None:
        umgebung.lauf()
        umgebung.steuerung.post("/_control/scenario/deletion")
        run = umgebung.lauf()

        eintraege = [
            entry
            for entry in umgebung.shared.changes
            if entry.concept_id == GELOESCHT and entry.change_type == "source_deleted"
        ]
        assert len(eintraege) == 1
        assert eintraege[0].run_id == run.id

    def test_eine_wiederholte_loeschmeldung_schreibt_nicht_erneut(self, umgebung: Umgebung) -> None:
        """Löschung ist ein Zustand, kein Ereignis — sonst wüchse das Journal bei jedem Lauf."""
        umgebung.lauf()
        umgebung.steuerung.post("/_control/scenario/deletion")
        umgebung.lauf()

        dritter = umgebung.lauf(full=True)

        assert dritter.stats["deleted"] == 0


class TestNebenlaeufigkeit:
    """Abnahmekriterium 3: "paralleler Start derselben Quelle wird abgewiesen" (§10.5)."""

    def test_ein_zweiter_lauf_wird_abgewiesen(self, umgebung: Umgebung) -> None:
        umgebung.locks.gehalten.add(("shared", "confluence-eng"))

        with pytest.raises(SourceBusy, match="confluence-eng"):
            umgebung.lauf()

    def test_die_abweisung_nennt_den_laufenden_lauf(self, umgebung: Umgebung) -> None:
        """§10.5 verlangt "409 Conflict mit der ID des laufenden Runs"."""
        laufend = umgebung.sync.prepare(umgebung.cfg, SyncRequest())
        umgebung.locks.gehalten.add(("shared", "confluence-eng"))

        with pytest.raises(SourceBusy) as info:
            umgebung.lauf()

        assert info.value.run_id == laufend.id

    def test_nach_dem_lauf_ist_die_sperre_wieder_frei(self, umgebung: Umgebung) -> None:
        umgebung.lauf()

        assert umgebung.locks.gehalten == set()

    def test_auch_ein_gescheiterter_lauf_gibt_die_sperre_frei(self, umgebung: Umgebung) -> None:
        umgebung.steuerung.post("/_control/fail", json={"status": 503, "count": 99})

        umgebung.lauf()

        assert umgebung.locks.gehalten == set()


class TestAusfall:
    """Abnahmekriterium 4 und §21.3: Ein Ausfall lässt den Cursor unverändert."""

    def test_ein_ausfall_beendet_den_lauf_mit_failed(self, umgebung: Umgebung) -> None:
        umgebung.steuerung.post("/_control/fail", json={"status": 503, "count": 99})

        run = umgebung.lauf()

        assert run.status is RunStatus.FAILED
        assert run.error is not None
        assert run.finished_at is not None

    def test_der_gescheiterte_lauf_steht_mit_grund_in_runs(self, umgebung: Umgebung) -> None:
        umgebung.steuerung.post("/_control/fail", json={"status": 503, "count": 99})
        run = umgebung.lauf()

        gespeichert = umgebung.sync.get_run(run.id, store="shared")
        assert gespeichert.status is RunStatus.FAILED
        assert gespeichert.error == run.error

    def test_ein_abbruch_mitten_im_lauf_laesst_den_cursor_stehen(self, umgebung: Umgebung) -> None:
        """§22.3, letzte Zusicherung — der Grund, warum es ``after_requests`` im Mock gibt."""
        umgebung.lauf()
        vorher = umgebung.sync.cursor_of("confluence-eng", store="shared")
        umgebung.steuerung.post("/_control/scenario/incremental_update")
        umgebung.steuerung.post(
            "/_control/fail", json={"status": 503, "count": 99, "after_requests": 1}
        )

        run = umgebung.lauf()

        assert run.status is RunStatus.FAILED
        assert umgebung.sync.cursor_of("confluence-eng", store="shared") == vorher

    def test_die_wiederholung_nach_einem_ausfall_ist_gefahrlos(self, umgebung: Umgebung) -> None:
        umgebung.steuerung.post("/_control/fail", json={"status": 503, "count": 99})
        umgebung.lauf()
        umgebung.steuerung.post("/_control/reset")

        wiederholung = umgebung.lauf()

        assert wiederholung.status is RunStatus.SUCCEEDED
        assert wiederholung.stats["created"] == SEITEN


class TestTrockenlauf:
    """``--dry-run`` (§19): alles ausführen, nichts behalten."""

    def test_der_trockenlauf_meldet_dieselben_zahlen(self, umgebung: Umgebung) -> None:
        run = umgebung.lauf(dry_run=True)

        assert run.status is RunStatus.SUCCEEDED
        assert run.stats["documents"] == SEITEN
        assert run.stats["created"] == SEITEN
        assert run.stats["dry_run"] is True

    def test_der_trockenlauf_schreibt_keine_konzepte(self, umgebung: Umgebung) -> None:
        umgebung.lauf(dry_run=True)

        assert umgebung.shared.concepts == {}
        assert umgebung.shared.edges == []
        assert umgebung.shared.changes == []

    def test_der_trockenlauf_hinterlaesst_keinen_lauf(self, umgebung: Umgebung) -> None:
        """``--dry-run`` verspricht, nichts zu verändern; eine Zeile in ``runs`` wäre eine."""
        run = umgebung.lauf(dry_run=True)

        assert umgebung.shared.runs == {}
        with pytest.raises(RunNotFound):
            umgebung.sync.get_run(run.id, store="shared")

    def test_der_trockenlauf_bewegt_den_cursor_nicht(self, umgebung: Umgebung) -> None:
        umgebung.lauf(dry_run=True)

        assert umgebung.sync.cursor_of("confluence-eng", store="shared").is_empty

    def test_nach_einem_trockenlauf_ist_der_echte_lauf_unveraendert(
        self, umgebung: Umgebung
    ) -> None:
        umgebung.lauf(dry_run=True)

        echt = umgebung.lauf()

        assert echt.stats["created"] == SEITEN


class TestVorbereiteteLaeufe:
    """Der Weg aus §16.3: erst anlegen, dann ausführen."""

    def test_prepare_legt_einen_wartenden_lauf_an(self, umgebung: Umgebung) -> None:
        run = umgebung.sync.prepare(umgebung.cfg, SyncRequest(full=True))

        assert run.status is RunStatus.QUEUED
        assert run.params["full"] is True
        assert umgebung.sync.get_run(run.id, store="shared").status is RunStatus.QUEUED

    def test_ein_vorbereiteter_lauf_wird_uebernommen(self, umgebung: Umgebung) -> None:
        vorbereitet = umgebung.sync.prepare(umgebung.cfg, SyncRequest())

        run = umgebung.sync.sync(
            umgebung.adapter, umgebung.cfg, SyncRequest(), run_id=vorbereitet.id
        )

        assert run.id == vorbereitet.id
        assert run.status is RunStatus.SUCCEEDED
        assert len(umgebung.shared.runs) == 1

    def test_ein_unbekannter_lauf_bricht_verstaendlich_ab(self, umgebung: Umgebung) -> None:
        from uuid import uuid4

        with pytest.raises(RunNotFound, match="Kein Lauf"):
            umgebung.sync.sync(umgebung.adapter, umgebung.cfg, run_id=uuid4())

    def test_recent_runs_zeigt_die_neuesten_zuerst(self, umgebung: Umgebung) -> None:
        erster = umgebung.lauf()
        zweiter = umgebung.lauf(full=True)

        laeufe = umgebung.sync.recent_runs(store="shared", kind=RunKind.SYNC)

        assert [run.id for run in laeufe] == [zweiter.id, erster.id]

    def test_recent_runs_ist_leer_ohne_laeufe(self, umgebung: Umgebung) -> None:
        assert umgebung.sync.recent_runs(store="shared") == ()


class TestZwischenstand:
    """§16.3: "schreibt Fortschritt und Statistik"."""

    def test_der_zwischenstand_erscheint_waehrend_des_laufs(
        self, umgebung: Umgebung, mocker: Any
    ) -> None:
        gesehen: list[int] = []
        echt = umgebung.sync._zwischenstand

        def mitschreiben(run: Any, zahlen: dict[str, int], *, store: str) -> None:
            gesehen.append(zahlen["documents"])
            echt(run, zahlen, store=store)

        mocker.patch.object(umgebung.sync, "_zwischenstand", mitschreiben)

        umgebung.lauf()

        # 120 Seiten, Intervall 100: genau ein Zwischenstand, und zwar bei 100.
        assert gesehen == [100]


class TestSyncRequest:
    def test_parameter_gehen_in_den_lauf_und_zurueck(self) -> None:
        request = SyncRequest(full=True, dry_run=True)

        zurueck = SyncRequest.from_params(request.as_params("confluence-eng"))

        assert zurueck.full is True
        assert zurueck.dry_run is True

    def test_fehlende_parameter_sind_der_regelfall(self) -> None:
        zurueck = SyncRequest.from_params({"source": "confluence-eng"})

        assert zurueck.full is False
        assert zurueck.dry_run is False


class TestEinzelneFehlerhafteObjekte:
    """§21.3: "Einzelnes Quellobjekt fehlerhaft → überspringen, in ``runs.stats.errors`` zählen,
    Lauf fortsetzen." Der Unterschied zu einem Quellausfall ist wesentlich: Der betrifft alle noch
    ausstehenden Objekte, dieser genau eines.
    """

    @pytest.fixture
    def fixture_lauf(self, settings: Settings) -> Any:
        """Ein Lauf über drei Dokumente, von denen eines einen unbekannten Typ nennt."""
        from wissensgraph.infrastructure.adapters import FixtureAdapter

        cfg = quellen.quelle(
            "fixtures",
            adapter="fixture-source",
            id_prefix="fix",
            selection={
                "documents": [
                    {"external_id": "1", "title": "Gut", "updated_at": "2026-05-01T10:00:00Z"},
                    {
                        "external_id": "2",
                        "title": "Kaputt",
                        "type_hint": "Gibt Es Nicht",
                        "updated_at": "2026-05-01T11:00:00Z",
                    },
                    {
                        "external_id": "3",
                        "title": "Auch gut",
                        "updated_at": "2026-05-01T12:00:00Z",
                    },
                ]
            },
        )
        adapter = FixtureAdapter()
        adapter.configure(cfg)
        factory = MemoryUnitOfWorkFactory(["shared", "personal"])
        dienst = SyncService(settings, factory, MemorySourceLocks(), known_prefixes=("fix",))
        return dienst, adapter, cfg, factory

    def test_der_lauf_geht_weiter_und_zaehlt_den_fehler(self, fixture_lauf: Any) -> None:
        dienst, adapter, cfg, factory = fixture_lauf

        run = dienst.sync(adapter, cfg, SyncRequest())

        assert run.status is RunStatus.SUCCEEDED
        assert run.stats["documents"] == 3
        assert run.stats["created"] == 2
        assert run.stats["errors"] == 1
        assert set(factory.state("shared").concepts) == {"fix:1", "fix:3"}

    def test_der_cursor_wird_trotzdem_fortgeschrieben(self, fixture_lauf: Any) -> None:
        """Der Lauf *ist* vollständig durchgelaufen; nur ein Objekt war unbrauchbar."""
        dienst, adapter, cfg, _ = fixture_lauf

        dienst.sync(adapter, cfg, SyncRequest())

        assert not dienst.cursor_of("fixtures", store="shared").is_empty
