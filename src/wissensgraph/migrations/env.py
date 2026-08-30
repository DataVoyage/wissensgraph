"""Alembic-Umgebung für beide Stores.

Diese Datei wird von Alembic ausgeführt, nicht importiert. Sie hält sich deshalb kurz und
entscheidet nur eines: ob gegen eine bestehende Verbindung migriert wird (Normalfall) oder ob
lediglich SQL ausgegeben werden soll (``wg migrate --sql``, der Trockenlauf aus §19).

Der Verbindungsaufbau selbst gehört ausdrücklich **nicht** hierher. Er passiert in
``wissensgraph.infrastructure.db.migrations``, weil dort auch der Advisory-Lock aus §5.5 gehalten
wird — Lock und Migration müssen in derselben Datenbanksitzung laufen, sonst schützt der Lock
nichts.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from wissensgraph.migrations.context import (
    MigrationError,
    current_options,
)


def run_migrations_offline() -> None:
    """Gibt die Migration als SQL aus, ohne eine Datenbank zu berühren."""
    options = current_options()
    context.configure(
        url=options.dsn,
        version_table=options.version_table,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Führt die Migration auf der vom Aufrufer übergebenen Verbindung aus."""
    options = current_options()
    connection = context.config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise MigrationError(
            "Es wurde keine Datenbankverbindung übergeben. Der Migrationslauf wird über "
            "'wg migrate' gestartet; nur dort wird die Verbindung samt Advisory-Lock aufgebaut "
            "(§5.5)."
        )

    context.configure(
        connection=connection,
        version_table=options.version_table,
        target_metadata=None,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
