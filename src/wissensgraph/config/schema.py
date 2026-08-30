"""Pydantic-Schema der aufgelösten Konfiguration (§6).

Die Konfiguration wird beim Start **einmal** validiert und danach unverändert gehalten (§6.1
Regel 4) — alle Modelle hier sind deshalb ``frozen``. Verstöße gegen die Regeln aus §6.5 werden
als :class:`~wissensgraph.config.errors.ConfigValidationError` gemeldet, nicht stillschweigend
korrigiert: ein Prozess mit unklarer Konfiguration soll gar nicht erst laufen.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wissensgraph.config import defaults
from wissensgraph.config.network import is_local_dsn

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


def _empty_to_none(value: object) -> object:
    """Behandelt einen leeren String wie einen fehlenden Wert.

    Ein Platzhalter mit leerem Fallback (``${WG_BROKER_URL:-}``) liefert einen leeren String.
    Für ein optionales Feld ist das gleichbedeutend mit "nicht gesetzt"; ohne diese Umwandlung
    würde etwa eine leere Broker-URL als gültiger Wert durchgehen.
    """
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class FrozenModel(BaseModel):
    """Basis aller Konfigurationsmodelle: unveränderlich und ohne unbekannte Felder.

    ``extra="forbid"`` ist Absicht. Ein Tippfehler in einer YAML-Datei (``neighbours_k`` statt
    ``neighbors_k``) soll den Start abbrechen und nicht als stillschweigend ignorierter Schlüssel
    zu einem falsch laufenden System führen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Stores und Scopes (§6.3, §7.3)
# ---------------------------------------------------------------------------


class StoreConfig(FrozenModel):
    """Ein physischer Speicherbereich — genau eine PostgreSQL-Datenbank."""

    dsn: str = Field(min_length=1, description="SQLAlchemy-DSN der Datenbank dieses Stores.")
    allow_remote: bool = Field(
        default=False,
        description=(
            "Ob dieser Store auf einem entfernten Host liegen darf. Für 'personal' ist das nach "
            "Leitprinzip 2 'false' — der DSN muss dann lokal auflösen (§6.5)."
        ),
    )

    @model_validator(mode="after")
    def _check_locality(self) -> StoreConfig:
        if not self.allow_remote and not is_local_dsn(self.dsn):
            raise ValueError(
                "allow_remote=false, aber der DSN zeigt nicht auf einen lokalen Host. "
                "Entweder den DSN auf localhost/ein privates Netz/den Compose-Service richten "
                "oder allow_remote bewusst auf true setzen."
            )
        return self


class ScopeConfig(FrozenModel):
    """Eine logische Gruppierung innerhalb eines Stores — eine Spalte, kein Verzeichnis (§2)."""

    name: str = Field(min_length=1)
    store: str = Field(min_length=1)
    description: str | None = None


class ConceptTypeConfig(FrozenModel):
    """Ein Eintrag der Typen-Taxonomie (§7.2). Taxonomie ist Konfiguration, nicht Code."""

    name: str = Field(min_length=1)
    stores: tuple[str, ...] = Field(min_length=1)
    source_mirrored: bool = Field(
        default=False,
        description=(
            "true: Inhaltsfelder sind für UI, API und Agent schreibgeschützt; nur Kanten, "
            "status, tags und Verifikationsfelder sind kuratierbar (§7.2)."
        ),
    )


class EdgeKindsConfig(FrozenModel):
    """Die erlaubten Kantenarten, getrennt nach Wirkung auf die Traversierung (§7.7)."""

    structural: tuple[str, ...] = Field(min_length=1)
    semantic: tuple[str, ...] = Field(min_length=1)

    @property
    def all_kinds(self) -> tuple[str, ...]:
        """Alle Kantenarten in einer Liste."""
        return (*self.structural, *self.semantic)

    @model_validator(mode="after")
    def _check_disjoint(self) -> EdgeKindsConfig:
        overlap = set(self.structural) & set(self.semantic)
        if overlap:
            raise ValueError(
                f"Kantenarten dürfen nicht zugleich strukturell und semantisch sein: "
                f"{sorted(overlap)}. Die Unterscheidung steuert Traversierung und die Definition "
                f"eines losen Knotens (§7.7)."
            )
        return self


