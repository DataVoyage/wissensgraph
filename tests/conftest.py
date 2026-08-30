"""Gemeinsame Test-Fixtures.

Grundsatz: Kein Test liest die echte Prozessumgebung oder die echten Config-Dateien des
Repositories. Jede Konfiguration wird explizit übergeben, damit Tests unabhängig davon laufen,
was auf dem ausführenden Rechner in ``.env`` steht — und damit sie plattformunabhängig sind.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry


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
            {"name": "Jira Issue", "stores": ["shared"], "source_mirrored": True},
            {"name": "Cluster", "stores": ["shared", "personal"], "source_mirrored": False},
            {"name": "Note", "stores": ["personal"], "source_mirrored": False},
            # Der Typ des Brücken-Konzepts (§24, Stufe 5). Er liegt ausschließlich in 'personal' —
            # eine Brücke wird immer von der privaten Seite aus geschlagen (§12.1).
            {"name": "Project", "stores": ["personal"], "source_mirrored": False},
        ],
        "edge_kinds": {
            "structural": ["member", "related"],
            "semantic": ["depends_on", "references"],
        },
        "api": {"auth_mode": "token", "token": "test-token"},
        "embedding_dim": 768,
    }


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    """Die geprüfte Konfiguration zur minimalen Testkonfiguration."""
    return Settings.model_validate(minimal_config_dict)


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


# ---------------------------------------------------------------------------
# PostgreSQL für Integrations- und Guard-Tests (§22.1)
# ---------------------------------------------------------------------------

#: Verbindung zu einer PostgreSQL-Instanz mit pgvector. Der Default entspricht dem, was
#: ``docker compose --profile test up -d`` auf dem Host veröffentlicht.
POSTGRES_DSN_ENV = "WG_TEST_POSTGRES_DSN"
DEFAULT_POSTGRES_DSN = "postgresql+psycopg://wg:wg@localhost:5433/wg_shared"

#: Vektordimension der Testdatenbanken. Bewusst klein und ungewöhnlich: Ein Wert wie 768 würde
#: auch dann im Schema stehen, wenn er in Wahrheit fest im Code stünde statt aus der
#: Konfiguration zu kommen.
TEST_EMBEDDING_DIM = 16


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """DSN einer erreichbaren PostgreSQL-Instanz; überspringt den Test, wenn keine läuft.

    Ein Überspringen statt eines Fehlschlags ist Absicht: Die Unit-Tests sollen auf jedem Rechner
    ohne Docker durchlaufen. Die Abnahme der Stufe 1 verlangt dagegen einen echten Lauf — dafür
    wird der Stack mit dem Profil ``test`` gestartet.
    """
    dsn = os.environ.get(POSTGRES_DSN_ENV, DEFAULT_POSTGRES_DSN)
    engine = create_engine(dsn, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"Keine PostgreSQL-Instanz unter {make_url(dsn).render_as_string()} erreichbar "
            f"({type(exc).__name__}). Fuer diese Tests: "
            f"docker compose --profile test up -d — oder {POSTGRES_DSN_ENV} setzen."
        )
    finally:
        engine.dispose()
    return dsn


@pytest.fixture
def store_dsns(postgres_dsn: str) -> Iterator[dict[str, str]]:
    """Zwei frisch angelegte, leere Datenbanken — eine je Store.

    Beide liegen auf derselben Instanz. Für das Deployment wäre das falsch (§5.2 trennt sie in
    zwei Container), für diesen Test ist es richtig: Was die Migration unterscheidet, ist der
    *Name* des Stores, nicht sein Host. Und ``db-personal`` ist vom Host aus bewusst gar nicht
    erreichbar.
    """
    suffix = uuid4().hex[:8]
    databases = {"shared": f"wg_test_shared_{suffix}", "personal": f"wg_test_personal_{suffix}"}
    admin = create_engine(postgres_dsn, isolation_level="AUTOCOMMIT")

    try:
        with admin.connect() as connection:
            for name in databases.values():
                connection.execute(text(f'CREATE DATABASE "{name}"'))

        url = make_url(postgres_dsn)
        yield {
            store: url.set(database=name).render_as_string(hide_password=False)
            for store, name in databases.items()
        }
    finally:
        with admin.connect() as connection:
            for name in databases.values():
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def postgres_settings(minimal_config_dict: dict[str, Any], store_dsns: dict[str, str]) -> Settings:
    """Konfiguration, die auf die frisch angelegten Testdatenbanken zeigt."""
    return Settings.model_validate(
        {
            **minimal_config_dict,
            "embedding_dim": TEST_EMBEDDING_DIM,
            "stores": {store: {"dsn": dsn} for store, dsn in store_dsns.items()},
        }
    )


@pytest.fixture
def postgres_registry(postgres_settings: Settings) -> Iterator[StoreRegistry]:
    """Store-Registry auf den Testdatenbanken — der einzige Weg zu einer Verbindung (§20.1)."""
    with StoreRegistry(postgres_settings) as registry:
        yield registry
