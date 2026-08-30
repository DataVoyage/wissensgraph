"""Datenbankzugriff: Store-Registry, Engines, Repositories, Migrationen (§7, §20.1)."""

from __future__ import annotations

from wissensgraph.infrastructure.db.locks import SqlSourceLocks
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
from wissensgraph.infrastructure.db.repositories import (
    SqlChangeLogRepository,
    SqlConceptRepository,
    SqlEdgeRepository,
    SqlRunRepository,
    SqlSourceCursorRepository,
    StoreMismatchError,
)
from wissensgraph.infrastructure.db.uow import SqlUnitOfWork, UnitOfWorkFactory
from wissensgraph.migrations.context import (
    MigrationError,
    MigrationOptions,
)

__all__ = [
    "MigrationError",
    "MigrationOptions",
    "MigrationResult",
    "SqlChangeLogRepository",
    "SqlConceptRepository",
    "SqlEdgeRepository",
    "SqlRunRepository",
    "SqlSourceCursorRepository",
    "SqlSourceLocks",
    "SqlUnitOfWork",
    "StoreHealth",
    "StoreMismatchError",
    "StoreRegistry",
    "UnitOfWorkFactory",
    "UnknownStoreError",
    "downgrade_store",
    "head_revision",
    "status",
    "upgrade_all",
    "upgrade_store",
]
