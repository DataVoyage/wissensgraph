"""Die Zusammenstellung des Systems (:mod:`wissensgraph.runtime`).

Hier wird geprüft, was sonst nirgends steht: welche Umsetzung ein Port bekommt. Die Fragen sind
klein, ihre Folgen nicht — eine falsch gewählte Queue macht ``wg sync`` von einem Broker abhängig,
den es nie benutzt, und ein Job, dessen Art niemand kennt, ließe seinen Lauf für immer auf
``queued`` stehen.

Ohne Datenbank: Alle Fälle hier enden, bevor eine Verbindung gebraucht wird.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from wissensgraph.config.schema import Settings
from wissensgraph.domain.runs import RunKind
from wissensgraph.infrastructure.queue import MemoryJobQueue, RedisJobQueue
from wissensgraph.ports.queue import Job
from wissensgraph.runtime import Runtime, UnknownSourceError

pytestmark = pytest.mark.unit


@pytest.fixture
def sources_file(tmp_path: Path) -> Path:
    """Eine Quellkonfiguration mit einer Quelle, die ohne Netzwerk auskommt (§9.1)."""
    pfad = tmp_path / "sources.yaml"
    pfad.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "fixtures",
                        "adapter": "fixture-source",
                        "id_prefix": "fix",
                        "target": {"scope": "engineering", "default_type": "Confluence Page"},
                        "selection": {"documents": [{"external_id": "1", "title": "Ein Dokument"}]},
                    },
                    {
                        "name": "abgeschaltet",
                        "adapter": "fixture-source",
                        "id_prefix": "aus",
                        "enabled": False,
                        "target": {"scope": "engineering", "default_type": "Confluence Page"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return pfad


@pytest.fixture
def runtime(settings: Settings, sources_file: Path) -> Any:
    with Runtime(settings, sources_file=sources_file) as laufzeit:
        yield laufzeit


class TestQueueWahl:
    def test_ohne_broker_url_wird_im_speicher_gearbeitet(
        self, settings: Settings, sources_file: Path
    ) -> None:
        """``wg sync`` arbeitet synchron und soll keinen laufenden Redis voraussetzen (§19)."""
        with Runtime(settings, sources_file=sources_file) as laufzeit:
            assert isinstance(laufzeit.jobs._queue, MemoryJobQueue)

    def test_mit_broker_url_wird_redis_gewaehlt(
        self, minimal_config_dict: dict[str, Any], sources_file: Path
    ) -> None:
        minimal_config_dict["broker_url"] = "redis://broker:6379/0"
        settings = Settings.model_validate(minimal_config_dict)

        with Runtime(settings, sources_file=sources_file) as laufzeit:
            # Der Client baut die Verbindung erst beim ersten Befehl auf — hier fällt also
            # nichts an, obwohl kein Redis läuft.
            assert isinstance(laufzeit.jobs._queue, RedisJobQueue)


class TestQuellen:
    def test_eingeschaltete_quellen_werden_registriert(self, runtime: Any) -> None:
        assert [item.name for item in runtime.registered] == ["fixtures"]

    def test_die_registrierung_geschieht_nur_einmal(self, runtime: Any) -> None:
        """``build_all`` ruft ``health()`` und damit die Quelle an — je Lauf einmal genügt."""
        erste = runtime.source("fixtures")

        assert runtime.source("fixtures") is erste
        assert runtime.registered[0] is erste

    def test_eine_unbekannte_quelle_bricht_verstaendlich_ab(self, runtime: Any) -> None:
        with pytest.raises(UnknownSourceError, match="fixtures"):
            runtime.source("gibtesnicht")

    def test_eine_abgeschaltete_quelle_gilt_als_unbekannt(self, runtime: Any) -> None:
        """``enabled: false`` heißt "gibt es gerade nicht", nicht "ist kaputt" (§8.3)."""
        with pytest.raises(UnknownSourceError):
            runtime.source("abgeschaltet")


class TestJobzuordnung:
    def test_eine_noch_nicht_umgesetzte_lauf_art_bricht_laut_ab(self, runtime: Any) -> None:
        """Ein stilles Verwerfen ließe den Lauf für immer auf 'queued' stehen.

        ``export`` ist die letzte Lauf-Art, die es als Aufzählungswert gibt, ohne umgesetzt zu
        sein. Genau dafür ist der Test da: Er hält fest, dass eine noch nicht gebaute Art laut
        abbricht — und er wird mit jeder Stufe, die eine weitere umsetzt, auf die nächste
        umgestellt.
        """
        auftrag = Job(run_id=uuid4(), kind=RunKind.EXPORT, store="shared")

        with pytest.raises(NotImplementedError, match="export"):
            runtime.handle(auftrag)

    def test_ein_job_auf_eine_unbekannte_quelle_bricht_ab(self, runtime: Any) -> None:
        auftrag = Job(run_id=uuid4(), kind=RunKind.SYNC, store="shared", params={"source": "weg"})

        with pytest.raises(UnknownSourceError):
            runtime.handle(auftrag)

    def test_ein_kaputter_job_beendet_die_worker_schleife_nicht(self, runtime: Any) -> None:
        runtime.jobs.submit(Job(run_id=uuid4(), kind=RunKind.EXPORT, store="shared"))

        assert runtime.work(once=True) == 1

    def test_ohne_job_meldet_die_schleife_null(self, runtime: Any) -> None:
        assert runtime.work(once=True) == 0


class TestQuellenGleichzeitig:
    """``sources.max_concurrency`` in ``wg sync --all`` (§19).

    Zwei Quellen behindern einander nicht: Jeder Lauf nimmt seinen eigenen Advisory-Lock auf
    *seinen* Quellnamen (§10.5), schreibt in seinen eigenen Nummernkreis (§7.5) und führt seine
    eigene Zeile in ``runs``. Was sie sich teilen, ist der Datenbank-Pool — deshalb steht die
    Grenze in der Konfiguration und nicht im Code.
    """

    @staticmethod
    def _datei(tmp_path: Path, anzahl: int, gleichzeitig: int) -> Path:
        pfad = tmp_path / "viele.yaml"
        pfad.write_text(
            yaml.safe_dump(
                {
                    "max_concurrency": gleichzeitig,
                    "sources": [
                        {
                            "name": f"quelle-{i}",
                            "adapter": "fixture-source",
                            "id_prefix": f"fix{i}",
                            "target": {
                                "scope": "engineering",
                                "default_type": "Confluence Page",
                            },
                            "selection": {
                                "documents": [{"external_id": "1", "title": f"Dokument {i}"}]
                            },
                        }
                        for i in range(anzahl)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return pfad

    def test_die_reihenfolge_der_laeufe_folgt_der_konfiguration(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        # Sonst wechselte die Ausgabe von 'wg sync --all' von Lauf zu Lauf, und ein Skript,
        # das sie liest, hinge am Zufall.
        with Runtime(settings, sources_file=self._datei(tmp_path, 4, 4)) as laufzeit:
            assert [item.name for item in laufzeit.registered] == [
                f"quelle-{i}" for i in range(4)
            ]

    def test_die_grenze_kommt_aus_der_konfiguration(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Kein Literal im Code (§6.1 Regel 1)."""
        with Runtime(settings, sources_file=self._datei(tmp_path, 2, 3)) as laufzeit:
            assert laufzeit.sources.max_concurrency == 3

    def test_ohne_angabe_bleibt_es_nacheinander(self, settings: Settings, tmp_path: Path) -> None:
        pfad = tmp_path / "ohne.yaml"
        pfad.write_text(yaml.safe_dump({"sources": []}), encoding="utf-8")

        with Runtime(settings, sources_file=pfad) as laufzeit:
            assert laufzeit.sources.max_concurrency == 1
