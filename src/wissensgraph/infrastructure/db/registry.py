"""Store-Registry — der einzige Weg zu einer Datenbankverbindung (§20.1).

Die Trennung zwischen ``personal`` und ``shared`` ist auf vier Ebenen abgesichert (§20.1). Diese
Datei ist die *Anwendungsebene*: "Store-Auflösung ausschließlich über die Registry; kein Codepfad
wählt einen DSN selbst."

Praktisch heißt das: Kein Service, kein Repository und kein Adapter baut je selbst eine Engine.
Sie fragen die Registry nach einem Store-Namen und bekommen eine Verbindung — oder einen Fehler,
wenn der Name nicht konfiguriert ist. Ein Tippfehler im Store-Namen kann so nicht dazu führen,
dass versehentlich der falsche Store geöffnet wird.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from wissensgraph.config.masking import mask_dsn
from wissensgraph.config.schema import Settings, StoreConfig


class UnknownStoreError(KeyError):
    """Es wurde ein Store angefragt, der nicht konfiguriert ist."""

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        self.name = name
        self.known = known
        super().__init__(
            f"Unbekannter Store '{name}'. Konfiguriert sind: {', '.join(known) or '(keiner)'}."
        )


@dataclass(frozen=True)
class StoreHealth:
    """Ergebnis einer Verbindungsprüfung gegen einen Store."""

    store: str
    healthy: bool
    dsn: str
    """Maskierter DSN — die Ausgabe geht in ``/readyz`` und ins Log (§20.2)."""
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für die HTTP-Antwort."""
        return {
            "store": self.store,
            "healthy": self.healthy,
            "dsn": self.dsn,
            "detail": self.detail,
        }


class StoreRegistry:
    """Hält je Store genau eine SQLAlchemy-Engine und gibt sie auf Anfrage heraus.

    Engines werden erst beim ersten Zugriff angelegt. Das hält den Start schnell und sorgt dafür,
    dass ein Prozess, der nur einen Store braucht, nicht am anderen scheitert.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engines: dict[str, Engine] = {}
        self._readonly: dict[str, Engine] = {}

    @property
    def store_names(self) -> tuple[str, ...]:
        """Die Namen aller konfigurierten Stores."""
        return tuple(self._settings.stores)

    def config_of(self, store: str) -> StoreConfig:
        """Die Konfiguration eines Stores.

        Raises:
            UnknownStoreError: Wenn der Store nicht konfiguriert ist.
        """
        try:
            return self._settings.stores[store]
        except KeyError as exc:
            raise UnknownStoreError(store, self.store_names) from exc

    def engine(self, store: str) -> Engine:
        """Die Engine eines Stores; wird beim ersten Aufruf angelegt.

        Raises:
            UnknownStoreError: Wenn der Store nicht konfiguriert ist.
        """
        if store not in self._engines:
            config = self.config_of(store)
            self._engines[store] = create_engine(
                config.dsn,
                pool_pre_ping=True,
                future=True,
                **self._engine_options(config.dsn),
            )
        return self._engines[store]

    def readonly_engine(self, store: str) -> Engine:
        """Eine Engine, über die sich in diesem Store nichts schreiben lässt (§20.1).

        §20.1 verlangt als fünften Guard-Test: "Die MCP-Verbindung auf ``shared`` muss bei jedem
        Schreibversuch einen Datenbankfehler erzeugen." *Datenbankfehler* ist dabei der Punkt —
        eine Prüfung im Anwendungscode wäre nur so gut wie der Codepfad, der sie aufruft. Ein
        lesender Zugang, der auch dann noch lesend ist, wenn jemand ihn falsch benutzt, ist eine
        Eigenschaft der Verbindung und keine Verabredung.

        Zwei Ausprägungen, je nach Konfiguration:

        * Mit ``readonly_dsn`` meldet sich die Verbindung als eigene Datenbankrolle an, die nur
          ``SELECT`` darf. Das ist die Form für den Betrieb — sie hält auch dann, wenn der
          Prozess selbst kompromittiert ist.
        * Ohne ``readonly_dsn`` wird dieselbe Rolle benutzt, aber mit erzwungenem
          ``default_transaction_read_only``. Jede schreibende Anweisung scheitert damit in
          PostgreSQL. Das ist schwächer — wer die Einstellung kennt, kann sie zurücksetzen —,
          aber es ist ohne jede Einrichtung vorhanden und fängt jeden Irrtum ab.

        Raises:
            UnknownStoreError: Wenn der Store nicht konfiguriert ist.
        """
        if store not in self._readonly:
            config = self.config_of(store)
            dsn = config.readonly_dsn or config.dsn
            optionen: dict[str, Any] = dict(self._engine_options(dsn))
            if dsn.startswith("postgresql"):
                connect_args: dict[str, Any] = dict(optionen.get("connect_args", {}))
                connect_args["options"] = "-c default_transaction_read_only=on"
                optionen["connect_args"] = connect_args
            self._readonly[store] = create_engine(dsn, pool_pre_ping=True, future=True, **optionen)
        return self._readonly[store]

    def _engine_options(self, dsn: str) -> dict[str, object]:
        """Engine-Optionen, die vom Datenbank-Dialekt abhängen.

        SQLite kennt weder ``pool_size`` noch ``connect_timeout``; im Betrieb läuft ohnehin
        PostgreSQL, SQLite dient nur den Tests. Die Fallunterscheidung hält beides lauffähig,
        ohne dass Tests eine Sonderbehandlung im Produktivpfad erzwingen.
        """
        if not dsn.startswith("postgresql"):
            return {}
        return {
            "pool_size": self._settings.database.pool_size,
            "connect_args": {"connect_timeout": self._settings.database.connect_timeout_seconds},
        }

    def check(self, store: str) -> StoreHealth:
        """Prüft die Verbindung zu einem Store mit einem minimalen ``SELECT 1``.

        Gibt bei einem Fehler kein Exception-Objekt weiter, sondern einen negativen
        :class:`StoreHealth` — ``/readyz`` soll den Zustand *melden*, nicht selbst abstürzen.
        """
        config = self.config_of(store)
        masked = mask_dsn(config.dsn)
        try:
            with self.engine(store).connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            return StoreHealth(store=store, healthy=False, dsn=masked, detail=_short_reason(exc))
        return StoreHealth(store=store, healthy=True, dsn=masked)

    def check_all(self) -> tuple[StoreHealth, ...]:
        """Prüft alle konfigurierten Stores."""
        return tuple(self.check(name) for name in self.store_names)

    def dispose(self) -> None:
        """Schließt alle Verbindungspools. Beim Herunterfahren eines Prozesses aufzurufen."""
        for engine in (*self._engines.values(), *self._readonly.values()):
            engine.dispose()
        self._engines.clear()
        self._readonly.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.dispose()


def _short_reason(exc: SQLAlchemyError) -> str:
    """Kürzt eine SQLAlchemy-Fehlermeldung auf die erste Zeile.

    Der vollständige Text enthält Verbindungsparameter und ist für eine Health-Antwort zu lang;
    die erste Zeile benennt die Ursache ausreichend.
    """
    return str(exc).splitlines()[0][:200]
