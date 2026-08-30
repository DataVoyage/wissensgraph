"""Ports des Modellzugriffs (§11.2, §4.2).

Die Schnittstelle, die §11.1 verlangt: "Jeder Zugriff auf ein Sprach- oder Embedding-Modell läuft
über genau eine Komponente. Kein Service kennt einen Anbieter, ein Modellnamen oder ein SDK."

Zwei Ebenen, und der Unterschied zwischen ihnen ist der Kern dieser Stufe:

* :class:`ModelRouter` ist, was die Dienste sehen. Sie nennen eine **Aufgabe** und einen
  **Store** — nie ein Modell. Das ist die Regel, die den Router wirksam macht (§11.2).
* :class:`ChatClient` und :class:`EmbeddingClient` sind, was ein Anbieter erfüllt. Sie kennen
  weder Aufgaben noch Stores, sondern nur einen Aufruf und seine Antwort. Dazwischen liegt
  alles, was §11.6 aufzählt: Policy, Budget, Cache, Retries, Fallback, Protokollierung.

Diese Trennung ist der Grund, warum der Router mit LangChain arbeiten kann, ohne dass irgendein
Dienst davon weiß: LangChain erfüllt die untere Ebene, und die obere ist davon unberührt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

from wissensgraph.config import defaults


class ModelError(RuntimeError):
    """Ein Modellaufruf ist endgültig gescheitert — alle Routen der Kette erschöpft."""


class InvalidModelOutputError(ModelError):
    """Die Antwort ließ sich auch nach dem Reparaturversuch nicht gegen das Schema validieren.

    §11.6 sieht **genau einen** Reparaturversuch vor: "bei Fehlschlag ein Reparaturversuch mit der
    Fehlermeldung, dann ``invalid_output``". Mehr wäre ein Automatismus, der bei einem Modell, das
    die Aufgabe nicht kann, beliebig lange Token verbrennt.
    """


class BudgetExceededError(ModelError):
    """Der Budgetrahmen des Laufs ist erschöpft (§11.6).

    Kein Fehler im engeren Sinn, sondern eine Grenze, die gegriffen hat. §24 verlangt für Stufe 7,
    dass ein Budgetüberschritt "den Lauf sauber mit Teilergebnis" beendet — die Ausnahme wird
    deshalb vom Lauf gefangen und in eine Statistik übersetzt, nicht nach oben durchgereicht.
    """

    def __init__(self, *, grund: str, calls: int, cost_eur: float) -> None:
        self.calls = calls
        self.cost_eur = cost_eur
        super().__init__(
            f"Budget erschöpft: {grund} (bisher {calls} Aufruf(e), geschätzt "
            f"{cost_eur:.4f} EUR). Die Grenzen stehen unter 'budget' in wissensgraph.yaml (§11.6)."
        )


@dataclass(frozen=True)
class Usage:
    """Verbrauch eines Modellaufrufs — die Zahlen, die in ``model_calls`` landen (§7.4)."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_eur: float = 0.0
    latency_ms: int = 0

    def __add__(self, other: Usage) -> Usage:
        """Summiert zwei Verbräuche — für einen Aufruf, der in mehrere Batches zerfiel."""
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            cost_estimate_eur=self.cost_estimate_eur + other.cost_estimate_eur,
            latency_ms=self.latency_ms + other.latency_ms,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für Lauf-Statistiken und Logeinträge."""
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_estimate_eur": round(self.cost_estimate_eur, 6),
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class PromptSpec:
    """Ein Prompt in der Form, die der Router entgegennimmt (§11.2).

    Zwei Felder statt eines Strings, weil die Trennung für den Cache zählt: Der Schlüssel geht
    über beide, und eine geänderte Anweisung soll den Zwischenspeicher genauso ungültig machen wie
    ein geänderter Inhalt. Ein zusammengeklebter String könnte das nicht unterscheiden.
    """

    system: str | None
    user: str

    def normalized(self) -> str:
        """Die Form, aus der der Cache-Schlüssel gebildet wird (§11.6).

        Normalisiert wird nur der Leerraum an den Rändern. Weiter zu gehen — etwa Zeilenumbrüche
        zu vereinheitlichen — wäre gefährlich: Ein Prompt, dessen Zeilenstruktur die Bedeutung
        trägt, bekäme sonst den Treffer eines anderen.
        """
        return f"{(self.system or '').strip()}\n\x1e\n{self.user.strip()}"


@dataclass(frozen=True)
class ResolvedRoute:
    """Welches Modell für eine Aufgabe greifen würde — die Antwort von ``describe`` (§11.2)."""

    task: str
    provider: str
    model: str
    local: bool
    dim: int | None = None
    temperature: float | None = None
    batch_size: int = defaults.MODEL_BATCH_SIZE
    fallbacks: tuple[str, ...] = ()
    configured: bool = True

    @property
    def model_key(self) -> str:
        """``<provider>:<model>`` — die Kennung in ``concept_embeddings`` und ``model_calls``."""
        return f"{self.provider}:{self.model}"

    @property
    def generated_by(self) -> str:
        """Die Provenienz-Kennung erzeugter Datensätze (§11.6).

        ``"<provider>:<model>/<task>@v<router-version>"``. Alle vier Bestandteile sind nötig, um
        eine erzeugte Kante später zu beurteilen: Wer hat sie gemacht, mit welchem Modell, für
        welche Frage, und nach welchen Regeln des Routers.
        """
        return f"{self.model_key}/{self.task}@v{defaults.ROUTER_VERSION}"

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für ``wg models describe`` und die spätere API."""
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "model_key": self.model_key,
            "local": self.local,
            "dim": self.dim,
            "temperature": self.temperature,
            "batch_size": self.batch_size,
            "fallbacks": list(self.fallbacks),
            "configured": self.configured,
            "generated_by": self.generated_by,
        }


