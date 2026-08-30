"""Ein Adapter, der außerhalb des Kerns lebt — für das vierte Abnahmekriterium der Stufe 3.

§24 verlangt: "ein dritter, im Test angelegter Dummy-Adapter wird allein über einen Config-Eintrag
aktiv, ohne Kernänderung." Diese Datei ist dieser Adapter. Sie steht bewusst unter ``tests/`` und
nicht unter ``src/``: Wäre sie Teil des Pakets, bewiese der Test nur, dass der Kern seine eigenen
Adapter findet.

Aktiv wird er über einen Eintrag in ``sources.yaml``::

    - name: dummy
      adapter: dummy
      class: "support.dummy_adapter:DummyAdapter"
      id_prefix: dummy
      target: { scope: engineering, default_type: Confluence Page }

Kein Entry Point, keine Installation, keine Zeile Kerncode.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from wissensgraph.config.sources import SourceConfig
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    HealthState,
    HealthStatus,
    NotSupported,
    SourceDocument,
)

#: Fester Zeitpunkt statt "jetzt": Ein Adapter, dessen Dokumente sich mit der Uhr ändern, wäre
#: nicht idempotent (§8.2 Regel 4) und die Contract-Suite fände das zu Recht.
BEGINN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: Wie viele Dokumente dieser Adapter erfindet.
ANZAHL = 5


class DummyAdapter:
    """Erfindet ein paar Dokumente. Erbt von nichts — er erfüllt nur das Protokoll.

    Dass hier keine Basisklasse aus dem Kern steht, ist der eigentliche Beweis: §8.2 beschreibt
    ein ``Protocol``, keine Vererbungshierarchie. Wer einen Adapter beisteuert, schuldet Methoden,
    keine Abstammung.
    """

    name = "dummy"
    capabilities = AdapterCapabilities(
        incremental=True, deletions=False, single_fetch=True, references=True
    )

    def __init__(self) -> None:
        self._config: SourceConfig | None = None
        self._cursor = Cursor()

    def configure(self, cfg: SourceConfig) -> None:
        self._config = cfg

    def health(self) -> HealthStatus:
        zustand = HealthState.HEALTHY if self._config is not None else HealthState.UNHEALTHY
        return HealthStatus(state=zustand, detail=f"{ANZAHL} erfundene Dokumente.")

    def _alle(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                external_id=f"d-{nummer}",
                title=f"Erfundenes Dokument {nummer}",
                body=f"Inhalt des erfundenen Dokuments {nummer}.",
                tags=("dummy",),
                updated_at=BEGINN + timedelta(hours=nummer),
                references=(f"d-{nummer + 1}",) if nummer < ANZAHL else (),
            )
            for nummer in range(1, ANZAHL + 1)
        ]

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        seit = _seit(cursor)
        hoch = seit
        for document in self._alle():
            if seit is not None and document.updated_at is not None and document.updated_at <= seit:
                continue
            if document.updated_at is not None and (hoch is None or document.updated_at > hoch):
                hoch = document.updated_at
            yield document
        self._cursor = Cursor(value={"updated_after": hoch.isoformat()} if hoch else {})

    def next_cursor(self) -> Cursor:
        return self._cursor

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        raise NotSupported("Dieser Adapter meldet keine Löschungen (capabilities.deletions).")

    def fetch(self, external_id: str) -> SourceDocument | None:
        for document in self._alle():
            if document.external_id == external_id:
                return document
        return None


def _seit(cursor: Cursor | None) -> datetime | None:
    """Liest den Zeitpunkt aus dem Cursor; ein unbrauchbarer Wert gilt als "kein Cursor"."""
    if cursor is None or cursor.is_empty:
        return None
    roh = cursor.value.get("updated_after")
    if not isinstance(roh, str):
        return None
    try:
        return datetime.fromisoformat(roh)
    except ValueError:
        return None
