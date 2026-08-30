"""Einheitliche Fehlerausgabe als RFC-7807-Problem-Detail (§16.1).

Alle Fehler der API tragen dieselbe Form — ``type``, ``title``, ``status``, ``detail``,
``instance``. Das erspart der UI und dem Agenten eine Fallunterscheidung danach, welcher Teil des
Servers den Fehler erzeugt hat.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Medientyp nach RFC 7807.
PROBLEM_MEDIA_TYPE = "application/problem+json"

#: Basis der ``type``-URIs. Ein eigener Namensraum statt ``about:blank``, damit sich Fehlerarten
#: später dokumentieren lassen, ohne die Form zu ändern.
PROBLEM_TYPE_BASE = "https://wissensgraph.local/problems"


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str | None = None,
    instance: str | None = None,
    problem_type: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Baut eine Problem-Detail-Antwort.

    ``headers`` werden durchgereicht, weil einige Statuscodes ohne ihren Header unvollständig
    sind: Eine ``401`` ohne ``WWW-Authenticate`` verletzt RFC 9110 und lässt einen Client im
    Unklaren darüber, wie er sich ausweisen soll.
    """
    payload: dict[str, object] = {
        "type": problem_type or f"{PROBLEM_TYPE_BASE}/{_slug(title)}",
        "title": title,
        "status": status_code,
    }
    if detail is not None:
        payload["detail"] = detail
    if instance is not None:
        payload["instance"] = instance
    return JSONResponse(
        status_code=status_code,
        content=payload,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def _slug(title: str) -> str:
    """Macht aus einem Titel einen URI-tauglichen Bezeichner."""
    return "-".join(title.lower().split())


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Übersetzt ``HTTPException`` in ein Problem-Detail."""
    assert isinstance(exc, StarletteHTTPException)
    return problem_response(
        status_code=exc.status_code,
        title=_title_for(exc.status_code),
        detail=str(exc.detail),
        instance=str(request.url.path),
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Übersetzt Schema-Verstöße in ein Problem-Detail."""
    assert isinstance(exc, RequestValidationError)
    fehler = "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
    )
    return problem_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Ungültige Anfrage",
        detail=fehler,
        instance=str(request.url.path),
    )


_TITLES = {
    status.HTTP_400_BAD_REQUEST: "Ungültige Anfrage",
    status.HTTP_401_UNAUTHORIZED: "Nicht authentifiziert",
    status.HTTP_403_FORBIDDEN: "Nicht berechtigt",
    status.HTTP_404_NOT_FOUND: "Nicht gefunden",
    status.HTTP_409_CONFLICT: "Konflikt",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "Ungültige Anfrage",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "Interner Fehler",
    status.HTTP_503_SERVICE_UNAVAILABLE: "Nicht bereit",
}


def _title_for(status_code: int) -> str:
    return _TITLES.get(status_code, "Fehler")


def register_error_handlers(app: FastAPI) -> None:
    """Hängt die Problem-Detail-Handler in die Anwendung."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