# ---------------------------------------------------------------------------
# Lauf-Parameter (§6.3)
# ---------------------------------------------------------------------------


class ClusteringConfig(FrozenModel):
    """Parameter der Cluster-Bildung (§13.2, §13.3)."""

    neighbors_k: int = Field(default=defaults.CLUSTERING_NEIGHBORS_K, ge=1)
    min_cluster_size: int = Field(default=defaults.CLUSTERING_MIN_CLUSTER_SIZE, ge=2)
    max_cluster_size: int = Field(default=defaults.CLUSTERING_MAX_CLUSTER_SIZE, ge=2)
    stability_runs: int = Field(default=defaults.CLUSTERING_STABILITY_RUNS, ge=1)
    related_cluster_top_n: int = Field(default=defaults.CLUSTERING_RELATED_CLUSTER_TOP_N, ge=0)
    relabel_on_member_change_pct: int = Field(
        default=defaults.CLUSTERING_RELABEL_ON_MEMBER_CHANGE_PCT, ge=0, le=100
    )

    @model_validator(mode="after")
    def _check_sizes(self) -> ClusteringConfig:
        if self.min_cluster_size > self.max_cluster_size:
            raise ValueError(
                f"min_cluster_size ({self.min_cluster_size}) darf max_cluster_size "
                f"({self.max_cluster_size}) nicht überschreiten."
            )
        return self


class OrphansConfig(FrozenModel):
    """Parameter der Verwaiste-Knoten-Vernetzung (§15.4)."""

    loose_threshold: int = Field(default=defaults.ORPHANS_LOOSE_THRESHOLD, ge=0)
    proximity_top_n: int = Field(default=defaults.ORPHANS_PROXIMITY_TOP_N, ge=1)
    proximity_auto_commit: Probability = defaults.ORPHANS_PROXIMITY_AUTO_COMMIT
    proximity_candidate_band: Probability = defaults.ORPHANS_PROXIMITY_CANDIDATE_BAND
    use_llm: bool = defaults.ORPHANS_USE_LLM
    cluster_suggestion_limit: int = Field(default=defaults.ORPHANS_CLUSTER_SUGGESTION_LIMIT, ge=0)
    cluster_preview_members: int = Field(default=defaults.ORPHANS_CLUSTER_PREVIEW_MEMBERS, ge=1)
    min_confidence: Probability = defaults.ORPHANS_MIN_CONFIDENCE
    pattern_files: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_bands(self) -> OrphansConfig:
        if self.proximity_candidate_band > self.proximity_auto_commit:
            raise ValueError(
                f"proximity_candidate_band ({self.proximity_candidate_band}) muss unter "
                f"proximity_auto_commit ({self.proximity_auto_commit}) liegen — sonst gibt es "
                f"kein Kandidatenband für Stufe 2 (§15.2)."
            )
        return self


class RankingConfig(FrozenModel):
    """Gewichte der Relevanzbewertung eines Zielkonzepts (§12.3)."""

    hop_weight: float = Field(default=defaults.RANKING_HOP_WEIGHT, ge=0.0)
    density_weight: float = Field(default=defaults.RANKING_DENSITY_WEIGHT, ge=0.0)
    recency_weight: float = Field(default=defaults.RANKING_RECENCY_WEIGHT, ge=0.0)
    recency_half_life_days: float = Field(default=defaults.RANKING_RECENCY_HALF_LIFE_DAYS, gt=0.0)

    @model_validator(mode="after")
    def _check_any_weight(self) -> RankingConfig:
        if self.hop_weight == self.density_weight == self.recency_weight == 0.0:
            raise ValueError(
                "Mindestens ein Ranking-Gewicht muss größer als 0 sein, sonst ist jede "
                "Bewertung identisch null."
            )
        return self


