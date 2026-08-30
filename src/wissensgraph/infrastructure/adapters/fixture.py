"""Fixture-Adapter: eine Quelle ohne Netzwerk (§9.1).

§9.1 ordnet ihn ausdrücklich ein: "Zusätzlich existieren reine Fixture-Adapter für schnelle
Unit-Tests ohne Netzwerk. Sie sind kein Ersatz für den Mock-Server, sondern eine Ebene darunter."

Der Unterschied ist wichtig genug, ihn festzuhalten: Der Mock-Server prüft den Adapter, dieser
Adapter prüft alles *über* dem Adapter. Wer mit ihm eine Sync-Regel testet, weiß hinterher etwas
über die Regel — und nichts über Paginierung oder Rate-Limits, die es hier gar nicht gibt.

Die Dokumente kommen entweder aus einem Verzeichnis oder direkt aus der Konfiguration. Der zweite
Weg macht einen Test zu drei Zeilen YAML und ist der Grund, warum dieser Adapter auch die
Contract-Suite (§22.3) besteht: Was der Kontrakt verlangt, hängt nicht am Transportweg.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.config.sources import SourceConfig
from wissensgraph.infrastructure.adapters.base import BaseAdapter
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    HealthState,
    HealthStatus,
    SourceDocument,
    SourceError,
)

#: Schlüssel der Auswahl in ``sources.yaml``.
SELECTION_DIRECTORY = "directory"
SELECTION_DOCUMENTS = "documents"
SELECTION_DELETED = "deleted"


class FixtureAdapter(BaseAdapter):
    """Liefert vorgegebene Dokumente ohne jede Verbindung nach außen."""

    name = defaults.ADAPTER_FIXTURE
    capabilities = AdapterCapabilities(
        incremental=True, deletions=True, single_fetch=True, references=True
    )

    def __init__(self) -> None:
        super().__init__()
        self._documents: tuple[SourceDocument, ...] = ()
        self._deleted: tuple[str, ...] = ()

    def configure(self, cfg: SourceConfig) -> None:
        """Lädt die Dokumente sofort — sie sind der ganze Zustand dieses Adapters."""
        super().configure(cfg)
        self._documents = tuple(self._laden(cfg))
        self._deleted = tuple(str(item) for item in cfg.selection.get(SELECTION_DELETED, ()))

    def _laden(self, cfg: SourceConfig) -> Iterator[SourceDocument]:
        """Liest die Dokumente aus der Konfiguration oder aus einem Verzeichnis."""
        for roh in cfg.selection.get(SELECTION_DOCUMENTS, ()):
            yield self._als_dokument(roh)

        verzeichnis = cfg.selection.get(SELECTION_DIRECTORY)
        if not verzeichnis:
            return
        pfad = Path(verzeichnis)
        if not pfad.is_dir():
            raise SourceError(
                f"Quelle '{cfg.name}': Das Fixture-Verzeichnis '{pfad}' gibt es nicht."
            )
        for datei in sorted(pfad.glob("*.json")):
            inhalt = json.loads(datei.read_text(encoding="utf-8"))
            for roh in inhalt if isinstance(inhalt, list) else [inhalt]:
                yield self._als_dokument(roh)

    def _als_dokument(self, roh: Any) -> SourceDocument:
        """Baut ein DTO aus einem Rohobjekt; ein Mapping darf die Felder umlenken (§8.4).

        Schlüssel, die kein DTO-Feld sind, landen in ``extra``. Das ist kein Nachlassen der
        Strenge, sondern genau der Zweck des Feldes: Eine Fixture darf die Form ihrer echten
        Quelle behalten (``$.body.storage.value``) und über die ``mapping:``-Sektion auf die
        DTO-Felder gelenkt werden — sonst könnte dieser Adapter nur DTOs abspielen und wäre als
        Ersatz für eine echte Quelle wertlos.
        """
        if not isinstance(roh, dict):
            raise SourceError(
                f"Quelle '{self.config.name}': Ein Fixture-Eintrag ist "
                f"{type(roh).__name__} statt eines Objekts."
            )
        felder = set(SourceDocument.model_fields)
        werte: dict[str, Any] = {name: wert for name, wert in roh.items() if name in felder}
        werte["extra"] = {
            **{name: wert for name, wert in roh.items() if name not in felder},
            **werte.get("extra", {}),
        }
        werte.update(self.mapping.apply(roh))
        return SourceDocument.model_validate(werte)

    def health(self) -> HealthStatus:
        """Immer erreichbar — die Frage ist nur, ob überhaupt Dokumente da sind."""
        if not self._documents:
            return HealthStatus(
                state=HealthState.DEGRADED,
                detail="Konfiguriert, aber ohne Dokumente. Prüfen: selection.directory.",
            )
        return HealthStatus(
            state=HealthState.HEALTHY, detail=f"{len(self._documents)} Dokumente geladen."
        )

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle Dokumente; mit Cursor nur die seither geänderten."""
        since = self.cursor_since(cursor)
        return self._durchreichen(iter(self._documents), since)

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die als gelöscht markierten externen IDs (``selection.deleted``)."""
        return iter(self._deleted)

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Ein einzelnes Dokument."""
        for document in self._documents:
            if document.external_id == external_id:
                return document
        return None
