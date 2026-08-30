"""Gemeinsame Test-Fixtures.

Grundsatz: Kein Test liest die echte Prozessumgebung oder die echten Config-Dateien des
Repositories. Jede Konfiguration wird explizit übergeben, damit Tests unabhängig davon laufen,
was auf dem ausführenden Rechner in ``.env`` steht — und damit sie plattformunabhängig sind.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def minimal_config_dict() -> dict[str, Any]:
    """Die kleinste Konfiguration, die :class:`Settings` gültig macht.

    Bewusst ohne Platzhalter: Tests, die die Auflösung prüfen, bauen sich ihre Platzhalter selbst.
    """
    return {
        "stores": {
            "shared": {"dsn": "postgresql+psycopg://wg:wg@db-shared:5432/wg_shared"},
            "personal": {
                "dsn": "postgresql+psycopg://wg:wg@db-personal:5432/wg_personal",
                "allow_remote": False,
            },
        },
        "scopes": [
            {"name": "engineering", "store": "shared"},
            {"name": "personal", "store": "personal"},
        ],
        "concept_types": [
            {"name": "Confluence Page", "stores": ["shared"], "source_mirrored": True},
            {"name": "Cluster", "stores": ["shared", "personal"], "source_mirrored": False},
            {"name": "Note", "stores": ["personal"], "source_mirrored": False},
        ],
        "edge_kinds": {
            "structural": ["member", "related"],
            "semantic": ["depends_on", "references"],
        },
        "api": {"auth_mode": "token", "token": "test-token"},
        "embedding_dim": 768,
    }


@pytest.fixture
def write_config(tmp_path: Path) -> Iterator[Any]:
    """Schreibt ein Mapping als YAML in eine temporäre Datei und gibt deren Pfad zurück."""

    def _write(data: dict[str, Any], name: str = "wissensgraph.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    yield _write


@pytest.fixture
def empty_dotenv(tmp_path: Path) -> Path:
    """Pfad auf eine nicht existierende ``.env``-Datei.

    So laufen Tests garantiert ohne die ``.env`` des Entwicklerrechners — ein fehlender Pfad ist
    für den Loader kein Fehler, sondern ein leeres Mapping.
    """
    return tmp_path / "nicht-vorhanden.env"
