"""Gemeinsame Grundlage der mitgelieferten Adapter (§8.2).

Was hier steht, ist genau das, was jede Quelle gleich macht und deshalb nicht dreimal
geschrieben werden soll: die Fähigkeitsflags in Ausnahmen übersetzen, den Cursor erst am Ende
einer vollständigen Iteration fortschreiben, HTTP-Anfragen drosseln und wiederholen.

Der Cursor ist der subtilste Teil. §22.3 verlangt: "Netzwerkfehler mitten in der Iteration lassen
den Cursor unverändert." Ein Adapter, der die Marke schon nach jeder Seite fortschreibt, verliert
bei einem Abbruch stillschweigend den Rest des Bestands — beim nächsten Lauf beginnt er hinter
den nie gelesenen Objekten. :meth:`BaseAdapter._durchreichen` setzt die neue Marke deshalb in der
letzten Zeile des Generators; bricht die Iteration vorher ab, wird sie nie erreicht.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx

from wissensgraph.config import defaults
from wissensgraph.config.sources import SourceConfig
from wissensgraph.infrastructure.adapters.mapping import DocumentMapping
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    HealthState,
    HealthStatus,
    NotSupported,
    SourceDocument,
    SourceError,
    SourceObjectNotFound,
    SourceUnavailable,
)

_log = get_logger(__name__)

#: Schlüssel im Cursor. Er ist adapterdefiniert (§8.2) — der Kern liest ihn nie; dass alle
#: mitgelieferten Adapter denselben benutzen, ist eine Bequemlichkeit, keine Festlegung.
CURSOR_UPDATED_AFTER = "updated_after"


class BaseAdapter:
    """Die Hälfte eines Adapters, die nichts mit der konkreten Quelle zu tun hat."""

    #: Registry-Schlüssel. Unterklassen setzen ihn (§8.3).
    name: str = ""

    #: Was diese Umsetzung kann (§8.2). Unterklassen setzen sie.
    capabilities: AdapterCapabilities = AdapterCapabilities()

    def __init__(self) -> None:
        self._config: SourceConfig | None = None
        self._mapping = DocumentMapping({})
        self._cursor = Cursor()

    # -- Konfiguration ----------------------------------------------------------

    def configure(self, cfg: SourceConfig) -> None:
        """Übernimmt die Konfiguration und übersetzt die Mapping-Ausdrücke sofort (§8.4)."""
        self._config = cfg
        self._mapping = DocumentMapping(cfg.mapping, source=cfg.name)

    @property
    def config(self) -> SourceConfig:
        """Die Konfiguration dieser Quellinstanz.

        Raises:
            SourceError: Wenn der Adapter noch nicht konfiguriert wurde. Das ist ein
                Programmfehler und keine Quellstörung — die Registry ruft ``configure()`` vor
                jedem Zugriff auf (§8.3).
        """
        if self._config is None:
            raise SourceError(
                f"Adapter '{self.name}' wurde benutzt, bevor configure() aufgerufen wurde."
            )
        return self._config

    @property
    def mapping(self) -> DocumentMapping:
        """Die geparsten Mapping-Ausdrücke dieser Quelle."""
        return self._mapping

    # -- Cursor -----------------------------------------------------------------

    def next_cursor(self) -> Cursor:
        """Die Marke nach der zuletzt *vollständig* durchlaufenen Iteration (§22.3)."""
        return self._cursor

    @staticmethod
    def cursor_since(cursor: Cursor | None) -> datetime | None:
        """Den Zeitpunkt aus einem Cursor lesen; ein unbrauchbarer Wert gilt als "kein Cursor".

        Ein kaputter Cursor darf keinen Lauf verhindern. Der schlimmste Fall ist ein
        Vollabgleich — teuer, aber korrekt; ein Abbruch wäre beides nicht.
        """
        if cursor is None or cursor.is_empty:
            return None
        roh = cursor.value.get(CURSOR_UPDATED_AFTER)
        if not isinstance(roh, str):
            return None
        try:
            return datetime.fromisoformat(roh)
        except ValueError:
            _log.warning("adapter.cursor_unlesbar", value=roh)
            return None

    def _durchreichen(
        self, documents: Iterator[SourceDocument], since: datetime | None
    ) -> Iterator[SourceDocument]:
        """Filtert nach dem Cursor und schreibt die neue Marke erst am Ende fort.

        Der Filter liegt hier und nicht in der Quelle, weil er auch dann gelten muss, wenn eine
        Quelle serverseitig nur grob filtern kann — ein Objekt zweimal auszuliefern ist erlaubt
        (das Upsert erkennt es am Hash, §10.2 Regel 3), eines auszulassen nicht.
        """
        hoch = since
        for document in documents:
            geaendert = document.updated_at
            if since is not None and geaendert is not None and geaendert <= since:
                continue
            if geaendert is not None and (hoch is None or geaendert > hoch):
                hoch = geaendert
            yield document
        self._cursor = Cursor(value={CURSOR_UPDATED_AFTER: hoch.isoformat()} if hoch else {})

    # -- Fähigkeiten (§8.2 Regel 3) --------------------------------------------

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Standardverhalten ohne die Fähigkeit ``deletions``.

        Raises:
            NotSupported: Immer — eine Unterklasse mit dem Flag überschreibt die Methode.
        """
        raise NotSupported(
            f"Die Quelle '{self.name}' meldet keine Löschungen "
            f"(capabilities.deletions = false, §8.2)."
        )

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Standardverhalten ohne die Fähigkeit ``single_fetch``.

        Raises:
            NotSupported: Immer — eine Unterklasse mit dem Flag überschreibt die Methode.
        """
        raise NotSupported(
            f"Die Quelle '{self.name}' kann kein Einzelobjekt holen "
            f"(capabilities.single_fetch = false, §8.2)."
        )

    def close(self) -> None:
        """Gibt gehaltene Ressourcen frei. Ohne solche eine leere Zusage."""


class HttpSourceAdapter(BaseAdapter):
    """Ein Adapter, der sein Quellsystem über HTTP anspricht.

    Drosselung und Wiederholung stehen hier, weil §8.2 Regel 5 sie dem Adapter zuweist: "Rate-
    Limits und Retries behandelt der Adapter selbst, mit Werten aus seiner Config." Ein
    aufrufender Dienst, der das täte, müsste wissen, welche Antwort einer Quelle ein Rate-Limit
    ist — und wüsste damit mehr über die Quelle, als der Kontrakt vorsieht.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[SourceConfig], httpx.Client] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """
        Args:
            client_factory: Baut den HTTP-Client aus der Konfiguration. Als Parameter, damit ein
                Test den Mock-Server im selben Prozess ansprechen kann, ohne Netzwerk und ohne
                dass der Adapter davon etwas merkt.
            sleep: Die Wartefunktion des Backoffs. Als Parameter, damit ein Test das
                Wiederholverhalten prüfen kann, ohne wirklich zu warten.
        """
        super().__init__()
        self._client_factory = client_factory or _default_client
        self._sleep = sleep or time.sleep
        self._client: httpx.Client | None = None
        self._letzte_anfrage: float | None = None

    # -- Verbindung -------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        """Der HTTP-Client dieser Quelle; wird beim ersten Zugriff gebaut."""
        if self._client is None:
            self._client = self._client_factory(self.config)
        return self._client

    def close(self) -> None:
        """Schließt den HTTP-Client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def health(self) -> HealthStatus:
        """Prüft die Erreichbarkeit über den Endpunkt, den die Unterklasse dafür nennt."""
        pfad = self.health_path()
        try:
            self.get(pfad)
        except SourceError as exc:
            return HealthStatus(state=HealthState.UNHEALTHY, detail=str(exc))
        basis = self.config.connection.base_url or "(ohne base_url)"
        return HealthStatus(state=HealthState.HEALTHY, detail=f"erreichbar unter {basis}")

    def health_path(self) -> str:
        """Der Pfad, dessen Erreichbarkeit als Gesundheitsprüfung gilt."""
        raise NotImplementedError

    # -- Anfragen ---------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Eine GET-Anfrage mit Drosselung, Wiederholung und JSON-Auswertung.

        Raises:
            SourceUnavailable: Wenn alle erlaubten Versuche an einem vorübergehenden Fehler
                scheitern (429, 5xx, Timeout, Verbindungsabbruch).
            SourceError: Bei einer dauerhaft fehlerhaften Antwort (4xx außer 429) oder einem
                Rumpf, der kein JSON ist. Ein erneuter Versuch würde daran nichts ändern.
        """
        verbindung = self.config.connection
        letzter: str = ""
        for versuch in range(verbindung.retries + 1):
            self._drosseln()
            try:
                antwort = self.client.get(path, params=params)
            except httpx.HTTPError as exc:
                letzter = f"{type(exc).__name__}: {exc}"
            else:
                if antwort.status_code not in defaults.SOURCE_RETRY_STATUS_CODES:
                    return _json_von(antwort, self.name)
                letzter = f"HTTP {antwort.status_code}"
                self._warten(versuch, antwort)
                continue
            self._warten(versuch, None)

        raise SourceUnavailable(
            f"Quelle '{self.config.name}' antwortet nicht: {letzter} "
            f"(nach {verbindung.retries + 1} Versuchen auf '{path}')."
        )

    def _drosseln(self) -> None:
        """Hält den Mindestabstand zwischen zwei Anfragen ein (``rate_limit_per_second``)."""
        rate = self.config.connection.rate_limit_per_second
        if rate <= 0:
            return
        abstand = 1.0 / rate
        jetzt = time.monotonic()
        if self._letzte_anfrage is not None:
            rest = abstand - (jetzt - self._letzte_anfrage)
            if rest > 0:
                self._sleep(rest)
        self._letzte_anfrage = time.monotonic()

    def _warten(self, versuch: int, antwort: httpx.Response | None) -> None:
        """Wartet vor dem nächsten Versuch — exponentiell, begrenzt, ``Retry-After`` schlägt alles.

        Ein Server, der eine Wartezeit vorgibt, weiß es besser als eine Formel; ihn zu ignorieren
        ist der schnellste Weg, ein Rate-Limit in eine Sperre zu verwandeln.
        """
        wartezeit = min(
            defaults.SOURCE_BACKOFF_INITIAL_SECONDS * defaults.SOURCE_BACKOFF_FACTOR**versuch,
            defaults.SOURCE_BACKOFF_MAX_SECONDS,
        )
        vorgabe = _retry_after(antwort)
        if vorgabe is not None:
            wartezeit = min(vorgabe, defaults.SOURCE_BACKOFF_MAX_SECONDS)
        _log.info(
            "adapter.backoff",
            source=self.config.name,
            attempt=versuch + 1,
            wait_seconds=round(wartezeit, 3),
            status=None if antwort is None else antwort.status_code,
        )
        self._sleep(wartezeit)


