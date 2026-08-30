"""Abbildung von Umgebungsvariablen auf Konfigurationspfade (§6.4).

Jede Variable aus der Tabelle in §6.4 steht hier genau einmal mit ihrem Zielpfad in der
Konfigurationsstruktur und der Umwandlung ihres Strings in den Zieltyp. Das hält die
ENV-Schnittstelle an einer Stelle überprüfbar — sowohl gegen das Dokument als auch gegen
``.env.example``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wissensgraph.config.errors import ConfigValidationError

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def to_bool(raw: str) -> bool:
    """Wandelt einen ENV-String in einen Wahrheitswert."""
    lowered = raw.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ConfigValidationError(
        f"'{raw}' ist kein Wahrheitswert. Erlaubt sind: "
        f"{', '.join(sorted(TRUE_VALUES | FALSE_VALUES))}."
    )


def to_int(raw: str) -> int:
    """Wandelt einen ENV-String in eine Ganzzahl."""
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigValidationError(f"'{raw}' ist keine ganze Zahl.") from exc


def to_str(raw: str) -> str:
    """Übernimmt einen ENV-String unverändert bis auf umgebenden Leerraum."""
    return raw.strip()


def to_csv_tuple(raw: str) -> tuple[str, ...]:
    """Zerlegt eine kommaseparierte Liste, z. B. ``WG_API_CORS_ORIGINS``."""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class EnvBinding:
    """Bindet eine Umgebungsvariable an einen Pfad in der Konfigurationsstruktur."""

    variable: str
    path: tuple[str, ...]
    convert: Callable[[str], Any]
    description: str


#: Die vollständige ENV-Schnittstelle nach §6.4. Provider- und Quell-Variablen
#: (``WG_PROVIDER_*``, ``WG_SOURCE_*``) stehen bewusst nicht hier: Sie werden über
#: ``${...}``-Platzhalter in ``models.yaml``/``sources.yaml`` aufgelöst und kommen mit den
#: jeweiligen Stufen (7 bzw. 3) hinzu.
ENV_BINDINGS: tuple[EnvBinding, ...] = (
    EnvBinding("WG_ENV", ("env",), to_str, "Laufzeitumgebung: dev | test | prod"),
    EnvBinding("WG_CONFIG_DIR", ("config_dir",), to_str, "Verzeichnis der Config-Dateien"),
    EnvBinding("WG_LOG_LEVEL", ("logging", "level"), to_str, "Log-Level"),
    EnvBinding("WG_LOG_FORMAT", ("logging", "format"), to_str, "json | console"),
    EnvBinding("WG_DB_SHARED_DSN", ("stores", "shared", "dsn"), to_str, "DSN des shared-Stores"),
    EnvBinding(
        "WG_DB_PERSONAL_DSN", ("stores", "personal", "dsn"), to_str, "DSN des personal-Stores"
    ),
    EnvBinding("WG_DB_POOL_SIZE", ("database", "pool_size"), to_int, "Connection-Pool je Store"),
    EnvBinding(
        "WG_EMBEDDING_DIM",
        ("embedding_dim",),
        to_int,
        "Vektordimension; bestimmt das Migrationsschema (§7.3)",
    ),
    EnvBinding("WG_API_HOST", ("api", "host"), to_str, "Bind-Adresse der API"),
    EnvBinding("WG_API_PORT", ("api", "port"), to_int, "Port der API"),
    EnvBinding("WG_API_AUTH_MODE", ("api", "auth_mode"), to_str, "none | token | oidc"),
    EnvBinding("WG_API_TOKEN", ("api", "token"), to_str, "Bearer-Token bei auth_mode=token"),
    EnvBinding(
        "WG_API_CORS_ORIGINS",
        ("api", "cors_origins"),
        to_csv_tuple,
        "kommaseparierte Liste erlaubter Ursprünge",
    ),
    EnvBinding("WG_MCP_TRANSPORT", ("mcp", "transport"), to_str, "stdio | http"),
    EnvBinding("WG_MCP_PORT", ("mcp", "port"), to_int, "Port bei HTTP-Transport"),
    EnvBinding("WG_BROKER_URL", ("broker_url",), to_str, "Redis-URL für die Job-Queue"),
    EnvBinding(
        "WG_PERSONAL_ALLOW_REMOTE_MODELS",
        ("personal_allow_remote_models",),
        to_bool,
        "Freigabe, persönliche Inhalte an nicht-lokale Provider zu senden (§11.5)",
    ),
)