class TraversalConfig(FrozenModel):
    """Grenzen und Bewertung der Graph-Traversierung (§12)."""

    default_hops: int = Field(default=defaults.TRAVERSAL_DEFAULT_HOPS, ge=1)
    max_hops: int = Field(default=defaults.TRAVERSAL_MAX_HOPS, ge=1)
    max_nodes: int = Field(default=defaults.TRAVERSAL_MAX_NODES, ge=1)
    ranking: RankingConfig = RankingConfig()

    @model_validator(mode="after")
    def _check_hops(self) -> TraversalConfig:
        if self.default_hops > self.max_hops:
            raise ValueError(
                f"default_hops ({self.default_hops}) darf max_hops ({self.max_hops}) nicht "
                f"überschreiten."
            )
        return self


class BudgetConfig(FrozenModel):
    """Harte Obergrenze für Modellaufrufe je Lauf (§11.6).

    Der Wächter ist der einzige Schutz davor, dass ein fehlkonfigurierter Lauf unbemerkt große
    Mengen Token verbraucht. ``on_exceed: abort`` beendet den Lauf sauber mit Teilergebnis.
    """

    max_model_calls_per_run: int = Field(default=defaults.BUDGET_MAX_MODEL_CALLS_PER_RUN, ge=0)
    max_estimated_cost_per_run_eur: float = Field(
        default=defaults.BUDGET_MAX_ESTIMATED_COST_PER_RUN_EUR, ge=0.0
    )
    on_exceed: Literal["abort", "warn"] = defaults.BUDGET_ON_EXCEED


# ---------------------------------------------------------------------------
# Schnittstellen (§16, §18, §20.3)
# ---------------------------------------------------------------------------


class ApiConfig(FrozenModel):
    """Bind-Adresse und Absicherung der HTTP-API (§16.1, §20.3)."""

    host: str = defaults.API_HOST
    port: int = Field(default=defaults.API_PORT, ge=1, le=65535)
    auth_mode: Literal["none", "token", "oidc"] = defaults.API_AUTH_MODE
    token: str | None = None
    cors_origins: tuple[str, ...] = (defaults.API_CORS_ORIGINS,)

    _normalize_token = field_validator("token", mode="before")(_empty_to_none)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Erlaubt eine kommaseparierte Liste, wie sie aus einer einzelnen ENV-Variable kommt."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, list | tuple):
            expanded: list[str] = []
            for item in value:
                if isinstance(item, str):
                    expanded.extend(part.strip() for part in item.split(",") if part.strip())
                else:
                    expanded.append(item)
            return tuple(expanded)
        return value

    @model_validator(mode="after")
    def _check_auth(self) -> ApiConfig:
        if self.auth_mode == "token" and not self.token:
            raise ValueError(
                "auth_mode='token' verlangt ein Token in WG_API_TOKEN. Ohne Token wäre die API "
                "unabgesichert erreichbar."
            )
        if self.auth_mode == "none" and self.host not in defaults.API_LOOPBACK_HOSTS:
            raise ValueError(
                f"auth_mode='none' ist nur bei Bindung an Loopback erlaubt "
                f"({', '.join(defaults.API_LOOPBACK_HOSTS)}), nicht an '{self.host}' (§20.3)."
            )
        if "*" in self.cors_origins:
            raise ValueError("CORS-Wildcard ist nicht erlaubt; Ursprünge explizit angeben (§20.3).")
        return self


class McpConfig(FrozenModel):
    """Transport des MCP-Servers (§18)."""

    transport: Literal["stdio", "http"] = defaults.MCP_TRANSPORT
    port: int = Field(default=defaults.MCP_PORT, ge=1, le=65535)


class LoggingConfig(FrozenModel):
    """Ausgabeform des strukturierten Logs (§21.1)."""

    level: str = defaults.LOG_LEVEL
    format: Literal["json", "console"] = defaults.LOG_FORMAT

    @model_validator(mode="after")
    def _check_level(self) -> LoggingConfig:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if self.level.upper() not in allowed:
            raise ValueError(f"Unbekanntes Log-Level '{self.level}'. Erlaubt: {sorted(allowed)}.")
        return self


