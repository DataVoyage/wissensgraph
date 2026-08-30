"""Gemeinsamer Start eines Prozesses: Konfiguration laden, Logging einrichten (§6, §21.1).

``api``, ``worker``, ``mcp`` und die CLI laufen im selben Image und unterscheiden sich nur im
Startbefehl (§5.1). Diese Datei ist der Teil, den alle vier gemeinsam haben — damit ein
Konfigurationsfehler in jedem der vier Prozesse zur selben Meldung führt.
"""

from __future__ import annotations

from pathlib import Path

from wissensgraph.config.loader import build_settings
from wissensgraph.config.schema import Settings
from wissensgraph.observability.logging import configure_logging


def bootstrap(
    *,
    service: str,
    config_file: Path | None = None,
    dotenv_file: Path | None = None,
) -> Settings:
    """Lädt die Konfiguration und richtet das Logging ein.

    Reihenfolge mit Bedacht: Die Konfiguration wird *vor* dem Logging geladen, weil sie das
    Log-Level und das Ausgabeformat bestimmt. Ein Konfigurationsfehler erscheint deshalb noch
    nicht im strukturierten Log, sondern als Ausnahme — das ist gewollt, denn ein Prozess mit
    unklarer Konfiguration soll laut scheitern und nicht in ein Logformat hinein, das er selbst
    nicht sauber bestimmen konnte.

    Args:
        service: Name des Prozesses, landet als Pflichtfeld in jedem Logeintrag.
        config_file: Abweichender Pfad der Kern-Config-Datei; sonst aus ``WG_CONFIG_DIR``.
        dotenv_file: Abweichender Pfad der ``.env``-Datei; sonst ``.env`` im Arbeitsverzeichnis.

    Returns:
        Die validierte Konfiguration.
    """
    settings = build_settings(config_file=config_file, dotenv_file=dotenv_file)
    configure_logging(
        level=settings.logging.level,
        log_format=settings.logging.format,
        service=service,
    )
    return settings
