"""Konfigurationsschicht (§6).

Öffentliche Schnittstelle des Pakets. Der Rest des Systems importiert ausschließlich von hier —
nie direkt aus den Untermodulen —, damit der interne Aufbau der Auflösung austauschbar bleibt.
"""

from __future__ import annotations

from wissensgraph.config.errors import (
    ConfigError,
    ConfigFileError,
    ConfigValidationError,
    PlaceholderResolutionError,
)
from wissensgraph.config.loader import build_settings
from wissensgraph.config.masking import mask_config, mask_dsn
from wissensgraph.config.schema import (
    ApiConfig,
    BudgetConfig,
    ClusteringConfig,
    ConceptTypeConfig,
    DatabaseConfig,
    EdgeKindsConfig,
    LoggingConfig,
    McpConfig,
    OrphansConfig,
    RankingConfig,
    ScopeConfig,
    Settings,
    StoreConfig,
    TraversalConfig,
)

__all__ = [
    "ApiConfig",
    "BudgetConfig",
    "ClusteringConfig",
    "ConceptTypeConfig",
    "ConfigError",
    "ConfigFileError",
    "ConfigValidationError",
    "DatabaseConfig",
    "EdgeKindsConfig",
    "LoggingConfig",
    "McpConfig",
    "OrphansConfig",
    "PlaceholderResolutionError",
    "RankingConfig",
    "ScopeConfig",
    "Settings",
    "StoreConfig",
    "TraversalConfig",
    "build_settings",
    "mask_config",
    "mask_dsn",
]
