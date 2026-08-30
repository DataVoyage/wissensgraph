"""Abhängigkeiten der HTTP-API: Konfiguration, Store-Registry, Authentifizierung (§16.1, §20.3).

Konfiguration und Registry hängen am ``app.state`` und nicht an einem Modul-Singleton. Das ist
Absicht: Tests bauen sich eine eigene Anwendung mit eigener Konfiguration, ohne globalen Zustand
zurücksetzen zu müssen.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry


def get_settings(request: Request) -> Settings:
    """Die aufgelöste Konfiguration dieses Prozesses."""
    settings: Settings = request.app.state.settings
    return settings


def get_registry(request: Request) -> StoreRegistry:
    """Die Store-Registry — der einzige Weg zu einer Datenbankverbindung (§20.1)."""
    registry: StoreRegistry = request.app.state.registry
    return registry


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[StoreRegistry, Depends(get_registry)]


def require_auth(request: Request, settings: SettingsDep) -> str:
    """Prüft die Berechtigung und liefert den ``actor`` für das Änderungsjournal (§16.1).

    Jede schreibende Operation trägt diesen Wert ins ``change_log``. Im POC ist er bei
    ``auth_mode=token`` eine feste Kennung; mit ``oidc`` käme er später aus dem Token (§20.3).

    Raises:
        HTTPException: 401, wenn der Bearer-Token fehlt oder nicht passt.
    """
    if settings.api.auth_mode == "none":
        return "user:local"

    if settings.api.auth_mode == "oidc":  # pragma: no cover — Ausbaustufe (§20.3)
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="auth_mode='oidc' ist eine Ausbaustufe und noch nicht umgesetzt.",
        )

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer-Token fehlt.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expected = settings.api.token or ""
    # Konstantzeitvergleich: Ein längenabhängiger Vergleich verrät über die Antwortzeit, wie viele
    # Zeichen eines geratenen Tokens stimmen.
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer-Token ist ungültig.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "user:token"


ActorDep = Annotated[str, Depends(require_auth)]
