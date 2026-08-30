"""Integrationstests der Migrationen gegen echtes PostgreSQL (§7.4, §24 Stufe 1).

Diese Datei beantwortet die Abnahmefragen der Stufe 1, die sich ohne Datenbank nicht beantworten
lassen: Läuft die Migration auf einer leeren Datenbank durch? Ist sie wiederholbar? Existiert ein
HNSW-Index? Geprüft wird jeweils das *Ergebnis* in der Datenbank, nicht der Text der Migration —
den prüfen die Unit-Tests.

Ohne laufende PostgreSQL-Instanz überspringen sich die Tests selbst; siehe ``postgres_dsn`` in
``tests/conftest.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import CheckStatus, check_schema
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.introspection import (
    extension_installed,
    index_method,
    table_exists,
    vector_dimension,
)
from wissensgraph.infrastructure.db.migrations import (
    build_options,
    current_revision,
    downgrade_store,
    head_revision,
    status,
    upgrade_all,
    upgrade_store,
)

pytestmark = pytest.mark.integration

#: Alle Tabellen und Sichten aus der DDL in §7.4.
ERWARTETE_RELATIONEN = (
    "concepts",
    "edges",
    "concept_embeddings",
    "cluster_centroids",
    "cluster_assignment_candidates",
    "change_log",
    "runs",
    "source_cursors",
    "model_calls",
    "v_loose_concepts",
)


class TestErsterLauf:
    def test_migration_laeuft_auf_leeren_datenbanken_durch(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Erstes Abnahmekriterium der Stufe 1."""
        results = upgrade_all(postgres_settings, postgres_registry)

        assert {result.store for result in results} == {"shared", "personal"}
        assert all(result.revision_before is None for result in results)
        assert all(result.revision_after == head_revision() for result in results)
        assert all(result.changed for result in results)

    @pytest.mark.parametrize("store", ["shared", "personal"])
    def test_alle_relationen_aus_der_ddl_existieren(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry, store: str
    ) -> None:
        upgrade_store(postgres_settings, postgres_registry, store)

        with postgres_registry.engine(store).connect() as connection:
            fehlend = [name for name in ERWARTETE_RELATIONEN if not table_exists(connection, name)]

        assert fehlend == []

    @pytest.mark.parametrize("store", ["shared", "personal"])
    def test_erweiterungen_werden_von_der_migration_angelegt(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry, store: str
    ) -> None:
        """Die Datenbanken sind frisch angelegt und haben kein Init-Skript gesehen (§7.3)."""
        upgrade_store(postgres_settings, postgres_registry, store)

        with postgres_registry.engine(store).connect() as connection:
            fehlend = [
                name
                for name in defaults.REQUIRED_EXTENSIONS
                if not extension_installed(connection, name)
            ]

        assert fehlend == []

    @pytest.mark.parametrize("store", ["shared", "personal"])
    def test_hnsw_index_existiert(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry, store: str
    ) -> None:
        """Drittes Abnahmekriterium der Stufe 1.

        Geprüft wird die Zugriffsmethode, nicht nur der Name: Ein versehentlich als B-Tree
        angelegter Index gleichen Namens würde eine Existenzprüfung bestehen.
        """
        upgrade_store(postgres_settings, postgres_registry, store)

        with postgres_registry.engine(store).connect() as connection:
            methode = index_method(connection, "ix_emb_hnsw")

        assert methode == "hnsw"

    @pytest.mark.parametrize("store", ["shared", "personal"])
    def test_vektordimension_stammt_aus_der_konfiguration(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry, store: str
    ) -> None:
        """§7.3: Die Dimension aus ``WG_EMBEDDING_DIM`` geht in das Schema ein."""
        upgrade_store(postgres_settings, postgres_registry, store)

        with postgres_registry.engine(store).connect() as connection:
            dimension = vector_dimension(connection, "concept_embeddings", "embedding")

        assert dimension == postgres_settings.embedding_dim

    def test_stores_fuehren_getrennte_versionstabellen(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """§7.3: getrennte Alembic-Versionstabellen."""
        upgrade_all(postgres_settings, postgres_registry)

        with postgres_registry.engine("shared").connect() as connection:
            assert table_exists(connection, "alembic_version_shared")
            assert not table_exists(connection, "alembic_version_personal")


class TestWiederholbarkeit:
    def test_zweiter_lauf_veraendert_nichts(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Zweites Abnahmekriterium der Stufe 1: die Migration ist wiederholbar."""
        upgrade_all(postgres_settings, postgres_registry)

        results = upgrade_all(postgres_settings, postgres_registry)

        assert all(not result.changed for result in results)
        assert all(result.revision_before == head_revision() for result in results)

    def test_downgrade_und_erneutes_upgrade_fuehren_zum_gleichen_schema(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Ein ``downgrade`` ist nur dann etwas wert, wenn danach wieder migriert werden kann."""
        upgrade_store(postgres_settings, postgres_registry, "shared")

        zurueck = downgrade_store(postgres_settings, postgres_registry, "shared", revision="base")
        erneut = upgrade_store(postgres_settings, postgres_registry, "shared")

        assert zurueck.revision_after is None
        assert erneut.revision_after == head_revision()
        with postgres_registry.engine("shared").connect() as connection:
            assert table_exists(connection, "concepts")
            assert index_method(connection, "ix_emb_hnsw") == "hnsw"

    def test_downgrade_raeumt_die_tabellen_ab(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Ein ``downgrade``, das nichts entfernt, wäre schlimmer als keines."""
        upgrade_store(postgres_settings, postgres_registry, "shared")

        downgrade_store(postgres_settings, postgres_registry, "shared", revision="base")

        with postgres_registry.engine("shared").connect() as connection:
            verbliebene = [name for name in ERWARTETE_RELATIONEN if table_exists(connection, name)]
            # Die Erweiterungen bleiben absichtlich stehen: Sie gehören der Datenbank, nicht
            # diesem Schema.
            assert extension_installed(connection, "vector")

        assert verbliebene == []

    def test_status_meldet_vor_dem_lauf_ausstehende_migrationen(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Die Grundlage von ``wg migrate --check``."""
        vorher = status(postgres_settings, postgres_registry)
        upgrade_all(postgres_settings, postgres_registry)
        nachher = status(postgres_settings, postgres_registry)

        assert all(item.changed for item in vorher)
        assert all(not item.changed for item in nachher)


class TestSchemaVerhalten:
    def test_generierte_suchspalte_wird_beim_einfuegen_gefuellt(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """``search_tsv`` ist eine GENERATED-Spalte (§7.4) — sie muss ohne Zutun entstehen."""
        upgrade_store(postgres_settings, postgres_registry, "shared")

        with postgres_registry.engine("shared").begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO concepts (id, store, scope, type, title, description) "
                    "VALUES ('confluence:1', 'shared', 'engineering', 'Confluence Page', "
                    "'Zahlungsabgleich', 'Abgleich der Zahlungen')"
                )
            )
            treffer = connection.execute(
                text(
                    "SELECT count(*) FROM concepts "
                    "WHERE search_tsv @@ to_tsquery('simple', 'zahlungsabgleich')"
                )
            ).scalar()

        assert treffer == 1

    def test_doctor_erkennt_eine_nicht_migrierte_datenbank(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        results = check_schema(postgres_settings, postgres_registry)

        assert all(result.status is CheckStatus.FAIL for result in results)
        assert all("Nicht migriert" in result.detail for result in results)

    def test_doctor_meldet_ein_passendes_schema_als_in_ordnung(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        upgrade_all(postgres_settings, postgres_registry)

        results = check_schema(postgres_settings, postgres_registry)

        assert all(result.status is CheckStatus.OK for result in results)

    def test_doctor_erkennt_eine_nachtraeglich_geaenderte_dimension(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """§11.7: Eine nach der Migration geänderte ``WG_EMBEDDING_DIM`` macht das Schema veraltet.

        Ohne diese Prüfung fiele der Widerspruch erst beim ersten Embedding-Lauf auf — also
        deutlich später und an einer Stelle, an der er schwerer zuzuordnen ist.
        """
        upgrade_all(postgres_settings, postgres_registry)
        geaendert = postgres_settings.model_copy(
            update={"embedding_dim": postgres_settings.embedding_dim + 1}
        )

        results = check_schema(geaendert, postgres_registry)

        assert all(result.status is CheckStatus.FAIL for result in results)
        assert all("vector(" in result.detail for result in results)

    def test_revision_ist_nach_dem_lauf_lesbar(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        upgrade_store(postgres_settings, postgres_registry, "personal")
        options = build_options(postgres_settings, "personal", postgres_registry)

        with postgres_registry.engine("personal").connect() as connection:
            assert current_revision(connection, options) == head_revision()
