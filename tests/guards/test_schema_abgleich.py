"""Guard: Tabellenbeschreibungen und Migration beschreiben dasselbe Schema (§7.4).

Das Schema entsteht in der Migration als SQL, die Abfragen entstehen aus
:mod:`wissensgraph.infrastructure.db.tables`. Zwei Beschreibungen derselben Sache driften
auseinander, sobald jemand nur eine davon ändert — eine hinzugefügte Spalte in der Migration
bliebe unbenutzt, eine entfernte ließe jede Abfrage auf dieser Tabelle scheitern, aber erst zur
Laufzeit und erst beim ersten Zugriff.

Dieser Test schließt die Lücke: Er migriert eine echte Datenbank und vergleicht sie Spalte für
Spalte mit den Beschreibungen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry, upgrade_all
from wissensgraph.infrastructure.db.tables import (
    NICHT_ABGEBILDETE_SPALTEN,
    change_log,
    cluster_assignment_candidates,
    cluster_centroids,
    concept_embeddings,
    concepts,
    edges,
    loose_concepts,
    model_calls,
    runs,
    source_cursors,
)

pytestmark = [pytest.mark.guard, pytest.mark.integration]

BESCHRIEBENE_TABELLEN = (
    concepts,
    edges,
    change_log,
    runs,
    source_cursors,
    model_calls,
    concept_embeddings,
    cluster_centroids,
    cluster_assignment_candidates,
    loose_concepts,
)


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


@pytest.mark.parametrize("store", ["shared", "personal"])
def test_jede_beschriebene_spalte_gibt_es_wirklich(migrated: StoreRegistry, store: str) -> None:
    inspector = inspect(migrated.engine(store))

    for table in BESCHRIEBENE_TABELLEN:
        tatsaechlich = {column["name"] for column in inspector.get_columns(table.name)}
        beschrieben = {column.name for column in table.columns}
        fehlend = beschrieben - tatsaechlich

        assert not fehlend, (
            f"In '{table.name}' beschreibt tables.py die Spalten {sorted(fehlend)}, die die "
            f"Migration nicht anlegt. Jede Abfrage darauf scheitert erst zur Laufzeit."
        )


@pytest.mark.parametrize("store", ["shared", "personal"])
def test_keine_spalte_bleibt_unbeschrieben(migrated: StoreRegistry, store: str) -> None:
    inspector = inspect(migrated.engine(store))

    for table in BESCHRIEBENE_TABELLEN:
        tatsaechlich = {column["name"] for column in inspector.get_columns(table.name)}
        beschrieben = {column.name for column in table.columns}
        uebrig = tatsaechlich - beschrieben - NICHT_ABGEBILDETE_SPALTEN

        assert not uebrig, (
            f"Die Migration legt in '{table.name}' die Spalten {sorted(uebrig)} an, die in "
            f"tables.py fehlen. Entweder ergänzen oder in NICHT_ABGEBILDETE_SPALTEN aufnehmen — "
            f"mit einer Begründung, warum sie nicht gelesen werden."
        )


@pytest.mark.parametrize("store", ["shared", "personal"])
def test_nullbarkeit_stimmt_ueberein(migrated: StoreRegistry, store: str) -> None:
    """Eine falsch als optional beschriebene Pflichtspalte fällt sonst erst beim INSERT auf."""
    inspector = inspect(migrated.engine(store))

    for table in BESCHRIEBENE_TABELLEN:
        tatsaechlich = {
            column["name"]: column["nullable"] for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            assert tatsaechlich[column.name] == column.nullable, (
                f"'{table.name}.{column.name}': Die Datenbank sagt "
                f"nullable={tatsaechlich[column.name]}, tables.py sagt {column.nullable}."
            )
