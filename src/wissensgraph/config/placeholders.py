"""Auflösung von ``${VAR}``-Platzhaltern in Config-Dateien (§6.1 Regel 3).

Config-Dateien im Repository enthalten keine Secrets. Stattdessen stehen dort Platzhalter, die
zur Startzeit aus der Prozessumgebung gefüllt werden::

    stores:
      shared:
        dsn: ${WG_DB_SHARED_DSN}

Ein nicht auflösbarer Platzhalter ist ein Startfehler und **kein leerer String** — sonst startet
ein Container mit einer halb gefüllten Konfiguration und scheitert erst später an unklarer Stelle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from wissensgraph.config.errors import PlaceholderResolutionError

#: ``${NAME}`` oder ``${NAME:-fallback}``. Der Fallback erlaubt es, abgeleitete Defaults wie
#: ``${WG_MODELS_FILE:-${WG_CONFIG_DIR}/models.yaml}`` in der Config auszudrücken (§6.4).
_PLACEHOLDER = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")

#: Obergrenze für die Auflösungstiefe, damit sich zyklische Verweise nicht als Endlosschleife
#: äußern, sondern als klarer Fehler.
_MAX_PASSES = 10


def resolve_placeholders(value: Any, env: Mapping[str, str], *, path: str = "$") -> Any:
    """Ersetzt Platzhalter rekursiv in Mappings, Sequenzen und Strings.

    Args:
        value: Der zu verarbeitende Wert — typischerweise das geparste YAML-Dokument.
        env: Die Quelle der Ersetzungen, üblicherweise ``os.environ``.
        path: Punktpfad des aktuellen Werts, wird nur für Fehlermeldungen mitgeführt.

    Returns:
        Denselben Aufbau mit ersetzten Platzhaltern.

    Raises:
        PlaceholderResolutionError: Wenn ein Platzhalter weder in ``env`` steht noch einen
            Fallback mitbringt.
    """
    if isinstance(value, Mapping):
        return {
            key: resolve_placeholders(item, env, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_placeholders(item, env, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _resolve_string(value, env, path)
    return value


def _resolve_string(value: str, env: Mapping[str, str], path: str) -> str:
    """Löst alle Platzhalter eines einzelnen Strings auf, auch verschachtelte."""
    current = value
    for _ in range(_MAX_PASSES):
        if "${" not in current:
            return current
        current = _PLACEHOLDER.sub(lambda match: _substitute(match, env, path), current)
    raise PlaceholderResolutionError(current, path)


def _substitute(match: re.Match[str], env: Mapping[str, str], path: str) -> str:
    name = match["name"]
    resolved = env.get(name)
    if resolved is not None and resolved != "":
        return resolved
    default = match["default"]
    if default is not None:
        return default
    raise PlaceholderResolutionError(name, path)


def find_placeholders(value: Any) -> set[str]:
    """Sammelt die Namen aller noch offenen Platzhalter — nützlich für Diagnosen (``wg doctor``)."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            found |= find_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            found |= find_placeholders(item)
    elif isinstance(value, str):
        found |= {match["name"] for match in _PLACEHOLDER.finditer(value)}
    return found
