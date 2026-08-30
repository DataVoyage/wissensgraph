"""Lesen einer ``.env``-Datei (§6.2, Präzedenzstufe zwischen YAML und Prozess-ENV).

Bewusst eine eigene, kleine Implementierung statt einer Abhängigkeit: Das Format ist trivial, und
die Präzedenzregel aus §6.2 verlangt, dass die Prozessumgebung eine ``.env``-Datei *überschreibt* —
das Gegenteil dessen, was verbreitete Bibliotheken standardmäßig tun. Diese Richtung explizit im
eigenen Code zu haben, ist verlässlicher als eine fremde Voreinstellung.
"""

from __future__ import annotations

from pathlib import Path

_EXPORT_PREFIX = "export "


def parse_dotenv(content: str) -> dict[str, str]:
    """Parst den Inhalt einer ``.env``-Datei zu einem Mapping.

    Unterstützt ``KEY=value``, führendes ``export``, Kommentarzeilen mit ``#``, Leerzeilen sowie
    einfache und doppelte Anführungszeichen um den Wert. Zeilen ohne ``=`` werden übersprungen.
    """
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_EXPORT_PREFIX):
            line = line[len(_EXPORT_PREFIX) :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    """Entfernt umschließende Anführungszeichen; ohne solche bleibt der Wert unverändert."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(path: Path) -> dict[str, str]:
    """Liest eine ``.env``-Datei. Eine fehlende Datei ist kein Fehler, sondern ein leeres Mapping.

    Das ist Absicht: Im Container kommt die Konfiguration aus der Prozessumgebung, eine
    ``.env``-Datei gibt es dort typischerweise gar nicht.
    """
    if not path.is_file():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))
