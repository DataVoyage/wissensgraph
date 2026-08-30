"""Adapter für Confluence (§8, §9.1).

Er spricht in der Entwicklung mit dem Mock-Server und im Betrieb mit der echten Instanz — der
Unterschied ist ``connection.base_url`` und ein gültiges Token (§9.4). Genau das ist der Sinn der
Übung: Paginierung, Fehlerbehandlung und Rate-Limit-Logik laufen in der Entwicklung wirklich, und
nicht erst dann, wenn niemand mehr zusieht.

Der Adapter kennt die Struktur der Confluence-Antwort und setzt daraus seine Vorgaben. Was in der
``mapping:``-Sektion einer Quelle steht, schlägt sie (§8.4) — eine Instanz mit abweichender
Konfiguration, etwa einer eigenen Ausgabe für ``description``, braucht dafür keinen neuen Adapter.
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

#: Pfade der benutzten Endpunkte. Sie sind die Schnittstelle zur Quelle und stehen deshalb
#: beieinander — wer die API-Version wechselt, ändert diese vier Zeilen.
PATH_SPACES = "/rest/api/space"
PATH_CONTENT = "/rest/api/content"
PATH_DELETED = "/rest/api/content/deleted"

#: Schlüssel der Auswahl in ``sources.yaml`` (§8.4: ``selection.spaces``).
SELECTION_SPACES = "spaces"
SELECTION_EXCLUDE_LABELS = "exclude_labels"


class ConfluenceAdapter(HttpSourceAdapter):
    """Liest Seiten aus Confluence."""

    name = defaults.ADAPTER_CONFLUENCE
    capabilities = AdapterCapabilities(
        incremental=True, deletions=True, single_fetch=True, references=True
    )

    def health_path(self) -> str:
        """Die Space-Liste ist der billigste Aufruf, der Erreichbarkeit *und* Auth prüft."""
        return PATH_SPACES

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle ausgewählten Seiten, seitenweise geholt (§8.2 Regel 1).

        Ein Generator über einen Generator: Die innere Funktion holt Seite für Seite, die äußere
        (:meth:`~BaseAdapter._durchreichen`) filtert nach dem Cursor und schreibt die neue Marke
        erst fort, wenn wirklich alles gelesen wurde.
        """
        since = self.cursor_since(cursor)
        return self._durchreichen(self._seiten(since), since)

    def _seiten(self, since: datetime | None) -> Iterator[SourceDocument]:
        """Blättert durch ``/rest/api/content`` und wandelt jede Seite in ein Dokument."""
        auswahl = self.config.selection
        limit = self.config.connection.page_size
        start = 0
        while True:
            parameter: dict[str, Any] = {"start": start, "limit": limit}
            if auswahl.get(SELECTION_SPACES):
                parameter["spaceKey"] = list(auswahl[SELECTION_SPACES])
            if since is not None:
                parameter["since"] = since.isoformat()

            antwort = self.get(PATH_CONTENT, parameter)
            treffer = list(antwort.get("results", []))
            if not treffer:
                return
            for seite in treffer:
                if not _ausgeschlossen(seite, auswahl.get(SELECTION_EXCLUDE_LABELS, ())):
                    yield self._als_dokument(seite)
            start += len(treffer)
            if start >= int(antwort.get("totalSize", start)):
                return

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die IDs der gelöschten Seiten (``capabilities.deletions``)."""
        antwort = self.get(PATH_DELETED)
        for eintrag in antwort.get("results", []):
            yield str(eintrag["id"])

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Eine einzelne Seite (``capabilities.single_fetch``)."""
        try:
            seite = self.get(f"{PATH_CONTENT}/{external_id}")
        except SourceObjectNotFound:
            return None
        return self._als_dokument(seite)

    def _als_dokument(self, seite: dict[str, Any]) -> SourceDocument:
        """Übersetzt eine Confluence-Seite in das quellneutrale DTO (§8.2)."""
        werte: dict[str, Any] = {
            "external_id": str(seite["id"]),
            "title": seite.get("title"),
            "description": seite.get("excerpt"),
            "body": seite.get("body", {}).get("storage", {}).get("value"),
            "resource": seite.get("links", {}).get("webui"),
            "tags": _labels(seite),
            "updated_at": seite.get("version", {}).get("when"),
            "references": [str(ziel) for ziel in seite.get("links", {}).get("internal", [])],
            "extra": {"space": seite.get("space", {}).get("key")},
        }
        werte.update(self.mapping.apply(seite))
        return SourceDocument.model_validate(werte)


def _labels(seite: dict[str, Any]) -> list[str]:
    """Die Label-Namen einer Seite; ``$.metadata.labels[*].name`` in der Schreibweise aus §8.4."""
    return [
        str(label["name"])
        for label in seite.get("metadata", {}).get("labels", [])
        if isinstance(label, dict) and "name" in label
    ]


def _ausgeschlossen(seite: dict[str, Any], ausgeschlossene_labels: Any) -> bool:
    """Ob eine Seite wegen eines Labels übergangen wird (§8.4: ``selection.exclude_labels``)."""
    verboten = {str(label) for label in ausgeschlossene_labels or ()}
    return bool(verboten & set(_labels(seite)))
