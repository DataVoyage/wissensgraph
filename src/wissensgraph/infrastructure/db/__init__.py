"""Datenbankzugriff: Store-Registry, Engines, Repositories, Migrationen (§7, §20.1)."""

from __future__ import annotations

from wissensgraph.infrastructure.db.migrations import (
    MigrationResult,
    downgrade_store,
    head_revision,
    status,
    upgrade_all,
    upgrade_store,
)
from wissensgraph.infrastructure.db.registry import (
    StoreHealth,
    StoreRegistry,
    UnknownStoreError,
)
from wissensgraph.migrations.context import (
    MigrationError,
    MigrationOptions,
)

__all__ = [
    "MigrationError",
    "MigrationOptions",
    "MigrationResult",
    "StoreHealth",
    "StoreRegistry",
    "UnknownStoreError",
    "downgrade_store",
    "head_revision",
    "status",
    "upgrade_all",
    "upgrade_store",
]