def _default_client(cfg: SourceConfig) -> httpx.Client:
    """Baut den HTTP-Client aus der Konfiguration — ohne ein Literal im Code (§6.1 Regel 1)."""
    verbindung = cfg.connection
    if not verbindung.base_url:
        raise SourceError(
            f"Quelle '{cfg.name}' hat keine base_url. Sie ist der einzige Unterschied zwischen "
            f"Mock und Live (§9.4) und muss in sources.yaml oder als ENV gesetzt sein."
        )
    kopfzeilen = {"Accept": "application/json"}
    if verbindung.token:
        kopfzeilen["Authorization"] = f"Bearer {verbindung.token}"
    return httpx.Client(
        base_url=verbindung.base_url,
        headers=kopfzeilen,
        timeout=verbindung.timeout_seconds,
        verify=verbindung.verify_tls,
    )


def _retry_after(antwort: httpx.Response | None) -> float | None:
    """Liest ``Retry-After`` als Sekundenzahl; alles andere wird ignoriert."""
    if antwort is None:
        return None
    roh = antwort.headers.get(defaults.SOURCE_RETRY_AFTER_HEADER)
    if roh is None:
        return None
    try:
        return max(0.0, float(roh))
    except ValueError:
        # Die Kopfzeile darf laut RFC auch ein Datum enthalten. Das auszuwerten hilft hier
        # nichts: Der berechnete Backoff ist die sichere Vorgabe.
        return None


def _json_von(antwort: httpx.Response, adapter: str) -> Any:
    """Wertet eine Antwort aus und macht aus einem Fehlerstatus eine sprechende Ausnahme."""
    if antwort.status_code == 404:
        raise SourceObjectNotFound(
            f"Adapter '{adapter}': '{antwort.request.url}' gibt es nicht (HTTP 404)."
        )
    if antwort.status_code >= 400:
        raise SourceError(
            f"Adapter '{adapter}': HTTP {antwort.status_code} auf "
            f"'{antwort.request.url}'. Ein erneuter Versuch ändert daran nichts."
        )
    try:
        return antwort.json()
    except ValueError as exc:
        raise SourceError(
            f"Adapter '{adapter}': Die Antwort auf '{antwort.request.url}' ist kein JSON."
        ) from exc
