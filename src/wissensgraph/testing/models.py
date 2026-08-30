"""Ein Fake-Provider für Tests und Trockenläufe (§24, Stufe 7; §22.2).

Er gehört ins ausgelieferte Paket und nicht in ``tests/``, aus demselben Grund wie die
Contract-Suite der Quell-Adapter: Er ist ein Werkzeug des Systems, kein Detail seiner
Testsammlung. Wer den Router in einer Umgebung ohne Zugangsdaten ausprobieren will, stellt ihn in
``models.yaml`` ein und bekommt einen vollständigen, aber kostenlosen Lauf.

**Das Embedding ist nicht zufällig, sondern lexikalisch.** Ein Hash über den ganzen Text ergäbe
Vektoren ohne jede Beziehung zueinander — damit ließe sich zwar prüfen, ob Zahlen ankommen, aber
nichts über Clustering, Nachbarschaft oder Ähnlichkeitsschwellen aussagen. Stattdessen wird jedes
Wort in einen Eimer gehasht und der Vektor normiert: Zwei Texte über dasselbe Thema landen dann
wirklich nahe beieinander, und ein Test über §13.2 prüft die Cluster-Bildung statt seiner eigenen
Vorbereitung.

Was er ausdrücklich nicht ist: ein Ersatz für ein Sprachmodell. Seine generativen Antworten
kommen aus einem Skript, das der Test vorgibt.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence

from wissensgraph.config.models import RouteConfig
from wissensgraph.ports.models import (
    ChatClient,
    EmbeddingClient,
    ModelError,
    PromptSpec,
    RawCompletion,
    RawEmbedding,
)

#: Wortgrenzen des lexikalischen Embeddings. Unicode-Wortzeichen, damit Umlaute nicht mitten im
#: Wort trennen — ein Fake, der 'Faktentabelle' und 'Faktentabellen' für unähnlich hielte, wäre
#: als Prüfstein für Ähnlichkeitsschwellen wertlos.
_WORT = re.compile(r"\w+", re.UNICODE)


class FakeEmbeddings:
    """Ein deterministisches, lexikalisches Embedding ohne Netzwerk."""

    def __init__(self, dim: int) -> None:
        if dim < 1:  # pragma: no cover — von der Konfiguration bereits ausgeschlossen
            raise ValueError("Die Dimension muss mindestens 1 sein.")
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> RawEmbedding:
        """Bettet Texte ein: Wort-Hashing in Eimer, danach auf Länge 1 normiert."""
        return RawEmbedding(
            vectors=tuple(self.vector(text) for text in texts),
            tokens_in=sum(len(text) for text in texts) // 4,
        )

    def vector(self, text: str) -> tuple[float, ...]:
        """Der Vektor eines einzelnen Textes.

        Öffentlich, weil Tests damit Erwartungen aufstellen: Wer prüfen will, dass ein Knoten in
        einem bestimmten Cluster landet, muss die Ähnlichkeit ausrechnen können, ohne den Lauf zu
        starten.
        """
        eimer = [0.0] * self._dim
        for wort in _WORT.findall(text.lower()):
            verdaut = hashlib.sha256(wort.encode()).digest()
            index = int.from_bytes(verdaut[:4], "big") % self._dim
            # Das Vorzeichen aus einem weiteren Byte: Ohne es wären alle Vektoren im positiven
            # Orthanten und damit paarweise ähnlich, egal worum es geht.
            eimer[index] += 1.0 if verdaut[4] % 2 == 0 else -1.0
        laenge = math.sqrt(sum(wert * wert for wert in eimer))
        if laenge == 0.0:
            # Ein Text ohne Wörter bekommt einen festen Einheitsvektor. Ein Nullvektor wäre in
            # der Kosinusähnlichkeit undefiniert und brächte den HNSW-Index in Verlegenheit.
            eimer[0] = 1.0
            return tuple(eimer)
        return tuple(wert / laenge for wert in eimer)


class ScriptedChat:
    """Ein generatives Modell, dessen Antworten der Test vorgibt.

    Die Antworten kommen als Funktion und nicht als Liste, damit ein Test auf den *Prompt*
    reagieren kann. Genau das braucht §14: Die Kantenerkennung stellt je Paar eine andere Frage,
    und die erwartete Mehrheitsantwort ist "keine Beziehung" — ein Skript aus einer festen Reihe
    könnte das nicht abbilden, ohne die Reihenfolge der Paare vorwegzunehmen.
    """

    def __init__(
        self,
        antwort: Callable[[PromptSpec], str] | str,
        *,
        fehler_bei: int | None = None,
    ) -> None:
        """
        Args:
            antwort: Die Antwort, oder eine Funktion, die sie aus dem Prompt bestimmt.
            fehler_bei: Beim wievielten Aufruf eine Anbieter-Ausnahme geworfen wird — für Tests
                der Wiederholungs- und Fallback-Logik aus §11.6.
        """
        self._antwort = antwort if callable(antwort) else (lambda _: str(antwort))
        self._fehler_bei = fehler_bei
        self.calls: list[PromptSpec] = []

    def complete(self, prompt: PromptSpec) -> RawCompletion:
        """Liefert die skriptgemäße Antwort."""
        self.calls.append(prompt)
        if self._fehler_bei is not None and len(self.calls) == self._fehler_bei:
            raise ModelError("Der Fake-Provider war für diesen Versuch absichtlich nicht bereit.")
        text = self._antwort(prompt)
        return RawCompletion(
            text=text,
            tokens_in=len(prompt.normalized()) // 4,
            tokens_out=len(text) // 4,
        )


class FakeClients:
    """Die Client-Fabrik zum Fake-Provider — erfüllt ``ModelClientFactory`` (§11.2)."""

    def __init__(
        self,
        *,
        dim: int,
        chat: Callable[[PromptSpec], str] | str = "{}",
        fehler_bei: int | None = None,
    ) -> None:
        self._dim = dim
        self.embeddings_client = FakeEmbeddings(dim)
        self.chat_client = ScriptedChat(chat, fehler_bei=fehler_bei)

    def chat(self, task: str, route: object) -> ChatClient:
        """Das skriptgesteuerte Chatmodell — für jede Aufgabe dasselbe."""
        return self.chat_client

    def embeddings(self, task: str, route: object) -> EmbeddingClient:
        """Das lexikalische Embedding, gegebenenfalls mit der Dimension der Route."""
        gewuenscht = getattr(route, "dim", None) if isinstance(route, RouteConfig) else None
        if gewuenscht is not None and gewuenscht != self._dim:
            return FakeEmbeddings(gewuenscht)
        return self.embeddings_client


__all__ = ["FakeClients", "FakeEmbeddings", "ScriptedChat"]
