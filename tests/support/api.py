"""Eine vollständige HTTP-API ohne Datenbank, ohne Netz und ohne einen Token.

Die API wird gegen dieselbe :class:`~wissensgraph.runtime.Runtime` gefahren wie im Betrieb — nur
mit speicherresidenten Repositories und dem Fake-Provider dahinter. Das ist der Punkt: Was hier
geprüft wird, ist der echte Weg von HTTP über den Router bis in die Kuration, und nicht eine
nachgebaute Abkürzung.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from support.memory import MemoryUnitOfWorkFactory
from support.semantik import DIM, antwort_skript
from wissensgraph.api.app import create_app
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.queue.memory import MemoryJobQueue
from wissensgraph.runtime import Runtime
from wissensgraph.testing.models import FakeClients

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def api_settings(basis: dict[str, Any]) -> Settings:
    """Konfiguration für die API-Tests: erreichbare Stores, Token-Auth, kleiner Vektorraum.

    ``clustering`` wird ergänzt und nicht überschrieben: Ein Test, der ``stability_runs`` setzt,
    soll das nicht stillschweigend wieder verlieren — der Lauf schriebe dann keine Mitgliedschaft,
    und der Test schlüge an einer Stelle fehl, die mit seiner Frage nichts zu tun hat.
    """
    return Settings.model_validate(
        {
            **basis,
            "stores": {
                "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
                "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
            },
            "api": {"auth_mode": "token", "token": TOKEN},
            "embedding_dim": DIM,
            "clustering": {"neighbors_k": 4, **basis.get("clustering", {})},
        }
    )


def models_datei(verzeichnis: Path) -> Path:
    """Schreibt eine ``models.yaml`` mit dem Fake-Anbieter und gibt ihren Pfad zurück.

    Als echte Datei und nicht als eingesetztes Objekt: Der Weg, den die Laufzeit im Betrieb geht —
    Datei lesen, Schema prüfen, gegen die Settings abgleichen (§11.7) — ist derselbe, den ein Test
    gehen soll. Ein untergeschobenes Konfigurationsobjekt überspränge genau die Prüfungen, die
    beim Start Fehler finden sollen.
    """
    ziel = verzeichnis / "models.yaml"
    ziel.write_text(
        yaml.safe_dump(_MODELS_YAML, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return ziel


_MODELS_YAML: dict[str, Any] = {
    "providers": {"p": {"type": "google_genai", "api_key": "test"}},
    "tasks": {
        defaults.TASK_EMBEDDING: {
            "primary": {"provider": "p", "model": "m", "dim": DIM, "batch_size": 8}
        },
        defaults.TASK_CLUSTER_LABELING: {
            "primary": {"provider": "p", "model": "m", "temperature": 0.2, "json_mode": True}
        },
        defaults.TASK_RELATION_EXTRACTION: {
            "primary": {"provider": "p", "model": "m", "temperature": 0.0, "json_mode": True}
        },
        defaults.TASK_CLUSTER_MATCHING: {
            "primary": {"provider": "p", "model": "m", "temperature": 0.0, "json_mode": True}
        },
        defaults.TASK_SUMMARIZATION: {
            "primary": {"provider": "p", "model": "m", "temperature": 0.3}
        },
    },
    "policies": {
        "shared": {"allowed_providers": ["p"]},
        "personal": {"allowed_providers": ["p"]},
    },
}


@contextmanager
def api(
    settings: Settings, verzeichnis: Path, *, chat: Any = antwort_skript
) -> Iterator[tuple[TestClient, Runtime]]:
    """Ein Testclient samt der Laufzeit dahinter.

    Die Laufzeit kommt mit zurück, weil ein Test den Bestand vorbereiten muss, ohne dafür über
    HTTP zu gehen: Ein Prüfaufbau, der sich seine Ausgangslage selbst über die API erzeugt, prüft
    am Ende nur, ob die API mit sich selbst übereinstimmt.
    """
    runtime = Runtime(
        settings,
        models_file=models_datei(verzeichnis),
        queue=MemoryJobQueue(),
        clients=FakeClients(dim=DIM, chat=chat),
        unit_of_work=MemoryUnitOfWorkFactory(tuple(settings.stores)),
    )
    with TestClient(create_app(settings, runtime=runtime)) as client:
        yield client, runtime


def state(runtime: Runtime, store: str = "shared") -> Any:
    """Der Inhalt eines Stores hinter der API — für Zusicherungen im Test."""
    fabrik = runtime._uow
    assert isinstance(fabrik, MemoryUnitOfWorkFactory)
    return fabrik.state(store)


def befuellen(runtime: Runtime, konzepte: list[Any], *, store: str = "shared") -> None:
    """Legt Konzepte direkt in einen Store — ohne Umweg über die API."""
    with runtime._uow(store) as uow:
        for concept in konzepte:
            uow.concepts.save(concept)


__all__ = ["AUTH", "TOKEN", "api", "api_settings", "befuellen", "models_datei", "state"]
