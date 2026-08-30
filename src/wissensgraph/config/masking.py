"""Maskierung von Secrets für Logs und ``GET /api/v1/config/effective`` (§6.1 Regel 5, §20.2).

Die aufgelöste Konfiguration soll jederzeit einsehbar sein — das ist der einzige verlässliche Weg
festzustellen, womit ein Container tatsächlich läuft. Genau deshalb darf sie keine Klartext-Secrets
enthalten. Maskiert wird **unabhängig vom Log-Level**; es gibt keinen Debug-Modus, der Secrets
sichtbar macht.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from wissensgraph.config.defaults import SECRET_MASK

#: Schlüsselnamen, deren Wert grundsätzlich als Secret gilt. Der Abgleich ist case-insensitiv und
#: prüft auf Teilstrings, damit auch ``api_key``, ``WG_API_TOKEN`` oder ``client_secret`` greifen.
SECRET_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "authorization",
)

#: Schlüssel, deren Wert eine URL mit möglicherweise eingebetteten Zugangsdaten ist. Hier wird
#: nicht der ganze Wert maskiert, sondern nur der Credential-Teil — die Hostangabe bleibt für die
#: Diagnose erhalten.
DSN_KEY_MARKERS: tuple[str, ...] = ("dsn", "url", "uri")

_URL_CREDENTIALS = re.compile(r"^(?P<user>[^:@/]*):(?P<password>[^@/]*)@")


def is_secret_key(key: str) -> bool:
    """Sagt, ob ein Konfigurationsschlüssel als Secret zu behandeln ist."""
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def _is_dsn_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in DSN_KEY_MARKERS)


def mask_dsn(value: str) -> str:
    """Entfernt das Passwort aus einer DSN/URL, behält Schema, Benutzer und Host.

    ``postgresql://wg:geheim@db-shared:5432/wg`` wird zu
    ``postgresql://wg:***@db-shared:5432/wg``. Ohne Zugangsdaten bleibt der Wert unverändert.
    """
    try:
        parts = urlsplit(value)
    except ValueError:  # pragma: no cover — urlsplit ist für Strings sehr tolerant
        return value
    if not parts.netloc or "@" not in parts.netloc:
        return value
    masked_netloc = _URL_CREDENTIALS.sub(rf"\g<user>:{SECRET_MASK}@", parts.netloc)
    return urlunsplit((parts.scheme, masked_netloc, parts.path, parts.query, parts.fragment))


def mask_config(value: Any, *, key: str | None = None) -> Any:
    """Gibt eine maskierte Kopie einer Konfigurationsstruktur zurück.

    Args:
        value: Mapping, Liste oder Skalar aus der aufgelösten Konfiguration.
        key: Der Schlüssel, unter dem ``value`` steht — entscheidet über die Maskierung.

    Returns:
        Dieselbe Struktur, in der Secret-Werte durch :data:`SECRET_MASK` und DSN-Passwörter durch
        eine gekürzte Form ersetzt sind. Das Original bleibt unverändert.
    """
    if isinstance(value, Mapping):
        return {
            item_key: mask_config(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [mask_config(item, key=key) for item in value]
    if key is None:
        return value
    if is_secret_key(key):
        return SECRET_MASK if value is not None else None
    if _is_dsn_key(key) and isinstance(value, str):
        return mask_dsn(value)
    return value
