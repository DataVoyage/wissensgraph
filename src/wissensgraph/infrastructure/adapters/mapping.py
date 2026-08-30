"""Vom Rohobjekt einer Quelle zum ``SourceDocument`` (§8.4).

Die ``mapping:``-Sektion einer Quelle sagt, wo in der Antwort des Quellsystems Titel, Text und
Tags stehen. Sie ist damit die Stelle, an der eine Quelle ihre Eigenheiten unterbringt, ohne dass
der Kern sie kennt: ``$.body.storage.value`` bei Confluence, ``$.fields.description`` bei Jira.

Ein Adapter kennt die Struktur seiner API natürlich trotzdem und setzt eigene Vorgaben. Die
Konfiguration schlägt sie — aber nur dort, wo sie wirklich etwas findet. Ein Ausdruck, der ins
Leere zeigt, lässt die Vorgabe des Adapters stehen, statt das Feld zu leeren: Ein optionales Feld
darf in einer Quellantwort fehlen (§8.4: "leer → wird per Task 'summarization' erzeugt").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.infrastructure.adapters.jsonpath import JsonPath, JsonPathError

#: Felder, die mehrere Werte tragen. Alles andere ist ein Einzelwert.
_MULTI_FIELDS = frozenset({"tags"})

#: Felder, deren Wert unverändert an Pydantic geht statt in eine Zeichenkette gewandelt zu werden.
#: ``updated_at`` kann als ISO-String oder als Zahl kommen; die Umwandlung gehört ins Modell.
_RAW_FIELDS = frozenset({"updated_at"})


class MappingError(ValueError):
    """Ein Mapping-Ausdruck ist unbrauchbar oder liefert einen Wert der falschen Gestalt."""


class DocumentMapping:
    """Die übersetzten Mapping-Ausdrücke einer Quelle, einmal geparst.

    Das Parsen passiert beim Konfigurieren des Adapters und nicht je Objekt. Der Unterschied ist
    nicht die Geschwindigkeit, sondern der Zeitpunkt des Fehlers: Ein Tippfehler im Ausdruck soll
    beim Start auffallen und nicht bei Seite 4.000 eines Laufs.
    """

    def __init__(self, mapping: Mapping[str, str], *, source: str = "") -> None:
        """
        Args:
            mapping: Feldname des ``SourceDocument`` -> JSONPath-Ausdruck.
            source: Name der Quelle, nur für Fehlermeldungen.

        Raises:
            MappingError: Bei einem unbekannten Feldnamen oder einem nicht parsebaren Ausdruck.
        """
        self._source = source
        self._paths: dict[str, JsonPath] = {}
        for field, expression in mapping.items():
            if field not in defaults.SOURCE_MAPPING_FIELDS:
                raise MappingError(
                    f"{self._wo()}Das Feld '{field}' ist nicht abbildbar. Möglich sind: "
                    f"{', '.join(defaults.SOURCE_MAPPING_FIELDS)}."
                )
            try:
                self._paths[field] = JsonPath.parse(expression)
            except JsonPathError as exc:
                raise MappingError(f"{self._wo()}{exc}") from exc

    def __len__(self) -> int:
        return len(self._paths)

    def __contains__(self, field: str) -> bool:
        return field in self._paths

    def apply(self, raw: Any) -> dict[str, Any]:
        """Die aus einem Rohobjekt gewonnenen Felder — nur die, die wirklich etwas ergeben.

        Raises:
            MappingError: Wenn ein Einzelwert-Feld auf ein Objekt oder eine Liste zeigt. Das ist
                keine Kleinigkeit, die man wegwerfen darf: Ein ``title``, der auf ein Objekt
                zeigt, ist ein falscher Ausdruck, und stillschweigend kein Titel zu setzen würde
                den Fehler bis in die Datenbank durchreichen.
        """
        ergebnis: dict[str, Any] = {}
        for field, path in self._paths.items():
            treffer = path.find(raw)
            if not treffer:
                continue
            ergebnis[field] = (
                self._als_liste(field, treffer)
                if field in _MULTI_FIELDS
                else self._als_einzelwert(field, treffer[0])
            )
        return ergebnis

    def _als_liste(self, field: str, treffer: list[Any]) -> tuple[str, ...]:
        """Flacht das Ergebnis eines Listenfelds zu Zeichenketten ab."""
        werte: list[str] = []
        for wert in treffer:
            for einzeln in wert if isinstance(wert, list) else [wert]:
                if einzeln is None:
                    continue
                if isinstance(einzeln, dict | list):
                    raise MappingError(
                        f"{self._wo()}'{field}' zeigt auf eine verschachtelte Struktur "
                        f"({type(einzeln).__name__}). Der Ausdruck muss bis auf die einzelnen "
                        f"Werte hinabsteigen, etwa '$.metadata.labels[*].name'."
                    )
                werte.append(str(einzeln))
        return tuple(werte)

    def _als_einzelwert(self, field: str, wert: Any) -> Any:
        """Wandelt einen Treffer in eine Zeichenkette, sofern das Feld eine erwartet."""
        if wert is None:
            return None
        if isinstance(wert, dict | list):
            raise MappingError(
                f"{self._wo()}'{field}' zeigt auf {type(wert).__name__}, erwartet wird ein "
                f"einzelner Wert. Der Ausdruck muss tiefer gehen."
            )
        if field in _RAW_FIELDS:
            return wert
        return wert if isinstance(wert, str) else str(wert)

    def _wo(self) -> str:
        """Präfix der Fehlermeldung mit dem Quellnamen, falls bekannt."""
        return f"Quelle '{self._source}': " if self._source else ""
