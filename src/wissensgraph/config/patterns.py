"""Regex-Muster für den textbasierten Abgleich (§6.3, §15.2a).

§15.2a beschreibt den billigsten und zugleich verlässlichsten Schritt der gesamten Vernetzung:
"Kommt ein Treffer wörtlich im Text eines anderen Konzepts vor, wird direkt eine Kante
geschrieben … Die Übereinstimmung ist der Beleg; ein Modell wäre hier reine Verschwendung."

Die Muster stehen in Dateien und nicht im Code, weil sie zum *Unternehmen* gehören und nicht zum
System: Wie ein Jira-Key, eine Dokumentnummer oder ein Systemname aussieht, weiß niemand außerhalb
der jeweiligen Organisation. Eine neue Konvention ist damit eine neue Zeile in einer YAML-Datei.

Ein fehlerhaftes Muster bricht den Start ab und nicht den Lauf. Ein nicht übersetzbarer regulärer
Ausdruck ist ein Konfigurationsfehler (§6.5) — er soll auffallen, bevor er mitten in einer
nächtlichen Vernetzung auffällt.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.loader import load_yaml_mapping
from wissensgraph.config.schema import FrozenModel, Settings

#: Unterverzeichnis der Musterdateien im Config-Verzeichnis (§6.3).
PATTERNS_DIR = "patterns"


class PatternConfig(FrozenModel):
    """Ein benanntes Muster, das Bezeichner in Fließtext findet (§15.2a)."""

    name: str = Field(min_length=1)
    regex: str = Field(min_length=1)
    description: str | None = None
    case_sensitive: bool = Field(
        default=True,
        description=(
            "Ob Groß- und Kleinschreibung zählt. Für Bezeichner wie 'PROJ-1234' ist 'true' "
            "richtig: 'proj-1234' im Fließtext ist meist kein Verweis, sondern ein Tippfehler — "
            "und eine Kante auf Verdacht ist schlechter als keine."
        ),
    )

    @field_validator("regex")
    @classmethod
    def _check_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"'{value}' ist kein gültiger regulärer Ausdruck: {exc}") from exc
        return value

    def compiled(self) -> re.Pattern[str]:
        """Das übersetzte Muster."""
        return re.compile(self.regex, 0 if self.case_sensitive else re.IGNORECASE)


class PatternFile(FrozenModel):
    """Der Inhalt einer Musterdatei."""

    patterns: tuple[PatternConfig, ...] = ()


def pattern_files(settings: Settings, *, configured: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """Welche Musterdateien gelesen werden.

    Sind in ``orphans.pattern_files`` Pfade angegeben, gelten genau diese. Sonst jede
    ``*.yaml`` im Verzeichnis ``patterns`` neben der Kernkonfiguration — so wirkt eine neu
    abgelegte Datei ohne Konfigurationsänderung.
    """
    if configured:
        return tuple(Path(eintrag) for eintrag in configured)
    verzeichnis = Path(settings.config_dir) / PATTERNS_DIR
    if not verzeichnis.is_dir():
        return ()
    return tuple(sorted(verzeichnis.glob("*.yaml")))


def load_patterns(
    settings: Settings, *, paths: tuple[Path, ...] | None = None
) -> tuple[PatternConfig, ...]:
    """Lädt alle Muster.

    Args:
        settings: Die geprüfte Kernkonfiguration; liefert Verzeichnis und ``orphans.pattern_files``.
        paths: Abweichende Dateien; sonst die aus :func:`pattern_files`.

    Returns:
        Alle Muster in stabiler Reihenfolge; keine Datei ergibt ein leeres Ergebnis.

    Raises:
        ConfigValidationError: Bei einem ungültigen Muster oder doppeltem Namen.
    """
    dateien = (
        pattern_files(settings, configured=settings.orphans.pattern_files)
        if paths is None
        else paths
    )
    gefunden: list[PatternConfig] = []
    namen: set[str] = set()

    for datei in dateien:
        if not datei.is_file():
            continue
        try:
            inhalt = PatternFile.model_validate(load_yaml_mapping(datei))
        except ValidationError as exc:
            raise ConfigValidationError(
                f"Musterdatei '{datei}' ist ungültig: "
                + "; ".join(
                    f"{'.'.join(str(teil) for teil in fehler['loc'])}: {fehler['msg']}"
                    for fehler in exc.errors()
                )
            ) from exc
        for muster in inhalt.patterns:
            if muster.name in namen:
                raise ConfigValidationError(
                    f"Das Muster '{muster.name}' kommt in mehreren Dateien vor. Namen müssen "
                    f"eindeutig sein — sie erscheinen im Lauf-Bericht und in der Provenienz."
                )
            namen.add(muster.name)
            gefunden.append(muster)
    return tuple(gefunden)


__all__ = [
    "PATTERNS_DIR",
    "PatternConfig",
    "PatternFile",
    "load_patterns",
    "pattern_files",
]
