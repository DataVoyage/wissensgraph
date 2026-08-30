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

from wissensgraph.config import defaults
from wissensgraph.domain.ids import is_valid_concept_id

_REFERENCE = re.compile(defaults.REFERENCE_PATTERN)


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
