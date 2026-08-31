"""Schema und Laden von ``config/models.yaml`` (§11.4, §6.3).

Diese Datei ist die Einlösung des Versprechens aus §11.1: "Damit ist ein Modellwechsel eine
Änderung in ``models.yaml`` — inklusive: anderer Anbieter, lokales Modell statt Cloud,
unterschiedliche Modelle je Aufgabe, unterschiedliche Modelle je Store."

Getrennt von :mod:`wissensgraph.config.schema` aus demselben Grund wie die Quellkonfiguration:
Die Kernkonfiguration braucht jeder Prozess, diese hier nur, wer ein Modell aufruft. Eine fehlende
``models.yaml`` ist deshalb kein Startfehler, sondern eine Konfiguration ohne Aufgaben — der
Graph lässt sich auch ohne Modell befüllen, durchsuchen und traversieren (§11.5: "Der Kernspace
funktioniert dann über Kanten und lexikalische Suche").

Die Querprüfungen stehen bewusst hier und nicht im Router: §6.5 verlangt, dass ein Task-Profil mit
unbekanntem Provider und eine von ``WG_EMBEDDING_DIM`` abweichende Dimension den **Start**
abbrechen. Ein Router, der das erst beim ersten Aufruf merkt, hätte den Fehler in die Nacht des
ersten großen Laufs verschoben.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.loader import load_yaml_mapping
from wissensgraph.config.placeholders import resolve_placeholders
from wissensgraph.config.schema import FrozenModel, Settings, empty_to_none

ProviderType = Literal["google_genai", "vertex", "openai_compatible"]


class UnknownTaskError(KeyError):
    """Es wurde eine Aufgabe angefragt, für die kein Profil konfiguriert ist (§11.3)."""

    def __init__(self, task: str, known: tuple[str, ...]) -> None:
        self.task = task
        super().__init__(
            f"Für die Aufgabe '{task}' ist kein Profil konfiguriert. Vorhanden sind: "
            f"{', '.join(known) or '(keine)'}. Task-Profile stehen in models.yaml (§11.3)."
        )


class UnknownProviderError(KeyError):
    """Es wurde ein Provider angefragt, den ``models.yaml`` nicht kennt."""

    def __init__(self, provider: str, known: tuple[str, ...]) -> None:
        self.provider = provider
        super().__init__(
            f"Unbekannter Provider '{provider}'. Konfiguriert sind: "
            f"{', '.join(known) or '(keiner)'}."
        )


class ProviderConfig(FrozenModel):
    """Ein Modellanbieter — eine Zugangsart, kein Modell (§11.4).

    ``local`` ist das wichtigste Feld dieser Datei. Es entscheidet nach §11.5, ob persönliche
    Inhalte an diesen Anbieter gehen dürfen, und es ist eine *Behauptung des Betreibers*: Ob ein
    Dienst wirklich auf demselben Rechner läuft, kann diese Konfiguration nicht nachprüfen. Genau
    deshalb steht der Wert hier ausdrücklich und wird nicht aus der URL geraten — wer ihn setzt,
    trifft eine bewusste Entscheidung, und ``wg config show`` macht sie sichtbar.
    """

    type: ProviderType = Field(
        description=(
            "Die LangChain-Integration, über die dieser Anbieter angesprochen wird. "
            "'openai_compatible' deckt Ollama, vLLM und jeden weiteren OpenAI-kompatiblen "
            "Endpunkt ab — für sie entsteht kein eigener Code (§11.4)."
        )
    )
    api_key: str | None = Field(
        default=None, description="Zugangsschlüssel; kommt aus ENV (§20.2)."
    )
    base_url: str | None = None
    project: str | None = Field(default=None, description="GCP-Projekt für 'vertex'.")
    location: str | None = Field(
        default=None,
        description=(
            "Standort für 'vertex': eine Region ('europe-west4'), eine Mehrregion ('eu', 'us') "
            "oder 'global'. Bestimmt den Endpunkt und damit den Ort der Verarbeitung."
        ),
    )
    credentials_file: str | None = Field(
        default=None,
        description=(
            "Pfad zum Dienstkonto-Schlüssel für 'vertex'. Ohne Angabe gilt die Standard-Anmeldung "
            "der Umgebung (Workload Identity, 'gcloud auth application-default login')."
        ),
    )
    local: bool = Field(
        default=False,
        description=(
            "Ob dieser Anbieter auf demselben Rechner läuft. Entscheidet nach §11.5 über den "
            "Zugang zu persönlichen Inhalten."
        ),
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Weitere Parameter, die unverändert an die LangChain-Integration durchgereicht "
            "werden. Der Kern interpretiert sie nicht — sie sind der Weg, eine Eigenheit eines "
            "Anbieters zu bedienen, ohne dass dafür ein Feld im Schema entsteht."
        ),
    )

    _normalize = field_validator(
        "api_key", "base_url", "project", "location", "credentials_file", mode="before"
    )(empty_to_none)

    @property
    def is_configured(self) -> bool:
        """Ob dieser Anbieter benutzbar erscheint — Grundlage von ``wg models describe``.

        Geprüft wird nur, ob das Nötigste dasteht. Ob der Schlüssel gilt, weiß erst der erste
        Aufruf; ein Router, der beim Start jeden Anbieter anspräche, verbrauchte Token für eine
        Frage, die niemand gestellt hat.
        """
        if self.type == defaults.PROVIDER_TYPE_VERTEX:
            # Der Standort gehört zum Nötigsten und nicht zur Feinabstimmung: Aus ihm folgt der
            # Endpunkt. Ohne ihn gibt es keinen Host, den man ansprechen könnte — ein
            # Dienstkonto-Schlüssel ist dagegen entbehrlich, weil die Standard-Anmeldung der
            # Umgebung (Workload Identity) derselbe gültige Weg ist.
            return bool(self.project) and bool(self.location)
        if self.type == defaults.PROVIDER_TYPE_OPENAI_COMPATIBLE:
            return bool(self.base_url)
        return bool(self.api_key)

    @property
    def endpoint(self) -> str | None:
        """Der Host, den ein ``vertex``-Anbieter tatsächlich anspricht — sonst ``None``.

        Die Ableitung bildet die Regel der Google-Bibliothek nach, damit sie **sichtbar** wird:
        ``global`` und die Mehrregionen ``eu``/``us`` haben je einen eigenen Hostnamen, jede
        andere Angabe wird als Region eingesetzt. Ein Tippfehler im Standort erzeugt deshalb keine
        Fehlermeldung, sondern einen anderen Ort der Verarbeitung — und das ist bei einem System
        mit einer Datenschutzgrenze (Leitprinzip 2) kein Detail. ``wg doctor`` und
        ``wg models describe`` geben den Wert aus, statt ihn nur intern zu prüfen.
        """
        if self.type != defaults.PROVIDER_TYPE_VERTEX or not self.location:
            return None
        if self.location == defaults.VERTEX_GLOBAL_LOCATION:
            return "aiplatform.googleapis.com"
        if self.location in defaults.VERTEX_MULTI_REGIONS:
            return f"aiplatform.{self.location}.rep.googleapis.com"
        return f"{self.location}-aiplatform.googleapis.com"


class RouteConfig(FrozenModel):
    """Ein konkretes Modell für eine Aufgabe — ein Eintrag unter ``primary`` oder ``fallback``."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)

    dim: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Vektordimension eines Embedding-Modells. Muss zu 'WG_EMBEDDING_DIM' passen — sonst "
            "verweigert der Router den Start (§11.7)."
        ),
    )
    batch_size: int = Field(default=defaults.MODEL_BATCH_SIZE, ge=1)

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    json_mode: bool = Field(
        default=False,
        description="Ob der Anbieter zu strukturierter Ausgabe gezwungen wird (§11.6).",
    )

    cost_per_1k_input_eur: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Preis je 1000 Eingabe-Token. Nur eine Schätzung und ausdrücklich als solche benannt "
            "— sie speist 'max_estimated_cost_per_run_eur' (§11.6). Ohne Wert kostet ein Aufruf "
            "rechnerisch nichts, und der Wächter greift allein über die Aufrufzahl."
        ),
    )
    cost_per_1k_output_eur: float = Field(default=0.0, ge=0.0)

    @property
    def model_key(self) -> str:
        """Die Kennung, unter der Vektoren und Aufrufe abgelegt werden (§13.1, §11.7).

        Provider *und* Modell, weil beides den Vektorraum bestimmt: Dasselbe Modell über zwei
        Anbieter kann sich unterscheiden, und eine Suche über gemischte Bestände wäre still
        falsch. §11.7: "Vektorsuchen filtern immer auf den aktiven ``model_key``."
        """
        return f"{self.provider}:{self.model}"

    def estimate_cost(self, *, tokens_in: int, tokens_out: int) -> float:
        """Die geschätzten Kosten eines Aufrufs in Euro."""
        return (
            tokens_in * self.cost_per_1k_input_eur + tokens_out * self.cost_per_1k_output_eur
        ) / 1000.0


