"""Tests der Migrationssteuerung (§5.5, §7.3, §7.4).

Diese Datei kommt ohne Datenbank aus. Zwei Dinge machen das möglich:

* Der Advisory-Lock ist an den PostgreSQL-Dialekt gebunden und wird für alles andere sauber
  übersprungen — geprüft wird hier genau diese Fallunterscheidung.
* Alembics Offline-Modus (``wg migrate --sql``) rendert die Migration als Text, ohne sich zu
  verbinden. Damit lässt sich die inhaltlich wichtigste Eigenschaft der Migration ohne
  Infrastruktur prüfen: dass shared- und personal-Store unterschiedliche Invarianten bekommen
  und die Vektordimension aus der Konfiguration stammt.

Dass die Migration tatsächlich *durchläuft*, prüfen die Integrationstests gegen echtes
PostgreSQL — das kann eine Attrappe nicht beantworten.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.migrations import (
    MigrationResult,
    advisory_lock,
    advisory_lock_key,
    alembic_config,
    build_options,
    head_revision,
    render_sql,
    status,
)
from wissensgraph.migrations.context import (
    ATTRIBUTE_KEY,
    MigrationError,
    MigrationOptions,
    current_options,
)


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    """Eine gültige Konfiguration mit einer auffälligen Vektordimension.

    Bewusst nicht 768: Eine Dimension, die zufällig dem üblichen Default entspricht, würde auch
    dann in der gerenderten Migration auftauchen, wenn sie in Wahrheit fest im Code stünde.
    """
    return Settings.model_validate({**minimal_config_dict, "embedding_dim": 16})


@pytest.fixture
def registry(settings: Settings) -> StoreRegistry:
    return StoreRegistry(settings)


class TestMigrationOptions:
    def test_versionstabelle_traegt_den_storenamen(self) -> None:
        """§7.3 verlangt getrennte Alembic-Versionstabellen je Store."""
        shared = MigrationOptions(store="shared", dsn="postgresql://x/y", embedding_dim=16)
        personal = MigrationOptions(store="personal", dsn="postgresql://x/y", embedding_dim=16)

        assert shared.version_table != personal.version_table
        assert shared.version_table.endswith("shared")
        assert personal.version_table.endswith("personal")

    def test_erkennt_den_geteilten_store(self) -> None:
        shared = MigrationOptions(store="shared", dsn="postgresql://x/y", embedding_dim=16)
        personal = MigrationOptions(store="personal", dsn="postgresql://x/y", embedding_dim=16)

        assert shared.is_shared
        assert not personal.is_shared

    @pytest.mark.parametrize("dim", [0, -1, defaults.EMBEDDING_DIM_MAX + 1])
    def test_lehnt_unbrauchbare_dimension_ab(self, dim: int) -> None:
        """Eine Dimension jenseits der pgvector-Grenze soll früh scheitern, nicht am HNSW-Index."""
        with pytest.raises(MigrationError, match="embedding_dim"):
            MigrationOptions(store="shared", dsn="postgresql://x/y", embedding_dim=dim)


class TestCurrentOptions:
    def test_ohne_laufenden_alembic_kontext_klarer_fehler(self) -> None:
        """Ein direktes ``alembic upgrade`` kennt weder Store noch Dimension (§6.2)."""
        with pytest.raises(MigrationError, match="wg migrate"):
            current_options()


class TestAlembicKonfiguration:
    def test_findet_die_skripte_im_paket(self, settings: Settings, registry: StoreRegistry) -> None:
        """Die Skripte liegen im Paket, damit sie im Container-Image mitkommen."""
        options = build_options(settings, "shared", registry)

        config = alembic_config(options)

        location = config.get_main_option("script_location")
        assert location is not None
        assert (options.version_table, config.attributes[ATTRIBUTE_KEY]) == (
            "alembic_version_shared",
            options,
        )
        assert location.endswith("migrations")

    def test_kennt_genau_eine_zielrevision(self) -> None:
        """Es gibt einen einzigen Kopf — keine Verzweigung im Migrationsverlauf.

        Die Revisionsnummer steht hier bewusst *nicht*: Sie wächst mit jeder Stufe, und ein Test,
        der sie festschreibt, müsste bei jeder Schemaänderung mitgeändert werden, ohne dabei etwas
        zu prüfen. Was tatsächlich schiefgehen kann, ist eine zweite Wurzel oder ein zweiter Kopf
        — dann wüsste ``alembic upgrade head`` nicht mehr, wohin.
        """
        kopf = head_revision()

        assert kopf is not None
        assert kopf.startswith("00")

    def test_uebernimmt_dsn_und_dimension_aus_der_konfiguration(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        """Die Werte kommen aus der geprüften Konfiguration, nicht aus der Umgebung (§6.2)."""
        options = build_options(settings, "personal", registry)

        assert options.embedding_dim == 16
        assert options.dsn == settings.stores["personal"].dsn


class TestAdvisoryLock:
    def test_schluessel_ist_ueber_prozesse_stabil(self) -> None:
        """Ein je Prozess wechselnder Schlüssel würde den Lock wertlos machen."""
        assert advisory_lock_key() == advisory_lock_key()
        assert isinstance(advisory_lock_key(), int)

    def test_wird_ausserhalb_von_postgresql_uebersprungen(self) -> None:
        """SQLite kennt keine Advisory-Locks; der Lauf soll deshalb nicht scheitern."""
        engine = create_engine("sqlite+pysqlite:///:memory:")

        with engine.connect() as connection, advisory_lock(connection) as held:
            assert held is False

    def test_bricht_nach_ablauf_der_frist_ab(self, mocker: Any) -> None:
        """Unbegrenztes Warten sähe aus wie ein hängender Container."""
        connection = mocker.MagicMock()
        connection.dialect.name = "postgresql"
        connection.execute.return_value.scalar.return_value = False
        mocker.patch("wissensgraph.infrastructure.db.migrations.time.sleep")

        with (
            pytest.raises(MigrationError, match="Lock"),
            advisory_lock(connection, timeout_seconds=0),
        ):
            pass  # pragma: no cover — der Block wird nie betreten

    def test_gibt_den_lock_auch_bei_einem_fehler_frei(self, mocker: Any) -> None:
        """Ein nicht freigegebener Lock würde jeden weiteren Start blockieren."""
        connection = mocker.MagicMock()
        connection.dialect.name = "postgresql"
        connection.execute.return_value.scalar.return_value = True

        with pytest.raises(ValueError, match="absichtlich"), advisory_lock(connection):
            raise ValueError("absichtlich")

        anweisungen = [str(call.args[0]) for call in connection.execute.call_args_list]
        assert any("pg_advisory_unlock" in anweisung for anweisung in anweisungen)


class TestStatus:
    def test_meldet_eine_leere_datenbank_als_ausstehend(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """Eine Datenbank ohne Versionstabelle steht auf ``None`` — es steht alles aus."""
        settings = Settings.model_validate(
            {
                **minimal_config_dict,
                "stores": {
                    "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": True},
                },
                "scopes": [{"name": "engineering", "store": "shared"}],
                "concept_types": [{"name": "Cluster", "stores": ["shared"]}],
            }
        )

        with StoreRegistry(settings) as registry:
            results = status(settings, registry)

        assert [(item.store, item.revision_before, item.changed) for item in results] == [
            ("shared", None, True)
        ]


class TestMigrationResult:
    def test_gleiche_revision_bedeutet_unveraendert(self) -> None:
        """Die Wiederholbarkeit aus §24 hängt an genau dieser Unterscheidung."""
        result = MigrationResult(store="shared", revision_before="0001", revision_after="0001")

        assert not result.changed
        assert result.as_dict()["changed"] is False


class TestGerendertesSQL:
    """``wg migrate --sql`` — der Trockenlauf aus §19.

    Er berührt keine Datenbank und eignet sich deshalb, um die inhaltlichen Entscheidungen der
    Migration zu prüfen: Dimension aus der Konfiguration, Invariante nur im shared-Store.
    """

    def test_setzt_die_dimension_aus_der_konfiguration_ein(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        sql = render_sql(settings, registry, "shared")

        assert "vector(16)" in sql
        assert "vector(768)" not in sql

    def test_legt_den_hnsw_index_an(self, settings: Settings, registry: StoreRegistry) -> None:
        """§24 verlangt für Stufe 1 ausdrücklich einen HNSW-Index."""
        sql = render_sql(settings, registry, "shared")

        assert "USING hnsw (embedding vector_cosine_ops)" in sql
        assert f"m = {defaults.HNSW_M}" in sql

    def test_invariante_nur_im_geteilten_store(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        """§7.4: Der CHECK gegen personal-Verweise entsteht nur im shared-Store."""
        shared_sql = render_sql(settings, registry, "shared")
        personal_sql = render_sql(settings, registry, "personal")

        assert "ck_shared_no_personal_ref" in shared_sql
        assert "ck_shared_no_personal_ref" not in personal_sql

    def test_bindet_die_store_spalte_an_die_datenbank(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        """Ergänzung über §7.4 hinaus: 'concepts.store' muss zur Datenbank passen."""
        shared_sql = render_sql(settings, registry, "shared")
        personal_sql = render_sql(settings, registry, "personal")

        assert "CHECK (store = 'shared')" in shared_sql
        assert "CHECK (store = 'personal')" in personal_sql

    def test_legt_die_erweiterungen_selbst_an(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        """Die Migration setzt kein Init-Skript des Datenbank-Images voraus (§7.3)."""
        sql = render_sql(settings, registry, "shared")

        for extension in defaults.REQUIRED_EXTENSIONS:
            assert f'CREATE EXTENSION IF NOT EXISTS "{extension}"' in sql

    def test_schreibt_in_die_versionstabelle_des_stores(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        sql = render_sql(settings, registry, "personal")

        assert "alembic_version_personal" in sql
        assert "alembic_version_shared" not in sql

    def test_enthaelt_alle_tabellen_aus_der_ddl(
        self, settings: Settings, registry: StoreRegistry
    ) -> None:
        """Ein Gegencheck zur DDL in §7.4 — keine Tabelle darf beim Abtippen verloren gehen."""
        sql = render_sql(settings, registry, "shared")

        for table in (
            "concepts",
            "edges",
            "concept_embeddings",
            "cluster_centroids",
            "cluster_assignment_candidates",
            "change_log",
            "runs",
            "source_cursors",
            "model_calls",
        ):
            assert f"CREATE TABLE {table} (" in sql
        assert "CREATE VIEW v_loose_concepts" in sql
