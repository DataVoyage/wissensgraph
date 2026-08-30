"""Ein bewusst kleiner JSONPath für die Mapping-Konfiguration (§8.4).

§8.4 schreibt Ausdrücke wie ``$.body.storage.value`` oder ``$.metadata.labels[*].name`` in
``sources.yaml``. Gebraucht wird davon genau so viel, wie eine Quellantwort auf ein Feld eines
:class:`~wissensgraph.ports.sources.SourceDocument` abbildet: absteigen, indizieren, über eine
Liste hinweggehen. Filterausdrücke, Slices, rekursiver Abstieg oder Funktionen kommen darin nicht
vor.

Deshalb dieser Eigenbau statt einer Bibliothek. Der Gewinn ist nicht die vermiedene Abhängigkeit,
sondern die Fehlermeldung: Ein nicht unterstützter Ausdruck bricht beim Laden der Konfiguration
ab und sagt, *welches Zeichen* an welcher Stelle nicht verstanden wurde — statt beim ersten Lauf
still einen leeren Titel zu erzeugen. Was hier fehlt, fehlt sichtbar.

Unterstützt::

    $                      die Wurzel
    .name  ['name']        Feld eines Objekts
    [0]                    Element einer Liste
    [*]                    alle Elemente einer Liste bzw. alle Werte eines Objekts
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Die Wurzel eines Ausdrucks. Ein Pfad ohne sie ist mehrdeutig — relativ wozu?
ROOT = "$"

_STEP = re.compile(
    r"""
    \.(?P<field>[A-Za-z_][A-Za-z0-9_-]*)      # .name
    | \[\s*'(?P<squoted>[^']*)'\s*\]          # ['name']
    | \[\s*"(?P<dquoted>[^"]*)"\s*\]          # ["name"]
    | \[\s*(?P<index>-?\d+)\s*\]              # [0]
    | \[\s*(?P<wildcard>\*)\s*\]              # [*]
    """,
    re.VERBOSE,
)


class JsonPathError(ValueError):
    """Ein Ausdruck ist nicht Teil der unterstützten Teilmenge."""

    def __init__(self, expression: str, position: int, reason: str) -> None:
        self.expression = expression
        self.position = position
        super().__init__(
            f"JSONPath '{expression}' ist an Position {position} nicht verwendbar: {reason} "
            f"Unterstützt sind '$', '.name', \"['name']\", '[0]' und '[*]' (§8.4)."
        )


@dataclass(frozen=True)
class _Field:
    name: str


@dataclass(frozen=True)
class _Index:
    index: int


@dataclass(frozen=True)
class _Wildcard:
    pass


_Step = _Field | _Index | _Wildcard


@dataclass(frozen=True)
class JsonPath:
    """Ein geparster Ausdruck. Unveränderlich und damit gefahrlos wiederverwendbar."""

    expression: str
    steps: tuple[_Step, ...]

    @classmethod
    def parse(cls, expression: str) -> JsonPath:
        """Zerlegt einen Ausdruck in seine Schritte.

        Raises:
            JsonPathError: Bei leerem Ausdruck, fehlender Wurzel oder nicht unterstützter Syntax.
        """
        text = expression.strip()
        if not text:
            raise JsonPathError(expression, 0, "Der Ausdruck ist leer.")
        if not text.startswith(ROOT):
            raise JsonPathError(expression, 0, f"Er beginnt nicht mit '{ROOT}'.")

        steps: list[_Step] = []
        position = len(ROOT)
        while position < len(text):
            match = _STEP.match(text, position)
            if match is None:
                raise JsonPathError(expression, position, f"'{text[position]}' passt zu nichts.")
            steps.append(_step_of(match))
            position = match.end()
        return cls(expression=text, steps=tuple(steps))

    @property
    def is_multi(self) -> bool:
        """Ob der Ausdruck über einen Platzhalter läuft und deshalb mehrere Werte liefern kann."""
        return any(isinstance(step, _Wildcard) for step in self.steps)

    def find(self, data: Any) -> list[Any]:
        """Alle Werte, auf die der Ausdruck zeigt.

        Ein nicht vorhandener Zwischenschritt ist kein Fehler, sondern ein leeres Ergebnis: Eine
        Quelle darf ein optionales Feld weglassen, und §8.4 sieht dafür ausdrücklich den Fall
        "leer → wird per Task 'summarization' erzeugt" vor.
        """
        aktuell: list[Any] = [data]
        for step in self.steps:
            aktuell = [wert for knoten in aktuell for wert in _apply(step, knoten)]
            if not aktuell:
                return []
        return aktuell

    def first(self, data: Any) -> Any | None:
        """Der erste Treffer, oder ``None``."""
        treffer = self.find(data)
        return treffer[0] if treffer else None


def _step_of(match: re.Match[str]) -> _Step:
    """Baut aus einem Treffer den passenden Schritt."""
    if match["wildcard"]:
        return _Wildcard()
    if match["index"] is not None:
        return _Index(int(match["index"]))
    name = match["field"] or match["squoted"] or match["dquoted"]
    return _Field(name if name is not None else "")


def _apply(step: _Step, node: Any) -> list[Any]:
    """Wendet einen Schritt auf einen Knoten an; ein unpassender Knoten liefert nichts."""
    if isinstance(step, _Field):
        if isinstance(node, dict) and step.name in node:
            return [node[step.name]]
        return []
    if isinstance(step, _Index):
        if isinstance(node, list) and -len(node) <= step.index < len(node):
            return [node[step.index]]
        return []
    if isinstance(node, list):
        return list(node)
    if isinstance(node, dict):
        return list(node.values())
    return []