class TaskConfig(FrozenModel):
    """Ein Task-Profil: das bevorzugte Modell und die Kette dahinter (§11.3, §11.6)."""

    primary: RouteConfig
    fallback: tuple[RouteConfig, ...] = ()

    @property
    def routes(self) -> tuple[RouteConfig, ...]:
        """Primary und Fallbacks in der Reihenfolge, in der sie versucht werden."""
        return (self.primary, *self.fallback)


class RouterDefaults(FrozenModel):
    """Was für alle Aufgaben gilt, solange keine etwas anderes sagt (§11.4)."""

    timeout_seconds: int = Field(default=defaults.MODEL_TIMEOUT_SECONDS, ge=1)
    max_retries: int = Field(default=defaults.MODEL_MAX_RETRIES, ge=0)
    backoff: Literal["exponential", "constant"] = "exponential"
    cache: bool = defaults.MODEL_CACHE_ENABLED
    cache_ttl_hours: int = Field(default=defaults.MODEL_CACHE_TTL_HOURS, ge=1)


class StorePolicyConfig(FrozenModel):
    """Welche Provider die Inhalte eines Stores sehen dürfen (§11.4, §11.5)."""

    allowed_providers: tuple[str, ...] = Field(min_length=1)
    on_violation: Literal["abort", "skip"] = Field(
        default="abort",
        description=(
            "'abort' bricht den Lauf ab, 'skip' überspringt das betroffene Konzept und zählt es. "
            "Ein stiller Fallback auf einen erlaubten, aber schlechteren Anbieter ist in beiden "
            "Fällen ausgeschlossen (§11.5)."
        ),
    )


