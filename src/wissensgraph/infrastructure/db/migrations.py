"""Ausführung der Migrationen beider Stores (§5.5, §7.3).

Diese Datei ist die einzige Stelle, die Alembic startet. Sie erledigt drei Dinge, die ein
``alembic upgrade`` von der Kommandozeile nicht könnte:

1. Sie holt DSN und Vektordimension aus der geprüften Konfiguration statt aus der Umgebung und
   hält damit die Präzedenzkette aus §6.2 ein.
2. Sie geht über die Store-Registry und wählt keinen DSN selbst (§20.1).
3. Sie hält während des Laufs einen PostgreSQL-Advisory-Lock. §5.5 verlangt, dass Migrationen
   nie parallel laufen; ohne den Lock würden zwei gleichzeitig startende api-Container dieselbe
   Migration nebeneinander ausführen.
"""

from __future__ import annotations

import io
import time
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db.registry import StoreRegistry
from wissensgraph.migrations import SCRIPT_LOCATION
from wissensgraph.migrations.context import (
    ATTRIBUTE_KEY,
    MigrationError,
    MigrationOptions,
)
from wissensgraph.observability.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    """Was ein Migrationslauf an einem Store verändert hat."""

    store: str
    revision_before: str | None
    revision_after: str | None

    @property
    def changed(self) -> bool:
        """Ob der Lauf etwas verändert hat.

        Ein zweiter Aufruf auf einer bereits migrierten Datenbank liefert ``False`` — das ist die
        Wiederholbarkeit, die §24 für Stufe 1 verlangt.
        """
        return self.revision_before != self.revision_after

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für Log und CLI-Ausgabe."""
        return {
            "store": self.store,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "changed": self.changed,
        }


# ---------------------------------------------------------------------------
# Alembic-Konfiguration
# ---------------------------------------------------------------------------


def build_options(settings: Settings, store: str, registry: StoreRegistry) -> MigrationOptions:
    """Baut die Optionen eines Migrationslaufs aus der geprüften Konfiguration."""
    return MigrationOptions(
        store=store,
        dsn=registry.config_of(store).dsn,
        embedding_dim=settings.embedding_dim,
    )


def alembic_config(options: MigrationOptions) -> Config:
    """Baut die Alembic-Konfiguration im Speicher.

    Bewusst ohne ``alembic.ini``: Eine Datei im Repository-Wurzelverzeichnis wäre im
    Container-Image nicht vorhanden und würde vom Arbeitsverzeichnis abhängen. Alles, was Alembic
    braucht, steht ohnehin in der Konfiguration des Systems.
    """
    config = _script_config()
    config.set_main_option("version_locations", str(SCRIPT_LOCATION / "versions"))
    config.attributes[ATTRIBUTE_KEY] = options
    return config


def _script_config() -> Config:
    """Die gemeinsame Grundlage: Wo die Versionsskripte liegen.

    ``path_separator = os`` ist nötig, weil Alembic Pfadlisten sonst an Leerzeichen und Kommata
    zerlegt — unter Windows liegt das Repository regelmäßig unter einem Pfad mit Leerzeichen, und
    ein solcher Pfad würde dabei in mehrere unbrauchbare Teile zerfallen.
    """
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    config.set_main_option("path_separator", "os")
    return config


def head_revision() -> str | None:
    """Die neueste im Paket vorhandene Revision — das Ziel eines ``upgrade head``."""
    return ScriptDirectory.from_config(_script_config()).get_current_head()


# ---------------------------------------------------------------------------
# Advisory-Lock (§5.5)
# ---------------------------------------------------------------------------


def advisory_lock_key() -> int:
    """Der Schlüssel des Migrations-Locks.

    Aus einem Namensraum abgeleitet statt frei gewählt: So kollidiert er nicht zufällig mit einem
    Lock einer anderen Anwendung, die dieselbe Datenbank benutzt. Der Wert ist über Prozesse und
    Plattformen hinweg stabil, weil ``zlib.crc32`` definiert ist und nicht wie ``hash()`` je
    Prozess variiert.
    """
    return zlib.crc32(defaults.MIGRATION_LOCK_NAMESPACE.encode("utf-8"))


@contextmanager
def advisory_lock(connection: Connection, *, timeout_seconds: int | None = None) -> Iterator[bool]:
    """Hält den Migrations-Lock für die Dauer des Blocks.

    Liefert ``True``, wenn tatsächlich ein Lock gehalten wird, und ``False`` bei einer Datenbank
    ohne Advisory-Locks. Letzteres betrifft nur SQLite in den Unit-Tests; im Betrieb läuft
    ausschließlich PostgreSQL.

    Es wird ``pg_try_advisory_lock`` in einer Schleife benutzt statt des blockierenden
    ``pg_advisory_lock``: Ein Container, der beim Start unbegrenzt auf einen Lock wartet, sieht
    aus wie ein Container, der hängt. Mit Frist bricht er stattdessen mit einer Begründung ab.

    Raises:
        MigrationError: Wenn der Lock innerhalb der Frist nicht zu bekommen war.
    """
    if connection.dialect.name != "postgresql":
        yield False
        return

    key = advisory_lock_key()
    limit = defaults.MIGRATION_LOCK_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + limit

    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise MigrationError(
                f"Der Migrations-Lock war nach {limit} s nicht zu bekommen. Vermutlich läuft eine "
                f"Migration bereits in einem anderen Container (§5.5). Der Lauf wurde abgebrochen, "
                f"ohne etwas zu verändern."
            )
        _log.info("migration.lock.warte", store_lock_key=key)
        time.sleep(1.0)

    try:
        yield True
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


# ---------------------------------------------------------------------------
# Lesen des Zustands
# ---------------------------------------------------------------------------


def current_revision(connection: Connection, options: MigrationOptions) -> str | None:
    """Die Revision, auf der ein Store gerade steht; ``None`` bei einer leeren Datenbank."""
    context = MigrationContext.configure(
        connection=connection, opts={"version_table": options.version_table}
    )
    return context.get_current_revision()


def status(settings: Settings, registry: StoreRegistry) -> tuple[MigrationResult, ...]:
    """Der Migrationsstand aller Stores, ohne etwas zu verändern.

    ``revision_after`` ist hier die im Paket vorhandene Ziel-Revision. ``changed`` bedeutet
    entsprechend "es stehen Migrationen aus" — genau die Frage, die ``wg migrate --check``
    beantwortet.
    """
    head = head_revision()
    results = []
    for store in registry.store_names:
        options = build_options(settings, store, registry)
        with registry.engine(store).connect() as connection:
            before = current_revision(connection, options)
        results.append(MigrationResult(store=store, revision_before=before, revision_after=head))
    return tuple(results)


# ---------------------------------------------------------------------------
# Ausführen
# ---------------------------------------------------------------------------


def _run(
    settings: Settings,
    registry: StoreRegistry,
    store: str,
    revision: str,
    *,
    downgrade: bool,
) -> MigrationResult:
    """Führt einen Migrationsschritt unter Advisory-Lock aus und meldet, was sich geändert hat.

    Lock und Migration laufen auf derselben Verbindung. Anders hätte der Lock keine Wirkung: Er
    ist an die Datenbanksitzung gebunden, nicht an den Prozess.
    """
    options = build_options(settings, store, registry)
    config = alembic_config(options)
    schritt = command.downgrade if downgrade else command.upgrade

    with registry.engine(store).connect() as connection, advisory_lock(connection):
        before = current_revision(connection, options)
        config.attributes["connection"] = connection
        schritt(config, revision)
        if connection.in_transaction():
            connection.commit()
        after = current_revision(connection, options)

    result = MigrationResult(store=store, revision_before=before, revision_after=after)
    _log.info("migration.abgeschlossen", **result.as_dict())
    return result


def upgrade_store(
    settings: Settings,
    registry: StoreRegistry,
    store: str,
    *,
    revision: str = "head",
) -> MigrationResult:
    """Bringt einen Store auf den Stand einer Revision.

    Idempotent: Steht der Store bereits auf der Zielrevision, passiert nichts und
    :attr:`MigrationResult.changed` ist ``False``.
    """
    return _run(settings, registry, store, revision, downgrade=False)


def downgrade_store(
    settings: Settings,
    registry: StoreRegistry,
    store: str,
    *,
    revision: str,
) -> MigrationResult:
    """Nimmt Migrationen eines Stores bis zu einer Revision zurück.

    ``revision`` hat bewusst keinen Default. Ein Rückbau löscht Tabellen samt Inhalt; welche
    Revision gemeint ist, soll derjenige aussprechen, der ihn auslöst, und nicht aus einer
    Voreinstellung folgen.
    """
    return _run(settings, registry, store, revision, downgrade=True)


def upgrade_all(
    settings: Settings,
    registry: StoreRegistry,
    *,
    revision: str = "head",
) -> tuple[MigrationResult, ...]:
    """Migriert alle konfigurierten Stores nacheinander.

    Nacheinander und nicht nebenläufig: Die Läufe sind kurz, und ein Fehler im ersten Store soll
    den zweiten gar nicht erst anfassen.
    """
    return tuple(
        upgrade_store(settings, registry, store, revision=revision)
        for store in registry.store_names
    )


def render_sql(
    settings: Settings,
    registry: StoreRegistry,
    store: str,
    *,
    revision: str = "head",
) -> str:
    """Gibt die Migration als SQL aus, ohne die Datenbank zu berühren.

    Das ist der Trockenlauf aus §19 für Migrationen: Er zeigt, was passieren würde, und lässt
    sich vor einem Lauf gegen eine Produktivdatenbank prüfen. Weil kein Verbindungsaufbau
    stattfindet, beginnt die Ausgabe immer bei der ersten Revision.
    """
    options = build_options(settings, store, registry)
    config = alembic_config(options)
    buffer = io.StringIO()
    # Alembic schreibt das SQL in 'output_buffer' und alles andere nach 'stdout'. Beide zeigen
    # hier auf denselben Puffer, damit der Aufrufer die vollständige Ausgabe als Zeichenkette
    # bekommt und nichts unbemerkt auf der Konsole landet.
    config.output_buffer = buffer
    config.stdout = buffer
    command.upgrade(config, revision, sql=True)
    return buffer.getvalue()
