"""Der Model-Router (§11).

Die eine Stelle, an der ein Modell aufgerufen wird. Alles, was §11.6 als Verhalten auflistet —
Policy, Budget, Cache, Retries, Fallback, strukturierte Ausgabe, Protokollierung — passiert hier
und nirgends sonst. Ein Dienst, der etwas vom Modell will, nennt eine Aufgabe und einen Store.

**Die Reihenfolge der Prüfungen ist nicht beliebig.** Sie lautet: Policy, dann Cache, dann Budget,
dann Aufruf.

* Die Policy zuerst, weil sie die einzige Prüfung ist, deren Verletzung nicht rückgängig zu machen
  wäre: Ein Inhalt, der den Rechner verlassen hat, ist draußen (§11.5).
* Der Cache vor dem Budget, weil ein Treffer nichts kostet. Ein Wächter, der Zwischenspeicher
  mitzählte, brächte einen Wiederholungslauf zum Erliegen, obwohl er nichts verbraucht.
* Das Budget zuletzt und **vor** dem Aufruf. Eine Grenze, die nach der Antwort greift, hat sie
  schon bezahlt.

**Ein Policy-Verstoß führt nie zu einem Fallback.** §11.5 ist an dieser Stelle ausdrücklich: nie
"ein stiller Fallback auf einen erlaubten, aber schlechteren Anbieter". Wer persönliche Inhalte
nicht an die Cloud geben darf, soll nicht stattdessen ein schwächeres Ergebnis bekommen, das wie
ein normales aussieht — er soll erfahren, dass hier nichts passiert ist.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from wissensgraph.config import defaults
from wissensgraph.config.models import ModelsConfig, ProviderConfig, RouteConfig, TaskConfig
from wissensgraph.config.schema import Settings
from wissensgraph.domain.budget import BudgetGuard
from wissensgraph.domain.policies import check_allowed_providers, check_store_policy
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import (
    BudgetExceededError,
    ChatClient,
    CompletionResult,
    EmbeddingClient,
    EmbeddingResult,
    InvalidModelOutputError,
    ModelCall,
    ModelClientFactory,
    ModelError,
    PromptSpec,
    RawCompletion,
    RawEmbedding,
    ResolvedRoute,
    ResponseCache,
    Usage,
    UsageSummary,
)
from wissensgraph.ports.repositories import UnitOfWorkFactory

_log = get_logger(__name__)

#: Der Zusatz, mit dem ein Reparaturversuch angestoßen wird (§11.6). Er nennt die Fehlermeldung
#: des Validators und wiederholt die Erwartung — mehr braucht es nicht, und mehr Aufwand lohnt
#: sich nicht: Wenn ein Modell die Form beim zweiten Mal nicht trifft, trifft es sie auch beim
#: fünften nicht.
_REPARATUR = (
    "Die vorige Antwort war kein gültiges JSON für das erwartete Schema.\n"
    "Fehler: {fehler}\n"
    "Antworte ausschließlich mit einem JSON-Objekt nach diesem Schema, ohne Vorrede, ohne "
    "Code-Zaun:\n{schema}"
)


class NoRouteError(ModelError):
    """Keine Route der Kette war benutzbar — meist ein fehlender Zugangsschlüssel."""


class ModelRouterService:
    """Die Umsetzung von :class:`~wissensgraph.ports.models.ModelRouter` (§11.2)."""

    def __init__(
        self,
        settings: Settings,
        models: ModelsConfig,
        clients: ModelClientFactory,
        *,
        unit_of_work: UnitOfWorkFactory | None = None,
        cache: ResponseCache | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Kernkonfiguration; liefert Budget und die Ausnahme aus §11.5.
            models: Die geprüfte Router-Konfiguration (``models.yaml``).
            clients: Die Fabrik, die zu einer Route einen Anbieter-Client baut.
            unit_of_work: Fabrik für Transaktionen je Store — für ``model_calls``. Ohne Angabe
                wird nicht protokolliert; das ist der Zustand in Unit-Tests, nicht im Betrieb.
            cache: Der Zwischenspeicher aus §11.6. Ohne Angabe gibt es keinen — jeder Aufruf geht
                dann hinaus, was langsamer und teurer, aber nie falsch ist.
            clock: Zeitquelle.
            sleep: Wartefunktion zwischen zwei Versuchen; als Parameter, damit Tests keine
                echte Zeit verbrauchen.
        """
        self._settings = settings
        self._models = models
        self._clients = clients
        self._unit_of_work = unit_of_work
        self._cache = cache
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._guard = BudgetGuard(
            max_calls=settings.budget.max_model_calls_per_run,
            max_cost_eur=settings.budget.max_estimated_cost_per_run_eur,
            on_exceed=settings.budget.on_exceed,
        )
        # Verbrauch ohne Lauf. Ein Aufruf von der Kommandozeile hat keine ``run_id`` und damit
        # keine Zeile, aus der sich der Stand rekonstruieren ließe — ohne diesen Zähler wäre er
        # der einzige Weg am Wächter vorbei.
        self._lose_aufrufe = 0
        self._lose_kosten = 0.0

    # -- Beschreiben -------------------------------------------------------------

    def describe(self, task: str) -> ResolvedRoute:
        """Welches Modell für diese Aufgabe greifen würde (§11.2).

        Raises:
            UnknownTaskError: Wenn die Aufgabe nicht konfiguriert ist.
        """
        profil = self._models.task(task)
        return self._aufgeloest(task, profil.primary, profil)

    def routes(self) -> tuple[ResolvedRoute, ...]:
        """Alle konfigurierten Aufgaben mit ihrem primären Modell — für ``wg models describe``."""
        return tuple(self.describe(name) for name in sorted(self._models.tasks))

    def usage(
        self, *, store: str, run_id: UUID | None = None, limit: int = defaults.MODEL_USAGE_LIMIT
    ) -> tuple[UsageSummary, ...]:
        """Die Auswertung von ``model_calls`` eines Stores — für ``wg models usage``."""
        if self._unit_of_work is None:
            return ()
        with self._unit_of_work(store) as uow:
            return uow.model_calls.usage(run_id=run_id, limit=limit)

    # -- Einbetten ---------------------------------------------------------------

    def embed(
        self, task: str, texts: Sequence[str], *, store: str, run_id: UUID | None = None
    ) -> EmbeddingResult:
        """Bettet Texte ein — mit Cache je Text und Bündelung nach ``batch_size`` (§11.6)."""
        if not texts:
            leer = self.describe(task)
            return EmbeddingResult(vectors=(), model_key=leer.model_key, dim=leer.dim or 0)

        letzter: ModelError | None = None
        for route, _ in self._kette(task, store=store):
            try:
                return self._einbetten(
                    task=task, route=route, texts=texts, store=store, run_id=run_id
                )
            except ModelError as exc:
                letzter = exc
                _log.warning(
                    "modell.route_gescheitert",
                    task=task,
                    model_key=route.model_key,
                    error=str(exc),
                )
        raise letzter or NoRouteError(_keine_route(task))

    # -- Erzeugen ----------------------------------------------------------------

    def complete(
        self,
        task: str,
        *,
        prompt: PromptSpec,
        schema: type[BaseModel] | None = None,
        store: str,
        run_id: UUID | None = None,
    ) -> CompletionResult:
        """Ruft ein generatives Modell auf und validiert die Antwort (§11.2, §11.6)."""
        letzter: ModelError | None = None
        for route, _ in self._kette(task, store=store):
            try:
                return self._erzeugen(
                    task=task,
                    route=route,
                    prompt=prompt,
                    schema=schema,
                    store=store,
                    run_id=run_id,
                )
            except InvalidModelOutputError:
                # Eine ungültige Ausgabe ist ein Ergebnis, kein Ausfall: Das Modell war erreichbar
                # und hat geantwortet. Ein Fallback würde dieselbe Frage einem anderen Modell
                # stellen — genau das, was §11.6 für einen erschöpften Primary vorsieht.
                raise
            except ModelError as exc:
                letzter = exc
                _log.warning(
                    "modell.route_gescheitert",
                    task=task,
                    model_key=route.model_key,
                    error=str(exc),
                )
        raise letzter or NoRouteError(_keine_route(task))

    # -- Kette und Policy --------------------------------------------------------

    def _kette(self, task: str, *, store: str) -> Iterator[tuple[RouteConfig, ProviderConfig]]:
        """Die Routen einer Aufgabe in ihrer Reihenfolge, jede erst nach der Policy-Prüfung.

        Die Prüfung steht hier und nicht im Aufrufer, weil sie für *jede* Route gilt: Ein Fallback,
        der die Grenze aus §11.5 verletzt, wäre der gefährlichste von allen — er greift erst,
        wenn schon etwas schiefgegangen ist, und niemand sieht hin.
        """
        profil = self._models.task(task)
        for route in profil.routes:
            provider = self._models.provider(route.provider)
            self._policy(task=task, route=route, provider=provider, store=store)
            yield route, provider

    def _policy(
        self, *, task: str, route: RouteConfig, provider: ProviderConfig, store: str
    ) -> None:
        """Die beiden Hälften von §11.5: Ortsregel und ausdrückliche Freigabeliste.

        Ein Verstoß hinterlässt einen ``model_calls``-Eintrag mit ``budget_denied`` — so verlangt
        es §11.5 wörtlich. Der Wert wirkt zunächst schief, ist aber richtig: Aus Sicht der
        Abrechnung ist ein Aufruf, der nicht stattfinden *darf*, derselbe Vorgang wie einer, der
        nicht mehr stattfinden *kann*. Beide erscheinen in ``wg models usage`` als verhinderte
        Aufrufe, und genau danach sucht, wer wissen will, warum ein Lauf wenig getan hat.
        """
        try:
            check_store_policy(
                store=store,
                provider=route.provider,
                provider_is_local=provider.local,
                allow_remote_personal=self._settings.personal_allow_remote_models,
            )
            check_allowed_providers(
                store=store,
                provider=route.provider,
                allowed=self._models.allowed_providers(store),
            )
        except Exception:
            self._protokollieren(
                ModelCall(
                    task=task,
                    provider=route.provider,
                    model=route.model,
                    status=defaults.MODEL_CALL_BUDGET_DENIED,
                    store=store,
                    run_id=None,
                ),
                store=store,
            )
            _log.warning(
                "modell.policy_verweigert", task=task, store=store, provider=route.provider
            )
            raise

        if store == defaults.STORE_PERSONAL and not provider.local:
            # Erreichbar nur mit WG_PERSONAL_ALLOW_REMOTE_MODELS=true. §11.5: "weicht die Regel
            # bewusst und protokolliert auf." Eine bewusste Ausnahme, die keine Spur hinterlässt,
            # wäre nach einer Woche von einem Versehen nicht mehr zu unterscheiden.
            _log.warning(
                "modell.personal_grenze_geoeffnet",
                task=task,
                provider=route.provider,
                model=route.model,
            )

    def _aufgeloest(self, task: str, route: RouteConfig, profil: TaskConfig) -> ResolvedRoute:
        """Übersetzt eine konfigurierte Route in die Auskunft, die ``describe`` liefert."""
        provider = self._models.provider(route.provider)
        return ResolvedRoute(
            task=task,
            provider=route.provider,
            model=route.model,
            local=provider.local,
            dim=route.dim,
            temperature=route.temperature,
            batch_size=route.batch_size,
            fallbacks=tuple(item.model_key for item in profil.fallback),
            configured=provider.is_configured,
            endpoint=provider.endpoint,
        )

    # -- Durchführung ------------------------------------------------------------

    def _einbetten(
        self,
        *,
        task: str,
        route: RouteConfig,
        texts: Sequence[str],
        store: str,
        run_id: UUID | None,
    ) -> EmbeddingResult:
        """Ein Embedding-Lauf über eine Route: Cache je Text, dann Bündel nach ``batch_size``."""
        vektoren: list[tuple[float, ...] | None] = []
        offen: list[int] = []
        for index, text in enumerate(texts):
            zwischenspeicher = _vektor_lesen(self._cache_lesen(task, route, text))
            vektoren.append(zwischenspeicher)
            if zwischenspeicher is None:
                offen.append(index)

        treffer = len(texts) - len(offen)
        verbrauch = Usage()
        client: EmbeddingClient | None = None

        for stapel in _stapel(offen, route.batch_size):
            if client is None:
                client = self._clients.embeddings(task, route)
            eingabe = [texts[index] for index in stapel]
            roh, teil = self._mit_wiederholung(
                task=task,
                route=route,
                store=store,
                run_id=run_id,
                aufruf=_Einbettung(client, eingabe),
            )
            verbrauch = verbrauch + teil
            for position, index in enumerate(stapel):
                vektor = tuple(float(zahl) for zahl in roh.vectors[position])
                vektoren[index] = vektor
                self._cache_schreiben(task, route, texts[index], json.dumps(vektor))

        fertig = tuple(vektor for vektor in vektoren if vektor is not None)
        if len(fertig) != len(texts):  # pragma: no cover — nur bei einem defekten Anbieter
            raise ModelError(
                f"Der Anbieter '{route.model_key}' lieferte {len(fertig)} Vektoren für "
                f"{len(texts)} Texte. Eine unvollständige Zuordnung wäre stillschweigend falsch."
            )

        dim = len(fertig[0]) if fertig else (route.dim or 0)
        return EmbeddingResult(
            vectors=fertig, model_key=route.model_key, dim=dim, cached=treffer, usage=verbrauch
        )

    def _erzeugen(
        self,
        *,
        task: str,
        route: RouteConfig,
        prompt: PromptSpec,
        schema: type[BaseModel] | None,
        store: str,
        run_id: UUID | None,
    ) -> CompletionResult:
        """Ein generativer Aufruf über eine Route, mit höchstens einem Reparaturversuch."""
        gespeichert = self._cache_lesen(task, route, prompt.normalized())
        if gespeichert is not None:
            geparst = _validieren(gespeichert, schema)
            if geparst is not None or schema is None:
                self._protokollieren(
                    ModelCall(
                        task=task,
                        provider=route.provider,
                        model=route.model,
                        status=defaults.MODEL_CALL_CACHE_HIT,
                        store=store,
                        run_id=run_id,
                        cache_hit=True,
                    ),
                    store=store,
                )
                return CompletionResult(
                    parsed=geparst, raw=gespeichert, model_key=route.model_key, cached=True
                )

        client = self._clients.chat(task, route)
        antwort, verbrauch = self._mit_wiederholung(
            task=task,
            route=route,
            store=store,
            run_id=run_id,
            aufruf=_Erzeugung(client, prompt),
        )

        geparst = _validieren(antwort.text, schema)
        if schema is None or geparst is not None:
            self._cache_schreiben(task, route, prompt.normalized(), antwort.text)
            return CompletionResult(
                parsed=geparst, raw=antwort.text, model_key=route.model_key, usage=verbrauch
            )

        # Genau ein Reparaturversuch (§11.6), mit der Fehlermeldung als Hinweis.
        fehler = _fehlertext(antwort.text, schema)
        nachfrage = PromptSpec(
            system=prompt.system,
            user=prompt.user
            + "\n\n"
            + _REPARATUR.format(fehler=fehler, schema=json.dumps(schema.model_json_schema())),
        )
        zweite, zweiter_verbrauch = self._mit_wiederholung(
            task=task,
            route=route,
            store=store,
            run_id=run_id,
            aufruf=_Erzeugung(client, nachfrage),
            attempt=2,
        )
        verbrauch = verbrauch + zweiter_verbrauch
        geparst = _validieren(zweite.text, schema)
        if geparst is None:
            self._protokollieren(
                ModelCall(
                    task=task,
                    provider=route.provider,
                    model=route.model,
                    status=defaults.MODEL_CALL_INVALID_OUTPUT,
                    store=store,
                    run_id=run_id,
                    attempt=2,
                ),
                store=store,
            )
            raise InvalidModelOutputError(
                f"'{route.model_key}' lieferte für '{task}' auch nach dem Reparaturversuch keine "
                f"gegen das Schema gültige Antwort: {fehler}"
            )

        self._cache_schreiben(task, route, prompt.normalized(), zweite.text)
        return CompletionResult(
            parsed=geparst,
            raw=zweite.text,
            model_key=route.model_key,
            usage=verbrauch,
            attempts=2,
        )

    def _mit_wiederholung(
        self,
        *,
        task: str,
        route: RouteConfig,
        store: str,
        run_id: UUID | None,
        aufruf: _Anbieteraufruf,
        attempt: int = 1,
    ) -> tuple[Any, Usage]:
        """Führt einen Anbieter-Aufruf aus: Budget davor, Retries mit Backoff, Protokoll danach.

        Der Backoff verdoppelt sich und ist gedeckelt. Er ist der Grund, warum ein kurzzeitig
        überlasteter Anbieter nicht denselben Lauf zum Scheitern bringt, den er beim zweiten
        Versuch bedient hätte — und warum ein dauerhaft ausgefallener nicht endlos festhält.
        """
        self._budget_pruefen(task=task, route=route, store=store, run_id=run_id)

        wartezeit = defaults.MODEL_BACKOFF_INITIAL_SECONDS
        letzter: Exception | None = None
        for versuch in range(self._models.defaults.max_retries + 1):
            beginn = time.monotonic()
            try:
                antwort = aufruf.ausfuehren()
            except Exception as exc:
                letzter = exc
                self._protokollieren(
                    ModelCall(
                        task=task,
                        provider=route.provider,
                        model=route.model,
                        status=defaults.MODEL_CALL_ERROR,
                        store=store,
                        run_id=run_id,
                        attempt=versuch + 1,
                        latency_ms=_ms(beginn),
                    ),
                    store=store,
                )
                if versuch < self._models.defaults.max_retries:
                    self._sleep(wartezeit)
                    if self._models.defaults.backoff == "exponential":
                        wartezeit = min(
                            wartezeit * defaults.MODEL_BACKOFF_FACTOR,
                            defaults.MODEL_BACKOFF_MAX_SECONDS,
                        )
                continue

            hinein, hinaus = aufruf.tokens(antwort)
            verbrauch = Usage(
                tokens_in=hinein,
                tokens_out=hinaus,
                cost_estimate_eur=route.estimate_cost(tokens_in=hinein, tokens_out=hinaus),
                latency_ms=_ms(beginn),
            )
            self._verbuchen(
                task=task,
                route=route,
                store=store,
                run_id=run_id,
                verbrauch=verbrauch,
                attempt=attempt if attempt > 1 else versuch + 1,
            )
            return antwort, verbrauch

        raise ModelError(
            f"'{route.model_key}' war für '{task}' nach "
            f"{self._models.defaults.max_retries + 1} Versuchen nicht erfolgreich: {letzter}"
        )

    # -- Budget ------------------------------------------------------------------

    def _budget_pruefen(
        self, *, task: str, route: RouteConfig, store: str, run_id: UUID | None
    ) -> None:
        """Prüft den Rahmen vor dem Aufruf (§11.6).

        Raises:
            BudgetExceededError: Wenn der Rahmen erschöpft ist und ``on_exceed = abort`` gilt.
        """
        aufrufe, kosten = self._verbraucht(store=store, run_id=run_id)
        urteil = self._guard.check(calls=aufrufe, cost_eur=kosten)
        if urteil.warned:
            _log.warning("modell.budget_ueberschritten", task=task, grund=urteil.reason)
            return
        if urteil.allowed:
            return

        self._protokollieren(
            ModelCall(
                task=task,
                provider=route.provider,
                model=route.model,
                status=defaults.MODEL_CALL_BUDGET_DENIED,
                store=store,
                run_id=run_id,
            ),
            store=store,
        )
        _log.warning("modell.budget_erschoepft", task=task, grund=urteil.reason)
        raise BudgetExceededError(grund=urteil.reason or "", calls=aufrufe, cost_eur=kosten)

    def _verbraucht(self, *, store: str, run_id: UUID | None) -> tuple[int, float]:
        """Was dieser Lauf bislang verbraucht hat — aus ``model_calls`` oder dem losen Zähler."""
        if run_id is None or self._unit_of_work is None:
            return self._lose_aufrufe, self._lose_kosten
        with self._unit_of_work(store) as uow:
            return uow.model_calls.spent(run_id)

    def _verbuchen(
        self,
        *,
        task: str,
        route: RouteConfig,
        store: str,
        run_id: UUID | None,
        verbrauch: Usage,
        attempt: int,
    ) -> None:
        """Schreibt einen gelungenen Aufruf fort — in ``model_calls`` und im losen Zähler."""
        self._lose_aufrufe += 1
        self._lose_kosten += verbrauch.cost_estimate_eur
        self._protokollieren(
            ModelCall(
                task=task,
                provider=route.provider,
                model=route.model,
                status=defaults.MODEL_CALL_OK,
                store=store,
                run_id=run_id,
                tokens_in=verbrauch.tokens_in,
                tokens_out=verbrauch.tokens_out,
                latency_ms=verbrauch.latency_ms,
                cost_estimate=verbrauch.cost_estimate_eur,
                attempt=attempt,
            ),
            store=store,
        )

    def _protokollieren(self, call: ModelCall, *, store: str) -> None:
        """Hängt einen Eintrag an ``model_calls`` — in eigener, sofort geschlossener Transaktion.

        Sie muss von der Transaktion des Aufrufers getrennt sein. Ein Lauf, der am Ende zurückrollt
        — ein Trockenlauf etwa —, soll seine Modellaufrufe trotzdem in der Abrechnung stehen
        haben: Sie haben stattgefunden, und Token kommen nicht zurück.
        """
        if self._unit_of_work is None:
            return
        with self._unit_of_work(store) as uow:
            uow.model_calls.record(call)

    # -- Cache -------------------------------------------------------------------

    def _cache_schluessel(self, task: str, route: RouteConfig, inhalt: str) -> str:
        """SHA-256 über Aufgabe, Modellschlüssel und Inhalt (§11.6).

        Der ``model_key`` gehört hinein, weil ein Modellwechsel den Zwischenspeicher sonst
        stillschweigend ungültig machen würde, ohne ihn zu leeren: Die Antwort des alten Modells
        käme unter dem neuen Namen zurück.
        """
        roh = f"{task}\x1e{route.model_key}\x1e{inhalt}".encode()
        return defaults.MODEL_CACHE_PREFIX + hashlib.sha256(roh).hexdigest()

    def _cache_lesen(self, task: str, route: RouteConfig, inhalt: str) -> str | None:
        """Ein Treffer aus dem Zwischenspeicher, oder ``None``."""
        if self._cache is None or not self._models.defaults.cache:
            return None
        return self._cache.get(self._cache_schluessel(task, route, inhalt))

    def _cache_schreiben(self, task: str, route: RouteConfig, inhalt: str, wert: str) -> None:
        """Legt eine Antwort mit der konfigurierten Verfallszeit ab."""
        if self._cache is None or not self._models.defaults.cache:
            return
        self._cache.set(
            self._cache_schluessel(task, route, inhalt),
            wert,
            ttl_seconds=self._models.defaults.cache_ttl_hours * 3600,
        )