@dataclass(frozen=True)
class EmbeddingResult:
    """Das Ergebnis eines Embedding-Aufrufs (§11.2)."""

    vectors: tuple[tuple[float, ...], ...]
    model_key: str
    dim: int
    cached: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class CompletionResult:
    """Das Ergebnis eines generativen Aufrufs (§11.2).

    ``parsed`` ist bereits gegen das übergebene Pydantic-Schema validiert. Ein Aufrufer bekommt
    entweder ein gültiges Objekt oder eine Ausnahme — nie einen halb verstandenen String, den er
    selbst noch einmal prüfen müsste.
    """

    parsed: BaseModel | None
    raw: str
    model_key: str
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    cached: bool = False


@dataclass(frozen=True)
class ModelCall:
    """Ein Eintrag der Tabelle ``model_calls`` (§7.4).

    Er wird auch dann geschrieben, wenn gar kein Aufruf hinausging — bei einem Cache-Treffer, bei
    einem Policy-Verstoß und bei erschöpftem Budget. Genau das macht die Tabelle auswertbar:
    ``wg models usage`` beantwortet sonst nur, was Geld gekostet hat, und nicht, was verhindert
    wurde.
    """

    task: str
    provider: str
    model: str
    status: str
    store: str | None = None
    run_id: UUID | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cost_estimate: float | None = None
    cache_hit: bool = False
    attempt: int = 1
    created_at: datetime | None = None


@dataclass(frozen=True)
class UsageSummary:
    """Die Auswertung von ``model_calls`` für ``wg models usage`` (§24, Stufe 7)."""

    task: str
    provider: str
    model: str
    calls: int
    cache_hits: int
    tokens_in: int
    tokens_out: int
    cost_estimate_eur: float
    failures: int

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für CLI und API."""
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_estimate_eur": round(self.cost_estimate_eur, 6),
            "failures": self.failures,
        }


@dataclass(frozen=True)
class RawCompletion:
    """Was ein Anbieter zurückgibt, bevor der Router etwas daraus macht."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class RawEmbedding:
    """Was ein Anbieter für einen Stapel Texte zurückgibt."""

    vectors: tuple[tuple[float, ...], ...]
    tokens_in: int = 0


class ChatClient(Protocol):
    """Ein generatives Modell in seiner einfachsten Form — Text hinein, Text heraus.

    Bewusst ohne Schema-Parameter. Die strukturierte Ausgabe samt Reparaturversuch ist Sache des
    Routers (§11.6) und soll an genau einer Stelle passieren; ein Anbieter, der sie selbst
    beherrscht, würde sie anders handhaben als einer, der es nicht tut, und der Unterschied wäre
    von außen nicht mehr zu sehen.
    """

    def complete(self, prompt: PromptSpec) -> RawCompletion:
        """Ruft das Modell auf.

        Raises:
            ModelError: Bei jedem Fehler des Anbieters. Ob erneut versucht wird, entscheidet der
                Router — nicht der Anbieter.
        """


