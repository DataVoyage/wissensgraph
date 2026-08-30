"""Adapter für Jira (§8, §9.1).

Derselbe Aufbau wie beim Confluence-Adapter, und das ist die eigentliche Aussage: Zwei Quellen
mit unterschiedlicher Paginierung (``start``/``limit`` gegen ``startAt``/``maxResults``),
unterschiedlicher Verschachtelung und unterschiedlichem Referenzbegriff enden beim selben DTO.
Der Kern sieht zwischen beiden keinen Unterschied.

Die externe ID ist der Vorgangsschlüssel (``TEAM-42``) und nicht die numerische ID. §22.3
verlangt Stabilität über Läufe hinweg, und der Schlüssel ist das, was Menschen in Texten
verlinken — womit er auch der Wert ist, auf den eine Referenz zeigt.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.infrastructure.adapters.base import HttpSourceAdapter
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    SourceDocument,
    SourceObjectNotFound,
)

PATH_BOARDS = "/rest/agile/1.0/board"
PATH_SEARCH = "/rest/api/3/search"
PATH_DELETED = "/rest/api/3/deleted"
PATH_ISSUE = "/rest/api/3/issue"

#: Schlüssel der Auswahl in ``sources.yaml`` (§8.4: ``selection.jql_filter``).
SELECTION_JQL = "jql_filter"


class JiraAdapter(HttpSourceAdapter):
    """Liest Vorgänge aus Jira."""

    name = defaults.ADAPTER_JIRA
    capabilities = AdapterCapabilities(
        incremental=True, deletions=True, single_fetch=True, references=True
    )

    def health_path(self) -> str:
        """Die Board-Liste — der billigste Aufruf, der Erreichbarkeit und Auth zugleich prüft."""
        return PATH_BOARDS

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle ausgewählten Vorgänge, seitenweise geholt (§8.2 Regel 1)."""
        since = self.cursor_since(cursor)
        return self._durchreichen(self._vorgaenge(since), since)

    def _vorgaenge(self, since: datetime | None) -> Iterator[SourceDocument]:
        """Blättert durch ``/rest/api/3/search``."""
        limit = self.config.connection.page_size
        start = 0
        while True:
            parameter: dict[str, Any] = {"startAt": start, "maxResults": limit}
            if self.config.selection.get(SELECTION_JQL):
                parameter["jql"] = self.config.selection[SELECTION_JQL]
            if since is not None:
                parameter["since"] = since.isoformat()

            antwort = self.get(PATH_SEARCH, parameter)
            treffer = list(antwort.get("issues", []))
            if not treffer:
                return
            for vorgang in treffer:
                yield self._als_dokument(vorgang)
            start += len(treffer)
            if start >= int(antwort.get("total", start)):
                return

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die Schlüssel der gelöschten Vorgänge (``capabilities.deletions``)."""
        antwort = self.get(PATH_DELETED)
        for key in antwort.get("keys", []):
            yield str(key)

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Einen einzelnen Vorgang (``capabilities.single_fetch``)."""
        try:
            vorgang = self.get(f"{PATH_ISSUE}/{external_id}")
        except SourceObjectNotFound:
            return None
        return self._als_dokument(vorgang)

    def _als_dokument(self, vorgang: dict[str, Any]) -> SourceDocument:
        """Übersetzt einen Jira-Vorgang in das quellneutrale DTO (§8.2)."""
        felder = vorgang.get("fields", {})
        werte: dict[str, Any] = {
            "external_id": str(vorgang["key"]),
            "title": felder.get("summary"),
            "description": None,
            "body": felder.get("description"),
            "resource": vorgang.get("self"),
            "tags": [str(label) for label in felder.get("labels", [])],
            "updated_at": felder.get("updated"),
            "references": [str(ziel) for ziel in vorgang.get("references", [])],
            "extra": {"status": felder.get("status", {}).get("name")},
        }
        werte.update(self.mapping.apply(vorgang))
        return SourceDocument.model_validate(werte)
