"""Arbeitseinheit je Store — die Umsetzung von §10.2 Regel 5.

"Der Aufruf ist transaktional: Konzept, Kanten und change_log gemeinsam." Eine Arbeitseinheit
hält dafür genau eine Verbindung mit genau einer Transaktion und reicht sie an ihre drei
Repositories weiter. Kein Repository committet selbst.

Die Verbindung kommt ausschließlich aus der :class:`StoreRegistry`. Damit gibt es im ganzen
System keinen Codepfad, der einen DSN selbst wählt (§20.1, Anwendungsebene).
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Connection

from wissensgraph.infrastructure.db.registry import StoreRegistry
from wissensgraph.infrastructure.db.repositories import (
    SqlChangeLogRepository,
    SqlConceptRepository,
    SqlEdgeRepository,
    SqlRunRepository,
    SqlSourceCursorRepository,
)


class SqlUnitOfWork:
    """Eine Transaktion auf einem Store, mit den drei Repositories dieses Stores.

    Beim regulären Verlassen des Kontextmanagers wird festgeschrieben, bei einer Ausnahme
    zurückgerollt. Ein halb geschriebener Vorgang — Konzept ja, Journal nein — ist damit
    ausgeschlossen.
    """

    def __init__(self, registry: StoreRegistry, store: str) -> None:
        self._registry = registry
        self._store = store
        self._connection: Connection | None = None
        self._concepts: SqlConceptRepository | None = None
        self._edges: SqlEdgeRepository | None = None
        self._changes: SqlChangeLogRepository | None = None
        self._runs: SqlRunRepository | None = None
        self._cursors: SqlSourceCursorRepository | None = None

    @property
    def store(self) -> str:
        """Der Store dieser Arbeitseinheit."""
        return self._store

    @property
    def connection(self) -> Connection:
        """Die offene Verbindung.

        Raises:
            RuntimeError: Wenn die Arbeitseinheit nicht als Kontextmanager betreten wurde.
        """
        if self._connection is None:
            raise RuntimeError(
                "Die Arbeitseinheit ist nicht geöffnet. Sie ist als Kontextmanager zu benutzen "
                "('with SqlUnitOfWork(registry, store) as uow:') — nur so ist der Abschluss der "
                "Transaktion garantiert."
            )
        return self._connection

    @property
    def concepts(self) -> SqlConceptRepository:
        """Das Konzept-Repository dieses Stores."""
        if self._concepts is None:
            self._concepts = SqlConceptRepository(self.connection, self._store)
        return self._concepts

    @property
    def edges(self) -> SqlEdgeRepository:
        """Das Kanten-Repository dieses Stores."""
        if self._edges is None:
            self._edges = SqlEdgeRepository(self.connection, self._store)
        return self._edges

    @property
    def changes(self) -> SqlChangeLogRepository:
        """Das Journal-Repository dieses Stores."""
        if self._changes is None:
            self._changes = SqlChangeLogRepository(self.connection, self._store)
        return self._changes

    @property
    def runs(self) -> SqlRunRepository:
        """Das Lauf-Repository dieses Stores."""
        if self._runs is None:
            self._runs = SqlRunRepository(self.connection, self._store)
        return self._runs

    @property
    def cursors(self) -> SqlSourceCursorRepository:
        """Das Cursor-Repository dieses Stores."""
        if self._cursors is None:
            self._cursors = SqlSourceCursorRepository(self.connection, self._store)
        return self._cursors

    def commit(self) -> None:
        """Schreibt die laufende Transaktion fest."""
        self.connection.commit()

    def rollback(self) -> None:
        """Verwirft die laufende Transaktion."""
        self.connection.rollback()

    def __enter__(self) -> Self:
        self._connection = self._registry.engine(self._store).connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        self._connection = None
        self._concepts = self._edges = self._changes = None
        self._runs = self._cursors = None
        if connection is None:  # pragma: no cover — nur bei doppeltem Verlassen erreichbar
            return
        try:
            if exc_type is None:
                connection.commit()
            else:
                connection.rollback()
        finally:
            connection.close()


class UnitOfWorkFactory:
    """Erzeugt Arbeitseinheiten — der einzige Weg zu einer schreibenden Transaktion.

    Die Dienste bekommen diese Fabrik statt einer Registry. Der Unterschied ist klein, aber er
    hält den Zugriff eng: Ein Dienst kann eine Transaktion auf einem *benannten* Store beginnen,
    aber keine Engine anfassen und keinen DSN lesen.
    """

    def __init__(self, registry: StoreRegistry) -> None:
        self._registry = registry

    @property
    def store_names(self) -> tuple[str, ...]:
        """Die Namen aller konfigurierten Stores."""
        return self._registry.store_names

    def __call__(self, store: str) -> SqlUnitOfWork:
        """Eine noch nicht geöffnete Arbeitseinheit für einen Store.

        Raises:
            UnknownStoreError: Wenn der Store nicht konfiguriert ist.
        """
        self._registry.config_of(store)
        return SqlUnitOfWork(self._registry, store)