class _Anbieteraufruf(Protocol):
    """Ein einzelner Aufruf an einen Anbieter, so weit gekapselt, dass er wiederholbar ist.

    Der Grund für dieses kleine Protokoll statt eines ``Callable``: :meth:`_mit_wiederholung`
    braucht zweierlei vom Aufruf — ihn ausführen und aus seiner Antwort die Token ablesen. Beides
    hängt an derselben Art von Aufruf, und ein Paar aus zwei Funktionen ließe zu, dass jemand die
    falsche Token-Funktion zum falschen Aufruf reicht.
    """

    def ausfuehren(self) -> Any:
        """Führt den Aufruf einmal aus."""

    def tokens(self, antwort: Any) -> tuple[int, int]:
        """Ein- und ausgehende Token der Antwort."""


class _Erzeugung:
    """Ein generativer Aufruf."""

    def __init__(self, client: ChatClient, prompt: PromptSpec) -> None:
        self._client = client
        self._prompt = prompt

    def ausfuehren(self) -> RawCompletion:
        """Ruft das Modell auf."""
        return self._client.complete(self._prompt)

    def tokens(self, antwort: Any) -> tuple[int, int]:
        """Ein- und ausgehende Token."""
        return antwort.tokens_in, antwort.tokens_out


class _Einbettung:
    """Ein Embedding-Aufruf über einen Stapel Texte."""

    def __init__(self, client: EmbeddingClient, texts: Sequence[str]) -> None:
        self._client = client
        self._texts = texts

    def ausfuehren(self) -> RawEmbedding:
        """Ruft das Modell auf."""
        return self._client.embed(self._texts)

    def tokens(self, antwort: Any) -> tuple[int, int]:
        """Ein Embedding erzeugt keine Ausgabe-Token — nur Eingabe zählt."""
        return antwort.tokens_in, 0


