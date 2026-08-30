"""Strukturiertes Logging (§21.1).

Zwei Festlegungen aus dem Dokument prägen dieses Modul:

1. **Pflichtfelder.** Jeder Eintrag trägt ``timestamp``, ``level``, ``service``, ``run_id``,
   ``request_id``, ``actor`` und ``store``. Fehlende Werte erscheinen als ``null`` statt zu
   fehlen — eine Auswertung soll sich auf die Feldmenge verlassen können.
2. **Keine personenbezogenen Inhalte.** Konzept-IDs werden geloggt, ``body`` nie. Das ist keine
   Frage der Sorgfalt beim Aufruf, sondern wird durch einen Prozessor erzwungen: verbotene
   Felder werden vor der Ausgabe entfernt.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from wissensgraph.config.defaults import SECRET_MASK
from wissensgraph.config.masking import is_secret_key

#: Felder, die in jedem Eintrag vorhanden sein müssen (§21.1).
REQUIRED_FIELDS: tuple[str, ...] = (
    "timestamp",
    "level",
    "service",
    "run_id",
    "request_id",
    "actor",
    "store",
)

#: Felder, die niemals in einem Logeintrag landen dürfen. ``body`` ist der Inhalt eines Konzepts
#: und damit potenziell personenbezogen; ``description`` und ``title`` sind bewusst nicht dabei,
#: weil ohne sie kein Lauf mehr nachvollziehbar wäre.
FORBIDDEN_FIELDS: frozenset[str] = frozenset({"body", "content", "raw_body", "prompt", "text"})


def drop_forbidden_fields(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Entfernt inhaltstragende Felder aus dem Logeintrag (§21.1).

    Der Prozessor ersetzt sie nicht durch eine Maske, sondern löscht sie: Ein Hinweis darauf,
    dass an dieser Stelle ein Body war, hilft der Diagnose nicht und lädt dazu ein, ihn doch
    wieder mitzuloggen.
    """
    for field in FORBIDDEN_FIELDS:
        event_dict.pop(field, None)
    return event_dict


def mask_secret_fields(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Maskiert Secret-Werte im Logeintrag, unabhängig vom Log-Level (§20.2)."""
    for key in list(event_dict):
        if is_secret_key(key) and event_dict[key] is not None:
            event_dict[key] = SECRET_MASK
    return event_dict


def ensure_required_fields(
    service: str,
) -> Any:
    """Baut einen Prozessor, der die Pflichtfelder ergänzt (§21.1).

    Args:
        service: Name des Prozesses (``api``, ``worker``, ``mcp``, ``cli``). Er unterscheidet in
            einer gemeinsamen Logausgabe, welcher Container gesprochen hat.
    """

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        for field in REQUIRED_FIELDS:
            event_dict.setdefault(field, None)
        return event_dict

    return processor


def configure_logging(*, level: str, log_format: str, service: str) -> None:
    """Richtet ``structlog`` und das Standard-Logging für einen Prozess ein.

    Beide Wege enden im selben Handler. Das ist keine Kosmetik: Fremde Bibliotheken — Alembic,
    SQLAlchemy, uvicorn, später die Provider-Clients — loggen über das ``logging``-Modul der
    Standardbibliothek. Ohne diese Verdrahtung liefen ihre Einträge an den Prozessoren vorbei und
    damit an den Pflichtfeldern aus §21.1, an der Secret-Maskierung aus §20.2 und an der
    Entfernung inhaltstragender Felder. Genau das darf nicht passieren: Was der Prozess über
    seine Bibliotheken schreibt, ist derselben Regel unterworfen wie das, was er selbst schreibt.

    Args:
        level: Log-Level aus der Konfiguration (``WG_LOG_LEVEL``).
        log_format: ``json`` für den Betrieb, ``console`` für die lokale Entwicklung.
        service: Name des Prozesses, landet als Pflichtfeld in jedem Eintrag.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if log_format == "console"
        else structlog.processors.JSONRenderer()
    )

    # Diese Prozessoren laufen über beide Wege: über structlog-Aufrufe und über Einträge, die aus
    # der Standardbibliothek kommen.
    gemeinsam: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        ensure_required_fields(service),
        drop_forbidden_fields,
        mask_secret_fields,
    ]

    structlog.configure(
        processors=[
            *gemeinsam,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Ausgabe nach stdout, nicht nach stderr: Der Log ist die normale Ausgabe eines Dienstes und
    # nicht sein Fehlerkanal. Nur so lässt er sich in einer Pipeline weiterverarbeiten.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=gemeinsam,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)


def get_logger(name: str) -> Any:
    """Gibt einen gebundenen Logger zurück."""
    return structlog.get_logger(name)


def bind_context(**values: Any) -> None:
    """Bindet Werte an den aktuellen Kontext, z. B. ``run_id`` für die Dauer eines Laufs."""
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    """Löscht den gebundenen Kontext — am Ende eines Laufs oder einer Anfrage."""
    structlog.contextvars.clear_contextvars()
