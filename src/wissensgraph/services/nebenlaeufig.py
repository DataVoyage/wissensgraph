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

from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor

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
