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


# ---------------------------------------------------------------------------
# Proxy (§5.2, §20.3)
# ---------------------------------------------------------------------------
#
# In vielen Unternehmensnetzen erreichen Container das Internet nur über einen Proxy, und der
# wird als ``HTTP_PROXY``/``HTTPS_PROXY`` in die Umgebung gesetzt. Jede Bibliothek, die ihre
# Umgebung liest — httpx tut das —, schickt dann **auch** die Aufrufe an den Nachbarcontainer
# dorthin. Der Proxy kennt ``mock-sources`` nicht, kann den Namen nicht auflösen und antwortet mit
# einem Fehler, der wie ein Ausfall des Nachbarn aussieht.
#
# Die Abhilfe ist ``NO_PROXY``. Sie ist nur deshalb heikel, weil sie *vollständig* sein muss: Ein
# vergessener Name fällt erst auf, wenn genau dieser Dienst angesprochen wird.

#: Groß- und Kleinschreibung: Beide Varianten sind im Umlauf, und welche eine Bibliothek liest,
#: ist nicht einheitlich. Wer nur eine setzt, hat es in der Hälfte der Fälle richtig gemacht.
PROXY_ENV_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)
NO_PROXY_ENV_VARS: tuple[str, ...] = ("NO_PROXY", "no_proxy")


def proxy_configured(env: dict[str, str]) -> str | None:
    """Der erste gesetzte Proxy aus der Umgebung, sonst ``None``."""
    for name in PROXY_ENV_VARS:
        wert = env.get(name, "").strip()
        if wert:
            return wert
    return None


def no_proxy_entries(env: dict[str, str]) -> tuple[str, ...]:
    """Die Einträge aus ``NO_PROXY``/``no_proxy``, zusammengeführt und normalisiert."""
    roh: list[str] = []
    for name in NO_PROXY_ENV_VARS:
        roh.extend(env.get(name, "").split(","))
    return tuple(sorted({eintrag.strip().lower() for eintrag in roh if eintrag.strip()}))


def bypasses_proxy(host: str | None, entries: tuple[str, ...]) -> bool:
    """Sagt, ob ein Host am Proxy vorbeigeht.

    Die Regel bildet nach, was die verbreiteten Bibliotheken tun: ``*`` hebt den Proxy ganz auf,
    ein Eintrag trifft den Host genau oder als Suffix hinter einem Punkt. Ein führender Punkt am
    Eintrag ist dabei gleichbedeutend — ``.firma.de`` und ``firma.de`` treffen beide
    ``api.firma.de``.

    Bewusst ohne Portangaben und CIDR-Bereiche: Beides ist nicht einheitlich unterstützt, und
    eine Prüfung, die mehr verspricht als die Bibliotheken einlösen, wäre irreführend.
    """
    if not host:
        return True
    if "*" in entries:
        return True
    gesucht = host.lower()
    for eintrag in entries:
        kandidat = eintrag.lstrip(".")
        if gesucht == kandidat or gesucht.endswith(f".{kandidat}"):
            return True
    return False
