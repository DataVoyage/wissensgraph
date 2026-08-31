"""Hilfsmittel für die Quell-Tests: der Mock-Server im selben Prozess.

Der Kniff ist :class:`starlette.testclient.TestClient`. Er ist ein echter ``httpx.Client``, der
seine Anfragen statt über ein Socket direkt in die ASGI-Anwendung schickt. Der Adapter merkt
davon nichts: Er sieht Statuscodes, Kopfzeilen, Paginierung und Fehlerantworten wie über das
Netz — nur ohne Port, ohne Docker und ohne Wartezeit.

Damit läuft die Contract-Suite (§22.3) gegen die *echten* Adapter auf jedem Rechner, auch ohne
laufenden Stack. Der Integrationstest gegen den wirklich gestarteten Container prüft danach das,
was hier grundsätzlich nicht geprüft werden kann: dass Netz, Port und Bind-Mount stimmen.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient

from wissensgraph.config.sources import SourceConfig
from wissensgraph.mocks import create_mock_app

#: Wurzel des Repositories — von dieser Datei aus drei Ebenen nach oben.
REPO = Path(__file__).resolve().parents[2]

#: Die Seed-Daten aus §9.2.
FIXTURES = REPO / "fixtures"

#: Basis-URLs im Mock. Der Host ist beliebig: Der TestClient wertet nur den Pfad aus.
CONFLUENCE_BASE = "http://mock-sources/confluence"
JIRA_BASE = "http://mock-sources/jira"
CONTROL_BASE = "http://mock-sources"

#: Der Gateway-Zugang zu denselben Confluence-Endpunkten: ohne ``/rest/api`` und mit
#: Pflicht-Kopfzeile. Eine Quelle darauf braucht ``api_prefix: ""`` und ``extra_headers``.
CONFLUENCE_GATEWAY_BASE = "http://mock-sources/gateway/confluence"


def mock_app() -> Any:
    """Eine frische Mock-Anwendung auf den Seed-Daten des Repositories."""
    return create_mock_app(FIXTURES)


def client_factory(app: Any) -> Callable[[SourceConfig], httpx.Client]:
    """Eine Client-Fabrik, die den Adapter mit der ASGI-Anwendung verbindet.

    Sie setzt dieselben Kopfzeilen wie die echte Fabrik in
    :mod:`wissensgraph.infrastructure.adapters.base`. Täte sie es nicht, prüfte kein Test je,
    ob ``extra_headers`` wirklich ankommen — und genau daran scheitert eine Anbindung hinter
    einem Gateway, das ohne seinen Schlüssel mit 401 antwortet.
    """

    def bauen(cfg: SourceConfig) -> httpx.Client:
        kopfzeilen = {**cfg.connection.extra_headers, "Accept": "application/json"}
        if cfg.connection.token:
            kopfzeilen["Authorization"] = f"Bearer {cfg.connection.token}"
        return TestClient(app, base_url=cfg.connection.base_url or CONTROL_BASE, headers=kopfzeilen)

    return bauen


def control(app: Any) -> TestClient:
    """Ein Client auf die Steuerungs-API (§9.3)."""
    return TestClient(app, base_url=CONTROL_BASE)


def quelle(
    name: str,
    *,
    adapter: str,
    id_prefix: str,
    base_url: str | None = None,
    scope: str = "engineering",
    default_type: str = "Confluence Page",
    **rest: Any,
) -> SourceConfig:
    """Eine Quellkonfiguration mit sinnvollen Vorgaben für Tests.

    ``rate_limit_per_second`` ist standardmäßig abgeschaltet: Eine Drosselung von fünf Anfragen
    je Sekunde würde einen Lauf über 120 Seiten in echte Wartezeit übersetzen, und was hier
    geprüft wird, ist nicht die Uhr.
    """
    verbindung: dict[str, Any] = {
        "base_url": base_url,
        "rate_limit_per_second": 0,
        "retries": 2,
        "page_size": 25,
    }
    verbindung.update(rest.pop("connection", {}))
    return SourceConfig.model_validate(
        {
            "name": name,
            "adapter": adapter,
            "id_prefix": id_prefix,
            "target": {"scope": scope, "default_type": default_type},
            "connection": verbindung,
            **rest,
        }
    )
