"""Das Lauf-Modell (§7.4, §10.1).

Ein Lauf ist unveränderlich und schreitet über Kopien voran. Diese Tests halten fest, was dabei
erhalten bleiben muss — und was sich ändern *darf*.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wissensgraph.domain.runs import Run, RunKind, RunStatus, new_run_id

pytestmark = pytest.mark.unit

BEGINN = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
ENDE = datetime(2026, 3, 1, 8, 0, 42, tzinfo=UTC)


@pytest.fixture
def lauf() -> Run:
    return Run(id=new_run_id(), kind=RunKind.SYNC, params={"source": "confluence-eng"})


class TestRunStatus:
    @pytest.mark.parametrize(
        ("status", "final"),
        [
            (RunStatus.QUEUED, False),
            (RunStatus.RUNNING, False),
            (RunStatus.SUCCEEDED, True),
            (RunStatus.FAILED, True),
            (RunStatus.CANCELLED, True),
        ],
    )
    def test_endzustaende(self, status: RunStatus, final: bool) -> None:
        """Die Nebenläufigkeitsprüfung aus §10.5 hängt an genau dieser Unterscheidung."""
        assert status.is_final is final


class TestLebenszyklus:
    def test_ein_neuer_lauf_wartet(self, lauf: Run) -> None:
        assert lauf.status is RunStatus.QUEUED
        assert lauf.progress == 0.0
        assert lauf.duration_seconds is None

    def test_gestartet_setzt_zeit_und_zustand(self, lauf: Run) -> None:
        gestartet = lauf.gestartet(BEGINN)

        assert gestartet.status is RunStatus.RUNNING
        assert gestartet.started_at == BEGINN
        # Das Ausgangsobjekt bleibt unberührt — sonst wäre 'unveränderlich' nur ein Wort.
        assert lauf.status is RunStatus.QUEUED

    def test_fortschritt_wechselt_den_zustand_nicht(self, lauf: Run) -> None:
        """§16.3 will einen Zwischenstand, keine geschätzte Prozentzahl."""
        zwischen = lauf.gestartet(BEGINN).fortschritt({"documents": 300})

        assert zwischen.stats == {"documents": 300}
        assert zwischen.status is RunStatus.RUNNING
        assert zwischen.progress == 0.0

    def test_erfolgreicher_abschluss(self, lauf: Run) -> None:
        beendet = lauf.gestartet(BEGINN).beendet(
            status=RunStatus.SUCCEEDED, now=ENDE, stats={"documents": 120}
        )

        assert beendet.is_final
        assert beendet.progress == 1.0
        assert beendet.duration_seconds == 42.0
        assert beendet.error is None

    def test_gescheiterter_lauf_behaelt_seinen_fortschritt(self, lauf: Run) -> None:
        """Ein abgebrochener Lauf *ist* nicht fertig geworden; 100 % wären eine Falschaussage."""
        beendet = lauf.gestartet(BEGINN).beendet(
            status=RunStatus.FAILED, now=ENDE, error="SourceUnavailable: 503"
        )

        assert beendet.progress == 0.0
        assert beendet.error == "SourceUnavailable: 503"

    def test_as_dict_ist_serialisierbar(self, lauf: Run) -> None:
        payload = lauf.gestartet(BEGINN).as_dict()

        assert payload["status"] == "running"
        assert payload["kind"] == "sync"
        assert isinstance(payload["id"], str)


class TestFortschrittsgrenzen:
    def test_ein_anteil_ausserhalb_von_null_bis_eins_ist_ungueltig(self) -> None:
        with pytest.raises(ValueError, match="progress"):
            Run(id=new_run_id(), kind=RunKind.SYNC, progress=1.5)
