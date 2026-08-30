"""Ports — die Protokolle, gegen die die Dienste programmiert sind (§4.2, §23).

Ein Port beschreibt, *was* gebraucht wird, nie *womit* es erfüllt wird. Die Umsetzungen liegen
in :mod:`wissensgraph.infrastructure`; ein import-linter-Kontrakt stellt sicher, dass die
Abhängigkeit nur in diese eine Richtung zeigt.
"""

from __future__ import annotations

from wissensgraph.ports.queue import Job, JobQueue
from wissensgraph.ports.repositories import (
    ChangeLogRepository,
    ConceptRepository,
    EdgeRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from wissensgraph.ports.runs import (
    RunRepository,
    SourceBusy,
    SourceCursorRepository,
    SourceCursorState,
    SourceLocks,
)
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    HealthState,
    HealthStatus,
    NotSupported,
    SourceAdapter,
    SourceDocument,
    SourceError,
    SourceUnavailable,
)

__all__ = [
    "AdapterCapabilities",
    "ChangeLogRepository",
    "ConceptRepository",
    "Cursor",
    "EdgeRepository",
    "HealthState",
    "HealthStatus",
    "Job",
    "JobQueue",
    "NotSupported",
    "RunRepository",
    "SourceAdapter",
    "SourceBusy",
    "SourceCursorRepository",
    "SourceCursorState",
    "SourceDocument",
    "SourceError",
    "SourceLocks",
    "SourceUnavailable",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
