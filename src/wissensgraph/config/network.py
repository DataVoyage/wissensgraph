"""Feststellen, ob ein Datenbank-Host lokal ist (§6.5, §20.1).

Der ``personal``-Store darf den lokalen Rechner nicht verlassen (Leitprinzip 2). Steht in der
Konfiguration ``allow_remote: false``, muss der DSN also auf etwas Lokales zeigen. "Lokal" heißt
hier dreierlei, und alle drei Fälle sind im Betrieb echt:

1. Loopback (``localhost``, ``127.0.0.1``, ``::1``) — direkter Start auf dem Host.
2. Eine private Adresse nach RFC 1918 / RFC 4193 — Docker-Bridge-Netze vergeben solche.
3. Ein bekannter Compose-Servicename (``db-personal``) — im Container ist das der Normalfall,
   und ein DNS-Lookup ist zur Startzeit weder verlässlich noch wünschenswert.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from wissensgraph.config.defaults import API_LOOPBACK_HOSTS

#: Compose-Servicenamen, die per Konvention lokal sind. Sie stehen hier und nicht im Code der
#: Validierung, damit ein abweichendes Deployment sie an einer Stelle erweitern kann.
KNOWN_LOCAL_SERVICE_NAMES: tuple[str, ...] = ("db-personal", "db-shared", "localhost")


def extract_host(dsn: str) -> str | None:
    """Zieht den Hostnamen aus einem DSN. Gibt ``None`` zurück, wenn keiner enthalten ist."""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return None
    try:
        host = parts.hostname
    except ValueError:
        # Ungültige Adressliterale (z. B. kaputte IPv6-Klammern) lassen ``hostname`` werfen.
        return None
    return host


def is_local_host(host: str | None) -> bool:
    """Sagt, ob ein Hostname als lokal gilt."""
    if not host:
        # Kein Host im DSN heißt Unix-Socket oder lokale Datei — das ist lokal.
        return True
    lowered = host.lower()
    if lowered in API_LOOPBACK_HOSTS or lowered in KNOWN_LOCAL_SERVICE_NAMES:
        return True
    if lowered.endswith(".local") or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def is_local_dsn(dsn: str) -> bool:
    """Sagt, ob ein DSN auf einen lokalen Host zeigt."""
    return is_local_host(extract_host(dsn))
