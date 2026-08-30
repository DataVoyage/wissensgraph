"""Advisory-Locks je Quelle — die Umsetzung von §10.5.

"Pro Quelle läuft höchstens ein Sync gleichzeitig, abgesichert über einen PostgreSQL-Advisory-Lock
auf dem Quellnamen."

Drei Entscheidungen stecken darin, die den Unterschied zwischen einer wirksamen und einer
scheinbaren Sperre ausmachen:

1. **Eine eigene Verbindung.** Advisory-Locks hängen an der Sitzung. Läge der Lock auf der
   Verbindung einer Arbeitseinheit, fiele er mit deren Ende — also nach dem ersten geschriebenen
   Dokument. Diese Sperre öffnet deshalb ihre eigene Verbindung und hält sie über den ganzen Lauf.
2. **``pg_try_advisory_lock`` statt ``pg_advisory_lock``.** Es wird nicht gewartet. §10.5 verlangt
   eine *Abweisung* ("liefert ``409 Conflict``"), keine Warteschlange. Ein zweiter Aufruf, der
   stumm wartet, sähe für den Aufrufer aus wie ein besonders langsamer Lauf.
3. **Die Zwei-Argument-Form.** ``pg_try_advisory_lock(namensraum, quelle)`` trennt unseren
   Schlüsselraum von dem jeder anderen Anwendung auf derselben Datenbank. Der Migrations-Lock
   benutzt daneben die Ein-Argument-Form (``bigint``) und kann deshalb gar nicht kollidieren.

Auf einer Datenbank ohne Advisory-Locks — SQLite in den Unit-Tests — wird nicht gesperrt. Das ist
kein stiller Verlust, sondern eine Eigenschaft der Umgebung: Die Abnahme aus §24 ("paralleler
Start derselben Quelle wird abgewiesen") wird gegen PostgreSQL geprüft, und die Unit-Tests
benutzen einen Fake, der genau dieselbe Zusicherung im Speicher erfüllt.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from wissensgraph.config import defaults
from wissensgraph.infrastructure.db.registry import StoreRegistry
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.runs import SourceBusy

_log = get_logger(__name__)

#: Grenze des vorzeichenbehafteten 32-Bit-Bereichs, den PostgreSQL für die Zwei-Argument-Form
#: erwartet. ``zlib.crc32`` liefert dagegen vorzeichenlos.
_INT32_GRENZE = 2**31


def lock_key(text_value: str) -> int:
    """Ein stabiler ``int4``-Schlüssel zu einem Namen.

    ``zlib.crc32`` und nicht ``hash()``: Der eingebaute Hash ist je Prozess anders gesalzen. Zwei
    Container würden damit verschiedene Schlüssel für denselben Quellnamen berechnen — und die
    Sperre wäre wirkungslos, ohne dass irgendetwas fehlschlüge.
    """
    wert = zlib.crc32(text_value.encode("utf-8"))
    return wert - 2**32 if wert >= _INT32_GRENZE else wert


class SqlSourceLocks:
    """Sperren je Quelle über PostgreSQL-Advisory-Locks (§10.5)."""

    def __init__(self, registry: StoreRegistry) -> None:
        """
        Args:
            registry: Der einzige Weg zu einer Verbindung (§20.1). Die Sperre wird in dem Store
                genommen, in dem der Lauf auch schreibt — damit sperrt ein Lauf über eine
                persönliche Quelle nicht Läufe im geteilten Store und umgekehrt.
        """
        self._registry = registry
        self._namespace = lock_key(defaults.SYNC_LOCK_NAMESPACE)

    @contextmanager
    def hold(self, *, store: str, name: str) -> Iterator[None]:
        """Hält die Sperre einer Quelle für die Dauer des Blocks.

        Raises:
            SourceBusy: Wenn bereits jemand hält.
        """
        with self._registry.engine(store).connect() as connection:
            if connection.dialect.name != "postgresql":
                # Kein Advisory-Lock verfügbar. Ausdrücklich gemeldet: Eine Sperre, die still
                # nicht sperrt, ist gefährlicher als gar keine.
                _log.debug(
                    "quelle.sperre.uebersprungen", source=name, dialect=connection.dialect.name
                )
                yield
                return

            schluessel = lock_key(name)
            erhalten = connection.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": self._namespace, "key": schluessel},
            ).scalar()
            if not erhalten:
                raise SourceBusy(name)

            _log.debug("quelle.sperre.gehalten", source=name, store=store)
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :key)"),
                    {"namespace": self._namespace, "key": schluessel},
                )
