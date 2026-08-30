"""Die Parameter, die ein Migrationslauf an seine Versionsskripte weitergibt (§7.4).

Alembic-Versionsskripte bekommen normalerweise keine Argumente. Zwei Werte brauchen sie hier
trotzdem:

* den **Store-Namen** — die Invariante ``ck_shared_no_personal_ref`` wird laut §7.4 nur im
  shared-Store angelegt, ansonsten sind beide Schemata identisch;
* die **Vektordimension** — sie stammt aus ``WG_EMBEDDING_DIM`` und steht im Spaltentyp
  ``vector(n)`` (§7.3).

Beides über Umgebungsvariablen direkt im Versionsskript zu lesen, würde die Präzedenzkette aus
§6.2 umgehen: Ein Skript, das selbst ``os.environ`` liest, sieht weder ``config/*.yaml`` noch ein
CLI-Flag. Die Werte kommen deshalb aus der bereits validierten Konfiguration und werden über
``config.attributes`` von Alembic durchgereicht.
"""

from __future__ import annotations

from dataclasses import dataclass

from wissensgraph.config import defaults

#: Schlüssel, unter dem die Optionen in ``alembic.config.Config.attributes`` liegen.
ATTRIBUTE_KEY = "wissensgraph_migration_options"


class MigrationError(RuntimeError):
    """Eine Migration konnte nicht vorbereitet oder nicht ausgeführt werden."""


@dataclass(frozen=True)
class MigrationOptions:
    """Alles, was ein Migrationslauf über sein Ziel wissen muss."""

    store: str
    """Name des Stores, ``'shared'`` oder ``'personal'`` (§7.3)."""

    dsn: str
    """SQLAlchemy-DSN der Zieldatenbank. Nur für den Offline-Modus (``--sql``) nötig."""

    embedding_dim: int
    """Vektordimension aus ``WG_EMBEDDING_DIM``; bestimmt den Spaltentyp ``vector(n)`` (§7.3)."""

    @property
    def version_table(self) -> str:
        """Name der Alembic-Versionstabelle dieses Stores (§7.3)."""
        return f"{defaults.MIGRATION_VERSION_TABLE_PREFIX}{self.store}"

    @property
    def is_shared(self) -> bool:
        """Ob dieser Lauf den geteilten Store migriert.

        Steuert die einzige Abweichung zwischen den beiden Schemata: den CHECK-Constraint, der
        Verweise auf den personal-Store verbietet (§7.4, §20.1).
        """
        return self.store == defaults.STORE_SHARED

    def __post_init__(self) -> None:
        if self.embedding_dim < 1 or self.embedding_dim > defaults.EMBEDDING_DIM_MAX:
            raise MigrationError(
                f"embedding_dim muss zwischen 1 und {defaults.EMBEDDING_DIM_MAX} liegen, "
                f"ist aber {self.embedding_dim}. Der Wert kommt aus WG_EMBEDDING_DIM und geht "
                f"als vector(n) in das Schema ein (§7.3)."
            )


def current_options() -> MigrationOptions:
    """Die Optionen des gerade laufenden Migrationslaufs.

    Aus einem Versionsskript heraus aufzurufen. Der Import von ``alembic.context`` steht bewusst
    in der Funktion: Auf Modulebene ist der Kontext nur gültig, während Alembic tatsächlich läuft.

    Raises:
        MigrationError: Wenn das Skript außerhalb eines von dieser Anwendung gestarteten
            Migrationslaufs ausgeführt wird — etwa über ein direktes ``alembic upgrade``, das die
            Konfiguration nicht kennt.
    """
    from alembic import context

    # Außerhalb eines Laufs ist ``alembic.context`` ein leerer Proxy und kennt sein ``config``
    # noch gar nicht. Der resultierende AttributeError sagt nichts darüber aus, was zu tun ist —
    # deshalb wird er hier in dieselbe Meldung übersetzt wie ein Lauf ohne Optionen.
    try:
        options = context.config.attributes.get(ATTRIBUTE_KEY)
    except AttributeError:
        options = None

    if not isinstance(options, MigrationOptions):
        raise MigrationError(
            "Die Migration wurde ohne Optionen gestartet. Sie ist über 'wg migrate' aufzurufen, "
            "nicht über das alembic-Kommando direkt — nur so stehen Store-Name und "
            "Vektordimension aus der geprüften Konfiguration fest (§6.2)."
        )
    return options