class DatabaseConfig(FrozenModel):
    """Verbindungsverhalten, das für beide Stores gleich gilt."""

    pool_size: int = Field(default=defaults.DB_POOL_SIZE, ge=1)
    connect_timeout_seconds: int = Field(default=defaults.DB_CONNECT_TIMEOUT_SECONDS, ge=1)


# ---------------------------------------------------------------------------
# Wurzel
# ---------------------------------------------------------------------------


class Settings(FrozenModel):
    """Die vollständig aufgelöste, validierte Konfiguration eines Prozesses (§6.1 Regel 4)."""

    env: Literal["dev", "test", "prod"] = defaults.ENV
    config_dir: str = defaults.CONFIG_DIR

    stores: dict[str, StoreConfig]
    scopes: tuple[ScopeConfig, ...] = Field(min_length=1)
    concept_types: tuple[ConceptTypeConfig, ...] = Field(min_length=1)
    edge_kinds: EdgeKindsConfig

    clustering: ClusteringConfig = ClusteringConfig()
    orphans: OrphansConfig = OrphansConfig()
    traversal: TraversalConfig = TraversalConfig()
    budget: BudgetConfig = BudgetConfig()

    api: ApiConfig
    mcp: McpConfig = McpConfig()
    logging: LoggingConfig = LoggingConfig()
    database: DatabaseConfig = DatabaseConfig()

    embedding_dim: int = Field(ge=1, description="Vektordimension; bestimmt das Migrationsschema.")
    broker_url: str | None = None
    personal_allow_remote_models: bool = defaults.PERSONAL_ALLOW_REMOTE_MODELS

    _normalize_broker_url = field_validator("broker_url", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def _check_cross_references(self) -> Settings:
        """Prüft die stufenübergreifenden Regeln aus §6.5."""
        known_stores = set(self.stores)

        for scope in self.scopes:
            if scope.store not in known_stores:
                raise ValueError(
                    f"Scope '{scope.name}' verweist auf den unbekannten Store '{scope.store}'. "
                    f"Bekannt sind: {sorted(known_stores)}."
                )

        for concept_type in self.concept_types:
            unknown = set(concept_type.stores) - known_stores
            if unknown:
                raise ValueError(
                    f"Konzepttyp '{concept_type.name}' verweist auf unbekannte Stores "
                    f"{sorted(unknown)}. Bekannt sind: {sorted(known_stores)}."
                )

        duplicate_scopes = _duplicates(scope.name for scope in self.scopes)
        if duplicate_scopes:
            raise ValueError(f"Scope-Namen müssen eindeutig sein, doppelt: {duplicate_scopes}.")

        duplicate_types = _duplicates(item.name for item in self.concept_types)
        if duplicate_types:
            raise ValueError(f"Konzepttypen müssen eindeutig sein, doppelt: {duplicate_types}.")

        return self

    def scopes_for_store(self, store: str) -> tuple[ScopeConfig, ...]:
        """Alle Scopes, die in einem bestimmten Store liegen."""
        return tuple(scope for scope in self.scopes if scope.store == store)

    def store_of_scope(self, scope_name: str) -> str:
        """Der Store, in dem ein Scope liegt.

        Raises:
            KeyError: Wenn der Scope nicht konfiguriert ist.
        """
        for scope in self.scopes:
            if scope.name == scope_name:
                return scope.store
        raise KeyError(f"Unbekannter Scope '{scope_name}'.")

    def concept_type(self, name: str) -> ConceptTypeConfig:
        """Der Taxonomie-Eintrag zu einem Typnamen.

        Raises:
            KeyError: Wenn der Typ nicht konfiguriert ist.
        """
        for concept_type in self.concept_types:
            if concept_type.name == name:
                return concept_type
        raise KeyError(f"Unbekannter Konzepttyp '{name}'.")


def _duplicates(values: object) -> list[str]:
    """Sammelt mehrfach vorkommende Namen — für Fehlermeldungen sortiert."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:  # type: ignore[attr-defined]
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
