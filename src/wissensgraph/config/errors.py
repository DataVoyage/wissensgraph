"""Fehlerklassen der Konfigurationsschicht.

Jeder Fehler hier ist ein *Startfehler*: Er tritt beim Auflösen und Validieren der Konfiguration
auf und verhindert, dass ein Prozess mit unklarer Konfiguration weiterläuft (§6.5). Ein leerer
String oder ein stillschweigender Default an dieser Stelle wäre die schlechtere Variante — er
würde den Fehler in den Betrieb verschleppen.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Basisklasse aller Konfigurationsfehler."""


class PlaceholderResolutionError(ConfigError):
    """Ein ``${WG_...}``-Platzhalter in einer Config-Datei ist nicht auflösbar (§6.1 Regel 3)."""

    def __init__(self, placeholder: str, path: str) -> None:
        self.placeholder = placeholder
        self.path = path
        super().__init__(
            f"Platzhalter '${{{placeholder}}}' unter '{path}' ist nicht auflösbar. "
            f"Setze die Umgebungsvariable '{placeholder}' oder entferne den Platzhalter."
        )


class ConfigFileError(ConfigError):
    """Eine Config-Datei fehlt, ist unlesbar oder enthält kein YAML-Mapping."""


class ConfigValidationError(ConfigError):
    """Die aufgelöste Konfiguration verletzt eine Regel aus §6.5."""