def _vektor_lesen(wert: str | None) -> tuple[float, ...] | None:
    """Wandelt einen abgelegten Vektor zurück; ein unlesbarer Eintrag gilt als Fehltreffer.

    Der Zwischenspeicher ist kein Speicher: Was sich nicht mehr lesen lässt — weil ein früherer
    Stand es anders abgelegt hat —, wird neu berechnet und nicht als Fehler gemeldet.
    """
    if wert is None:
        return None
    try:
        return tuple(float(zahl) for zahl in json.loads(wert))
    except (TypeError, ValueError):
        return None


def _stapel(indizes: Sequence[int], groesse: int) -> Iterator[list[int]]:
    """Zerlegt die noch offenen Positionen in Bündel (§11.6, "Batching")."""
    for beginn in range(0, len(indizes), groesse):
        yield list(indizes[beginn : beginn + groesse])


def _validieren(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
    """Validiert eine Antwort gegen das Schema; ``None`` heißt "passt nicht"."""
    if schema is None:
        return None
    try:
        return schema.model_validate_json(_json_ausschneiden(text))
    except (ValidationError, ValueError):
        return None


def _fehlertext(text: str, schema: type[BaseModel]) -> str:
    """Die Fehlermeldung, die dem Reparaturversuch beigelegt wird."""
    try:
        schema.model_validate_json(_json_ausschneiden(text))
    except (ValidationError, ValueError) as exc:
        return str(exc).replace("\n", " ")[:500]
    return ""  # pragma: no cover — nur erreichbar, wenn zwischenzeitlich doch gültig


def _json_ausschneiden(text: str) -> str:
    """Holt das JSON-Objekt aus einer Antwort, die es in einen Code-Zaun gepackt hat.

    Kein Nachbau eines Parsers: Gesucht wird die erste öffnende und die letzte schließende
    geschweifte Klammer. Modelle liefern ihre strukturierte Antwort gern mit ```json davor und
    einem erklärenden Satz danach — beides ist kein Formfehler des Modells, sondern eine
    Gewohnheit, an der eine sonst gültige Antwort nicht scheitern soll.
    """
    beginn = text.find("{")
    ende = text.rfind("}")
    if beginn == -1 or ende <= beginn:
        return text
    return text[beginn : ende + 1]


def _ms(beginn: float) -> int:
    """Die vergangene Zeit in Millisekunden."""
    return int((time.monotonic() - beginn) * 1000)


def _keine_route(task: str) -> str:
    """Die Meldung, wenn eine Aufgabe keine benutzbare Route hat."""
    return (
        f"Für die Aufgabe '{task}' war keine Route benutzbar. Häufigste Ursache: Der Provider hat "
        f"keinen Zugangsschlüssel — 'wg models describe' zeigt, welcher (§11.4)."
    )


__all__ = ["ModelRouterService", "NoRouteError"]
