"""Der geteilte Baustein für Nebenläufigkeit (§11.2, §8.2).

Zwei Formen, weil es zwei Arten von Wartezeit gibt. ``parallel`` vervielfacht *unabhängige*
Fragen — jede Modellfrage ist eine eigene HTTP-Anfrage, keine liest, was eine andere schreibt.
``vorauslesen`` kann das gerade nicht, weil eine Quelle geblättert wird und die nächste Seite am
Ergebnis der vorigen hängt; dort lässt sich nur *Holen gegen Verarbeiten* trennen.

Was beide teilen, ist die Regel, um die es eigentlich geht: Reihenfolge und Fehler dürfen sich
durch die Nebenläufigkeit nicht ändern. Ein Lauf, der bei zwei gleichzeitigen Anfragen andere
Ergebnisse liefert als bei einer, wäre kein schnellerer Lauf, sondern ein anderer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from wissensgraph.nebenlaeufig import BLOCK_JE_ARBEITER, bloecke, parallel, vorauslesen


class TestParallel:
    def test_haelt_die_reihenfolge_der_eingabe(self) -> None:
        # Ohne diese Zusicherung wäre jede Auswertung eines Laufs Glückssache: Die Aufrufer
        # setzen Ergebnis und Aufgabe über den Index zusammen.
        aufgaben = list(range(20))

        ergebnis = parallel(aufgaben, lambda zahl: zahl * 2, gleichzeitig=8)

        assert ergebnis == [zahl * 2 for zahl in aufgaben]

    def test_laeuft_wirklich_gleichzeitig(self) -> None:
        # Der Punkt der Übung. Acht Aufgaben zu je 50 ms brauchen nacheinander 400 ms; mit acht
        # Arbeitern deutlich weniger. Die Schranke ist bewusst großzügig — geprüft wird die
        # Größenordnung, nicht die Maschine.
        beginn = time.monotonic()
        parallel(list(range(8)), lambda _: time.sleep(0.05), gleichzeitig=8)

        assert time.monotonic() - beginn < 0.3

    def test_bleibt_ohne_nebenlaeufigkeit_im_selben_thread(self) -> None:
        # ``gleichzeitig=1`` soll den bisherigen Ablauf ergeben und keinen Pool anlegen: Wer
        # nichts konfiguriert, bekommt das alte Verhalten — samt seiner Stacktraces.
        threads: set[int] = set()

        parallel([1, 2, 3], lambda _: threads.add(threading.get_ident()), gleichzeitig=1)

        assert threads == {threading.get_ident()}

    def test_reicht_die_erste_ausnahme_an_den_aufrufer(self) -> None:
        # Ein erschöpftes Budget muss beim Aufrufer ankommen (§24 Stufe 7) und nicht in einem
        # Hintergrundthread verschwinden, wo niemand es sähe.
        def arbeit(zahl: int) -> int:
            if zahl == 3:
                raise RuntimeError("Budget erschöpft")
            return zahl

        with pytest.raises(RuntimeError, match="Budget"):
            parallel(list(range(10)), arbeit, gleichzeitig=4)

    def test_zerlegt_in_bloecke_ohne_etwas_zu_verlieren(self) -> None:
        aufgaben = list(range(10))

        geteilt = list(bloecke(aufgaben, 4))

        assert [list(block) for block in geteilt] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
        assert BLOCK_JE_ARBEITER >= 1


class TestVorauslesen:
    def test_gibt_alles_in_reihenfolge_weiter(self) -> None:
        with vorauslesen(iter(range(50)), tiefe=8) as strom:
            assert list(strom) == list(range(50))

    def test_laeuft_vor_waehrend_der_verbraucher_arbeitet(self) -> None:
        # Der eigentliche Zweck: Die Quelle holt weiter, während der Kern schreibt. Gemessen
        # wird das am Fortschritt des Erzeugers, nicht an einer Uhr — eine Zusicherung über
        # Zeit wäre auf einer belasteten Maschine eine Zusicherung über das Wetter.
        geholt: list[int] = []

        def quelle() -> Iterator[int]:
            for zahl in range(10):
                geholt.append(zahl)
                yield zahl

        with vorauslesen(quelle(), tiefe=5) as strom:
            erstes = next(iter(strom))
            # Dem Erzeuger einen Moment geben, den Puffer zu füllen.
            for _ in range(50):
                if len(geholt) > 1:
                    break
                time.sleep(0.01)

        assert erstes == 0
        assert len(geholt) > 1, "der Erzeuger ist dem Verbraucher nicht vorausgelaufen"

    def test_deckelt_den_vorlauf(self) -> None:
        # Ohne Deckel zöge der Vorlauf einen Bestand von hunderttausend Seiten in den Speicher —
        # genau das, was §8.2 Regel 1 verbietet.
        geholt: list[int] = []

        def quelle() -> Iterator[int]:
            for zahl in range(1000):
                geholt.append(zahl)
                yield zahl

        with vorauslesen(quelle(), tiefe=4) as strom:
            next(iter(strom))
            time.sleep(0.15)
            # Puffer (4) + das entnommene + eines in der Hand des Erzeugers. Großzügig gefasst,
            # aber weit unter tausend: Geprüft wird, dass überhaupt gedeckelt wird.
            assert len(geholt) <= 12

    def test_wirft_die_ausnahme_der_quelle_beim_verbraucher(self) -> None:
        # Entscheidend für §22.3: Ein Netzwerkfehler mitten in der Iteration darf den Cursor
        # nicht fortschreiben. Dafür muss er an derselben Stelle ankommen wie ohne Vorlauf.
        def quelle() -> Iterator[int]:
            yield 1
            raise ConnectionError("Quelle weg")

        with pytest.raises(ConnectionError, match="Quelle weg"), vorauslesen(
            quelle(), tiefe=4
        ) as strom:
            list(strom)

    def test_beendet_den_leser_wenn_der_verbraucher_abbricht(self) -> None:
        # Ein Vorlauf, der nach einem Abbruch weiterliest, hielte den Prozess offen und fragte
        # ein Quellsystem nach Antworten, die niemand mehr will.
        laeuft = threading.Event()

        def quelle() -> Iterator[int]:
            try:
                for zahl in range(100_000):
                    laeuft.set()
                    yield zahl
            finally:
                laeuft.clear()

        vorher = threading.active_count()
        with vorauslesen(quelle(), tiefe=4) as strom:
            next(iter(strom))
            assert laeuft.wait(timeout=1.0)

        for _ in range(100):
            if threading.active_count() <= vorher:
                break
            time.sleep(0.01)
        assert threading.active_count() <= vorher

    def test_bleibt_ohne_tiefe_ein_gewoehnlicher_iterator(self) -> None:
        # ``tiefe=0`` ist die Vorgabe: Wer nichts konfiguriert, bekommt den bisherigen Ablauf
        # — ohne Thread, ohne Puffer, mit denselben Stacktraces.
        threads: set[int] = set()

        def quelle() -> Iterator[int]:
            for zahl in range(5):
                threads.add(threading.get_ident())
                yield zahl

        with vorauslesen(quelle(), tiefe=0) as strom:
            assert list(strom) == list(range(5))

        assert threads == {threading.get_ident()}
