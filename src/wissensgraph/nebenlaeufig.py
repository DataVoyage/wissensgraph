"""Nebenläufige Modellfragen — das Muster, das mehrere Dienste teilen (§11.2).

Zwei Läufe stellen viele voneinander unabhängige Modellfragen: die Kantenerkennung (§14.2) und
die Waisen-Anbindung (§15.3). Beide warteten dabei fast die ganze Zeit auf das Netz — gemessen
1,26 Anfragen je Sekunde bei 824 ms Antwortzeit, also achtundneunzig Prozent Leerlauf.

Hier steht der geteilte Teil, damit er nicht zweimal geschrieben wird und vor allem nicht
zweimal *anders*. Die Regel, die dabei zählt, ist keine Zeile Code, sondern eine Einteilung:

**Gefragt wird nebenläufig, verbucht wird der Reihe nach.** Die Fragen sind unabhängig — jede
ist eine eigene HTTP-Anfrage, keine liest, was eine andere schreibt. Alles danach ist es nicht:
Berichte zählen, Kanten entstehen, Budgets werden geführt. Zwei Threads auf denselben Zählern
wären ein Wettlauf, und der teure Teil ist ohnehin das Warten.

Threads und nicht asyncio, aus demselben Grund wie im Router: Der ganze Weg darunter —
LangChain, SQLAlchemy, psycopg — ist synchron; ein Wechsel färbte bis in die Repositories durch,
und gewonnen wäre nichts, weil beim Warten auf das Netz ohnehin der GIL frei wird.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from wissensgraph.config import defaults

#: Wie viele Aufgaben je Arbeiter in einen Block gehen. Der Block bestimmt, wie oft die Daten
#: aus der Datenbank geholt werden: zu klein, und es sind wieder viele Roundtrips; zu groß, und
#: der Speicher hält Texte vor, die längst abgearbeitet sind. Vier ist ein Kompromiss ohne
#: Anspruch auf Optimalität — die Größenordnung stimmt, und die Zahl steht an einer Stelle.
BLOCK_JE_ARBEITER = 4


def bloecke[Eingabe](aufgaben: Sequence[Eingabe], groesse: int) -> Iterator[Sequence[Eingabe]]:
    """Zerlegt eine Aufgabenliste in Blöcke — die Einheit, in der geladen und gefragt wird."""
    schritt = max(1, groesse)
    for anfang in range(0, len(aufgaben), schritt):
        yield aufgaben[anfang : anfang + schritt]


def parallel[Eingabe, Ergebnis](
    aufgaben: Sequence[Eingabe],
    arbeit: Callable[[Eingabe], Ergebnis],
    *,
    gleichzeitig: int,
) -> list[Ergebnis]:
    """Führt ``arbeit`` über alle ``aufgaben`` aus und gibt die Ergebnisse in Eingabereihenfolge.

    Bei ``gleichzeitig <= 1`` entsteht kein Pool und der Ablauf ist der sequenzielle — wer
    nichts konfiguriert, bekommt das bisherige Verhalten.

    ``map`` und nicht ``submit``: Es hält die Reihenfolge und wirft beim Einsammeln die erste
    Ausnahme. Ein erschöpftes Budget bricht damit beim Aufrufer ab und nicht irgendwo im
    Hintergrund, wo niemand es sähe.
    """
    if gleichzeitig > 1 and len(aufgaben) > 1:
        with ThreadPoolExecutor(
            max_workers=min(gleichzeitig, len(aufgaben)),
            thread_name_prefix=defaults.MODEL_WORKER_PREFIX,
        ) as pool:
            return list(pool.map(arbeit, aufgaben))
    return [arbeit(aufgabe) for aufgabe in aufgaben]


#: Das Ende des Stroms — ein eigenes Objekt, weil ``None`` ein gültiges Element sein könnte.
_ENDE = object()


@contextmanager
def vorauslesen[Element](
    quelle: Iterable[Element], *, tiefe: int, name: str = defaults.SOURCE_WORKER_PREFIX
) -> Iterator[Iterator[Element]]:
    """Liest ``quelle`` in einem eigenen Thread voraus und gibt die Elemente in Reihenfolge weiter.

    Das Gegenstück zu :func:`parallel` für die Quellenseite. Dort ist jede Frage unabhängig und
    lässt sich vervielfachen; hier geht das gerade nicht: Eine Quelle wird geblättert, und die
    nächste Seite hängt am Ergebnis der vorigen (§8.2 Regel 1). Was sich trennen lässt, sind die
    beiden *Arten* von Arbeit — das Warten auf das Quellsystem und das Schreiben in die
    Datenbank. Bisher wechselten sie sich strikt ab: Seite holen, Seite verbuchen, Seite holen.
    Mit einem Vorlauf holt die Quelle die nächsten Dokumente, während der Kern die vorigen noch
    schreibt.

    ``tiefe`` ist die Zahl der Dokumente, die vorlaufen dürfen. Sie ist die Bremse, ohne die der
    Vorlauf einen Bestand von hunderttausend Seiten in den Speicher zöge — genau das, was §8.2
    Regel 1 verbietet. Bei ``tiefe <= 0`` entsteht kein Thread und der Ablauf ist der bisherige.

    Als Kontextmanager, damit der Thread auch dann endet, wenn der Verbraucher abbricht: Ein
    Vorlauf, der nach einer Ausnahme weiterliest, hielte den Prozess offen und schriebe Anfragen
    an ein Quellsystem, deren Antwort niemand mehr will.

    Eine Ausnahme aus der Quelle wird über den Puffer weitergereicht und beim Verbraucher
    geworfen — an derselben Stelle, an der sie ohne Vorlauf entstanden wäre. Das ist wichtig,
    weil ``SyncService`` daran den Cursor festmacht: Ein Netzwerkfehler mitten in der Iteration
    darf ihn nicht fortschreiben (§22.3).
    """
    if tiefe <= 0:
        yield iter(quelle)
        return

    puffer: queue.Queue[object] = queue.Queue(maxsize=tiefe)
    schluss = threading.Event()

    def lesen() -> None:
        try:
            for element in quelle:
                # Mit Zeitlimit, damit ein abgebrochener Verbraucher den Thread nicht auf einem
                # vollen Puffer stehen lässt: Er soll das Schlusssignal auch dann bemerken.
                while not schluss.is_set():
                    try:
                        puffer.put(element, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if schluss.is_set():
                    return
        except BaseException as exc:
            puffer.put(exc)
        else:
            puffer.put(_ENDE)

    leser = threading.Thread(target=lesen, name=f"{name}-vorlauf", daemon=True)
    leser.start()

    def ausgeben() -> Iterator[Element]:
        while True:
            element = puffer.get()
            if element is _ENDE:
                return
            if isinstance(element, BaseException):
                raise element
            yield element  # type: ignore[misc]

    try:
        yield ausgeben()
    finally:
        schluss.set()
        # Den Puffer leeren, damit ein blockierter Leser wieder ans Schlusssignal kommt.
        while leser.is_alive():
            try:
                puffer.get_nowait()
            except queue.Empty:
                leser.join(timeout=0.1)
