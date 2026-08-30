"""Zwischenspeicher der Modellantworten (§11.6).

"Caching — Schlüssel: SHA-256 über ``task`` + ``model_key`` + normalisierten Prompt/Text; Ablage
in Redis; Treffer werden als ``cache_hit`` gezählt."

Zwei Umsetzungen, und die zweite ist kein Testkrücke: Ohne Broker soll ein ``wg embed`` auf einem
Entwicklerrechner trotzdem laufen und innerhalb desselben Prozesses nicht zweimal dasselbe
einbetten. Der Unterschied ist die Lebensdauer, nicht die Wirkung.

**Ein Cache-Fehler ist kein Aufruf-Fehler.** Beide Umsetzungen schlucken ihre eigenen Ausnahmen:
Ein nicht erreichbarer Redis macht das System langsamer und teurer, aber nicht falsch. Ein Lauf,
der daran abbräche, hätte die Verhältnismäßigkeit verloren.
"""

from __future__ import annotations

from typing import Any

from wissensgraph.observability.logging import get_logger

_log = get_logger(__name__)


class MemoryResponseCache:
    """Ein Zwischenspeicher, der so lange lebt wie der Prozess.

    Ohne Verfallszeit — sie wäre wirkungslos: Der Prozess endet lange vor den 168 Stunden, die
    §11.4 als Voreinstellung nennt. Der Parameter wird trotzdem entgegengenommen, damit beide
    Umsetzungen denselben Port erfüllen.
    """

    def __init__(self) -> None:
        self._werte: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Der abgelegte Wert, oder ``None``."""
        return self._werte.get(key)

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Legt einen Wert ab."""
        self._werte[key] = value

    def __len__(self) -> int:
        """Wie viele Antworten abgelegt sind — für Tests und ``wg doctor``."""
        return len(self._werte)


class RedisResponseCache:
    """Der Zwischenspeicher aus §11.6 in Redis.

    Er teilt sich die Verbindung nicht mit der Job-Queue, obwohl beide dieselbe URL benutzen: Ein
    blockierendes ``BLPOP`` des Workers und ein ``GET`` des Routers auf derselben Verbindung
    kämen sich in die Quere.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any | None = None

    def _verbindung(self) -> Any:
        """Die Verbindung, beim ersten Zugriff aufgebaut."""
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def get(self, key: str) -> str | None:
        """Der abgelegte Wert, oder ``None`` — auch bei nicht erreichbarem Redis."""
        try:
            wert = self._verbindung().get(key)
        except Exception as exc:
            _log.warning("cache.nicht_erreichbar", error=str(exc))
            return None
        return None if wert is None else str(wert)

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Legt einen Wert mit Verfallszeit ab; ein Fehlschlag wird nur protokolliert."""
        try:
            self._verbindung().set(key, value, ex=ttl_seconds)
        except Exception as exc:
            _log.warning("cache.schreiben_gescheitert", error=str(exc))

    def close(self) -> None:
        """Gibt die Verbindung frei."""
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["MemoryResponseCache", "RedisResponseCache"]