class ModelsConfig(FrozenModel):
    """Der Inhalt von ``models.yaml`` als Ganzes."""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    defaults: RouterDefaults = RouterDefaults()
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)
    policies: dict[str, StorePolicyConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_internal(self) -> ModelsConfig:
        """Prüft, was sich ohne die Kernkonfiguration beantworten lässt."""
        for name, task in self.tasks.items():
            for route in task.routes:
                if route.provider not in self.providers:
                    raise ValueError(
                        f"Task '{name}' verweist auf den unbekannten Provider "
                        f"'{route.provider}'. Konfiguriert sind: "
                        f"{', '.join(sorted(self.providers)) or '(keiner)'} (§6.5)."
                    )
            if name in defaults.DETERMINISTIC_TASKS:
                _check_deterministic(name, task)

        for store, policy in self.policies.items():
            unbekannt = sorted(set(policy.allowed_providers) - set(self.providers))
            if unbekannt:
                raise ValueError(
                    f"policies.{store}.allowed_providers nennt die unbekannten Provider "
                    f"{unbekannt}. Eine Freigabe für etwas, das es nicht gibt, wäre "
                    f"stillschweigend wirkungslos."
                )
        return self

    def task(self, name: str) -> TaskConfig:
        """Das Profil einer Aufgabe.

        Raises:
            UnknownTaskError: Wenn die Aufgabe nicht konfiguriert ist.
        """
        try:
            return self.tasks[name]
        except KeyError as exc:
            raise UnknownTaskError(name, tuple(sorted(self.tasks))) from exc

    def provider(self, name: str) -> ProviderConfig:
        """Die Zugangsdaten eines Providers.

        Raises:
            UnknownProviderError: Wenn der Provider nicht konfiguriert ist.
        """
        try:
            return self.providers[name]
        except KeyError as exc:
            raise UnknownProviderError(name, tuple(sorted(self.providers))) from exc

    def allowed_providers(self, store: str) -> tuple[str, ...] | None:
        """Die Freigabeliste eines Stores, oder ``None``, wenn keine hinterlegt ist.

        Der Unterschied zwischen "keine Liste" und "leere Liste" ist bedeutsam und deshalb im
        Rückgabewert abgebildet: Eine fehlende Angabe erlaubt alles, eine leere nichts.
        """
        policy = self.policies.get(store)
        return None if policy is None else policy.allowed_providers

    def on_violation(self, store: str) -> str:
        """Wie ein Policy-Verstoß in diesem Store behandelt wird (§11.4)."""
        policy = self.policies.get(store)
        return "abort" if policy is None else policy.on_violation


def _check_deterministic(name: str, task: TaskConfig) -> None:
    """§11.6: Für ``relation_extraction`` und ``cluster_matching`` ist ``temperature = 0`` Pflicht.

    "die Validierung lehnt andere Werte ab" — und zwar für jede Route der Kette, nicht nur für
    die erste. Ein Fallback mit Temperatur wäre genau der Fall, in dem niemand hinsieht: Er greift
    erst, wenn der Primary schon versagt hat.
    """
    for route in task.routes:
        if route.temperature not in (0.0, None):
            raise ValueError(
                f"Task '{name}' verlangt nach §11.6 'temperature: 0' — die Route "
                f"'{route.model_key}' setzt {route.temperature}. Beide Aufgaben erzeugen Kanten "
                f"im Graphen; ein Zufallsanteil darin wäre nicht reproduzierbar."
            )


def models_file(settings: Settings, env: Mapping[str, str] | None = None) -> Path:
    """Der Pfad der Router-Konfiguration — aus ``WG_MODELS_FILE`` oder dem Config-Verzeichnis."""
    umgebung = os.environ if env is None else env
    angegeben = umgebung.get(defaults.MODELS_FILE_ENV, "").strip()
    if angegeben:
        return Path(angegeben)
    return Path(settings.config_dir) / defaults.MODELS_CONFIG_FILENAME


def load_models(
    settings: Settings,
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelsConfig:
    """Lädt und validiert ``models.yaml`` gegen die Kernkonfiguration (§6.5, §11.7).

    Args:
        settings: Die geprüfte Kernkonfiguration.
        path: Abweichender Pfad; sonst aus ``WG_MODELS_FILE`` bzw. dem Config-Verzeichnis.
        env: Prozessumgebung — als Parameter, damit Tests ohne globalen Zustand auskommen.

    Returns:
        Die validierte Router-Konfiguration; eine fehlende Datei ergibt eine leere.

    Raises:
        ConfigFileError: Wenn die Datei unlesbar ist oder kein Mapping enthält.
        PlaceholderResolutionError: Bei nicht auflösbarem ``${...}``-Platzhalter.
        ConfigValidationError: Bei jedem Verstoß gegen §11.4 oder §6.5.
    """
    umgebung = dict(os.environ if env is None else env)
    ziel = models_file(settings, umgebung) if path is None else path
    if not ziel.is_file():
        return ModelsConfig()

    roh = load_yaml_mapping(ziel)
    aufgeloest = resolve_placeholders(roh, umgebung, path=ziel.name)

    try:
        config = ModelsConfig.model_validate(aufgeloest)
    except ValidationError as exc:
        raise ConfigValidationError(_format_error(exc, ziel)) from exc

    _check_against_settings(config, settings, ziel)
    return config


def _check_against_settings(config: ModelsConfig, settings: Settings, path: Path) -> None:
    """Prüft die Router-Konfiguration gegen Stores und Vektordimension (§6.5, §11.7)."""
    for store in config.policies:
        if store not in settings.stores:
            raise ConfigValidationError(
                f"policies in '{path}' nennt den unbekannten Store '{store}'. Konfiguriert sind: "
                f"{', '.join(sorted(settings.stores))}."
            )

    embedding = config.tasks.get(defaults.TASK_EMBEDDING)
    if embedding is None:
        return

    # §11.7: "Der Router weigert sich zu starten, wenn tasks.embedding.primary.dim von
    # WG_EMBEDDING_DIM abweicht." Die Dimension steht im Migrationsschema als 'vector(n)'; ein
    # abweichendes Modell schriebe Vektoren, die die Spalte gar nicht aufnehmen kann — und das
    # erst mitten im ersten Embedding-Lauf.
    dim = embedding.primary.dim
    if dim is not None and dim != settings.embedding_dim:
        raise ConfigValidationError(
            f"tasks.embedding.primary in '{path}' liefert {dim} Dimensionen, das Schema ist mit "
            f"WG_EMBEDDING_DIM={settings.embedding_dim} migriert. Entweder ein Modell mit "
            f"passender Dimension wählen oder eine neue Migration mit vollständigem Neuaufbau "
            f"der Embeddings anstoßen (§11.7)."
        )

    for route in embedding.fallback:
        if route.dim is not None and route.dim != settings.embedding_dim:
            raise ConfigValidationError(
                f"Die Fallback-Route '{route.model_key}' für 'embedding' in '{path}' liefert "
                f"{route.dim} Dimensionen statt {settings.embedding_dim}. Ein Fallback mit "
                f"anderer Dimension scheiterte erst dann, wenn der Primary schon ausgefallen ist "
                f"— also im ungünstigsten Augenblick (§11.7)."
            )


def _format_error(exc: ValidationError, source: Path) -> str:
    """Formt Pydantic-Fehler in eine Meldung um, die den Ort des Problems benennt."""
    lines = [f"Router-Konfiguration aus '{source}' ist ungültig:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(Wurzel)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