class EmbeddingClient(Protocol):
    """Ein Embedding-Modell für einen Stapel Texte."""

    def embed(self, texts: Sequence[str]) -> RawEmbedding:
        """Bettet die Texte ein; die Reihenfolge der Vektoren entspricht der der Texte.

        Raises:
            ModelError: Bei jedem Fehler des Anbieters.
        """


class ModelClientFactory(Protocol):
    """Baut Anbieter-Clients zu einer aufgelösten Route.

    Der einzige Ort im System, an dem ein SDK entsteht. Er ist ein Port und keine Klasse, damit
    Tests ohne Netz, ohne Schlüssel und ohne Wartezeit dieselbe Router-Logik durchlaufen wie der
    Betrieb (§22).
    """

    def chat(self, task: str, route: object) -> ChatClient:
        """Ein generatives Modell für diese Route."""

    def embeddings(self, task: str, route: object) -> EmbeddingClient:
        """Ein Embedding-Modell für diese Route."""


class ResponseCache(Protocol):
    """Der Zwischenspeicher der Modellantworten (§11.6).

    Der Schlüssel wird vom Router gebildet (SHA-256 über ``task``, ``model_key`` und den
    normalisierten Prompt) — der Cache selbst kennt weder Aufgabe noch Modell und ist damit
    austauschbar gegen alles, was Zeichenketten unter Zeichenketten ablegen kann.
    """

    def get(self, key: str) -> str | None:
        """Der abgelegte Wert, oder ``None``."""

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Legt einen Wert mit Verfallszeit ab.

        Ein Fehlschlag ist kein Fehler des Aufrufs: Ein nicht erreichbarer Zwischenspeicher macht
        das System langsamer, nicht falsch. Umsetzungen schlucken deshalb ihre eigenen Fehler.
        """


@runtime_checkable
class ModelCallRepository(Protocol):
    """Die Modellaufrufe genau eines Stores (§7.4).

    Verbucht wird dort, wo der Inhalt herkommt. Ein Aufruf über persönliche Notizen hinterlässt
    damit keine Zeile im geteilten Store — auch nicht in der Abrechnung (Leitprinzip 2).
    """

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def record(self, call: ModelCall) -> None:
        """Hängt einen Aufruf an."""

    def usage(
        self, *, run_id: UUID | None = None, limit: int = defaults.MODEL_USAGE_LIMIT
    ) -> tuple[UsageSummary, ...]:
        """Die Auswertung, gruppiert nach Aufgabe und Modell, teuerste zuerst."""

    def spent(self, run_id: UUID) -> tuple[int, float]:
        """Aufrufzahl und geschätzte Kosten eines Laufs — die Eingabe des Budget-Wächters.

        Sie kommt aus der Datenbank und nicht aus einem Zähler im Speicher, weil ein Lauf über
        mehrere Prozesse verteilt sein kann: Der Worker führt ihn aus, die API zeigt ihn an, und
        ein Wiederanlauf nach einem Absturz setzt an derselben Zeile fort.
        """


class ModelRouter(Protocol):
    """Die Schnittstelle aus §11.2 — das Einzige, was ein Dienst vom Modellzugriff sieht."""

    def embed(
        self, task: str, texts: Sequence[str], *, store: str, run_id: UUID | None = None
    ) -> EmbeddingResult:
        """Bettet Texte ein.

        Raises:
            ProviderNotAllowedError: Wenn die Store-Policy den Aufruf verbietet (§11.5).
            BudgetExceededError: Wenn der Rahmen des Laufs erschöpft ist (§11.6).
            ModelError: Wenn alle Routen der Kette gescheitert sind.
        """

    def complete(
        self,
        task: str,
        *,
        prompt: PromptSpec,
        schema: type[BaseModel] | None = None,
        store: str,
        run_id: UUID | None = None,
    ) -> CompletionResult:
        """Ruft ein generatives Modell auf und validiert die Antwort gegen ``schema``.

        Raises:
            ProviderNotAllowedError: Wenn die Store-Policy den Aufruf verbietet (§11.5).
            BudgetExceededError: Wenn der Rahmen des Laufs erschöpft ist (§11.6).
            InvalidModelOutputError: Wenn auch der Reparaturversuch kein gültiges Objekt ergab.
            ModelError: Wenn alle Routen der Kette gescheitert sind.
        """

    def describe(self, task: str) -> ResolvedRoute:
        """Welches Modell für diese Aufgabe greifen würde — ohne es aufzurufen."""
