"""Referenzen im Fließtext: ``[[id]]`` (§7.1, §8.5).

§7.1 legt fest, dass ein ``body`` Verweise auf andere Konzepte als ``[[id]]`` trägt. Aus jedem
solchen Verweis entsteht eine Kante der Art ``references``.

Der Leitsatz aus §8.5 gilt hier wörtlich: **kaputte Referenzen sind kein Fehler.** Ein
``[[nicht wirklich eine id]]`` in einem Text ist keine Referenz, sondern Text — es wird
übergangen und bricht keinen Lauf ab. Ein Verweis auf ein Konzept, das es (noch) nicht gibt, wird
dagegen sehr wohl zur Kante, nur mit ``resolved = false``. Der Unterschied ist wichtig: Das eine
ist ein Tippfehler im Text, das andere eine Reihenfolge im Sync.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from wissensgraph.config import defaults
from wissensgraph.domain.base import DomainModel
from wissensgraph.domain.ids import is_valid_concept_id

_REFERENCE = re.compile(defaults.REFERENCE_PATTERN)


class SourceReference(DomainModel):
    """Ein vom Adapter gemeldeter Verweis — Ziel und die Art der Kante, die daraus wird (§8.5).

    Der Fließtext kennt nur eine Art von Verweis: ``[[id]]`` heißt ``references``, mehr lässt sich
    in einem Satz nicht ausdrücken. Ein Quellsystem weiß mehr. Ein Jira-Vorgang, der zu einem Epic
    gehört, sagt damit ``member``; einer, der einen anderen blockiert, sagt ``depends_on``. Diese
    Unterscheidung wieder aus dem Text zu erraten, wäre eine Modellaufgabe — dabei steht sie in
    der Quelle als Tatsache.

    Der Unterschied trägt bis in die Auswertung: §7.7 trennt strukturelle von semantischen Kanten,
    weil sie die Traversierung verschieden steuern und weil ein Knoten mit nur strukturellen
    Kanten ein anderer Fall ist als einer mit semantischen.
    """

    target: str = Field(
        min_length=1,
        description="Externe ID im Quellsystem oder bereits eine vollständige Konzept-ID (§8.5).",
    )
    kind: str = Field(
        default=defaults.EDGE_KIND_REFERENCES,
        min_length=1,
        description="Kantenart aus 'edge_kinds' (§7.7). Ohne Angabe 'references'.",
    )


def as_reference(value: Any) -> Any:
    """Hebt einen blanken String auf eine :class:`SourceReference`.

    Die meisten Verweise sind gewöhnliche Referenzen, und ein Adapter, der nur solche kennt, soll
    weiterhin eine Liste von IDs liefern dürfen. Ohne diese Hebung müsste jeder Aufrufer die
    Kantenart mitschreiben, die für ihn ohnehin immer dieselbe ist.
    """
    if isinstance(value, str):
        return {"target": value}
    return value


def normalize_references(value: Any) -> Any:
    """Wendet :func:`as_reference` auf jedes Element einer Folge an."""
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return value
    return [as_reference(item) for item in value]


def extract_references(body: str | None) -> tuple[str, ...]:
    """Liest alle gültigen ``[[id]]``-Referenzen aus einem Text.

    Mehrfach genannte IDs erscheinen einmal, in der Reihenfolge ihres ersten Vorkommens: Aus zwei
    Erwähnungen desselben Konzepts wird eine Kante, nicht zwei — der eindeutige Index
    ``ux_edges_triple`` (§7.4) ließe die zweite ohnehin nicht zu.

    Args:
        body: Freitext eines Konzepts; ``None`` und leerer Text sind zulässig.

    Returns:
        Die referenzierten Konzept-IDs ohne Dubletten.
    """
    if not body:
        return ()

    gefunden: list[str] = []
    for match in _REFERENCE.finditer(body):
        candidate = match.group(1).strip()
        if is_valid_concept_id(candidate) and candidate not in gefunden:
            gefunden.append(candidate)
    return tuple(gefunden)
