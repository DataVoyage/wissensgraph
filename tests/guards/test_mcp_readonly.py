"""Guard-Test 5 aus §20.1: Der Agent kann auf ``shared`` nicht schreiben.

    "Die MCP-Verbindung auf ``shared`` muss bei jedem Schreibversuch einen Datenbankfehler
    erzeugen."

*Datenbankfehler* ist der Punkt, und deshalb steht dieser Test hier und nicht bei den
Werkzeugtests. Eine Prüfung im Anwendungscode wäre nur so gut wie der Codepfad, der sie aufruft;
was §18.3 verlangt, ist eine Eigenschaft der **Verbindung**. Geprüft wird sie deshalb, indem am
Anwendungscode vorbei geschrieben wird — genau so, wie es ein Fehler im Code täte.

Ohne ``readonly_dsn`` benutzt die Verbindung dieselbe Rolle mit erzwungenem
``default_transaction_read_only``. Das ist schwächer als eine eigene Datenbankrolle — wer die
Einstellung kennt, kann sie zurücksetzen —, aber es ist ohne jede Einrichtung vorhanden und fängt
jeden Irrtum ab. Der Test prüft die Ausprägung, die diese Installation tatsächlich benutzt.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.migrations import upgrade_all
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.services.curation import CurationService

pytestmark = [pytest.mark.guard, pytest.mark.integration]


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    """Beide Stores auf dem Stand des Schemas aus §7.4."""
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


def _mcp_fabrik(registry: StoreRegistry) -> UnitOfWorkFactory:
    """Die Arbeitseinheiten-Fabrik, mit der der MCP-Server läuft (§18.3)."""
    return UnitOfWorkFactory(registry, readonly_stores=frozenset({defaults.STORE_SHARED}))


def _konzepte_anlegen(registry: StoreRegistry, *ids: str) -> None:
    """Legt Konzepte über die schreibende Verbindung an — der Aufbau, nicht der Prüfling."""
    with registry.engine(defaults.STORE_SHARED).begin() as connection:
        for concept_id in ids:
            connection.execute(
                text(
                    "INSERT INTO concepts (id, store, scope, type, title, content_hash) "
                    "VALUES (:id, 'shared', 'engineering', 'Confluence Page', :id, 'hash')"
                ),
                {"id": concept_id},
            )


class TestSchreibenAufShared:
    """§20.1, Guard 5."""

    def test_ein_direktes_insert_scheitert_in_der_datenbank(self, migrated: StoreRegistry) -> None:
        with (
            pytest.raises(DatabaseError, match="read-only"),
            migrated.readonly_engine(defaults.STORE_SHARED).begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO edges (from_store, from_id, to_store, to_id, kind) "
                    "VALUES ('shared', 'confluence:1', 'shared', 'confluence:2', 'references')"
                )
            )

    def test_die_kuration_ueber_die_mcp_fabrik_scheitert_ebenso(
        self, postgres_settings: Settings, migrated: StoreRegistry
    ) -> None:
        """Derselbe Schutz auf dem Weg, den ein Werkzeug tatsächlich nimmt.

        Die beiden Konzepte werden vorher über die *schreibende* Verbindung angelegt: Der Test
        soll an der Verbindung scheitern und nicht schon daran, dass der Ausgangspunkt fehlt.
        """
        _konzepte_anlegen(migrated, "confluence:1", "confluence:2")
        kuration = CurationService(postgres_settings, _mcp_fabrik(migrated))

        with pytest.raises(DatabaseError):
            kuration.add_edge(
                store=defaults.STORE_SHARED,
                from_id="confluence:1",
                to_id="confluence:2",
                actor="agent:test",
            )

    def test_lesen_bleibt_moeglich(self, migrated: StoreRegistry) -> None:
        """Der Agent darf überall lesen — nur schreiben nicht (§17.4)."""
        with _mcp_fabrik(migrated)(defaults.STORE_SHARED) as uow:
            assert uow.concepts.get("confluence:gibtsnicht") is None

    def test_der_persoenliche_store_bleibt_beschreibbar(
        self, postgres_settings: Settings, migrated: StoreRegistry
    ) -> None:
        """Sonst wäre der Agent nutzlos: Anlegen im eigenen Bereich ist sein Zweck (§18.1)."""
        from wissensgraph.services.curation import CurationService as Kuration

        kuration = Kuration(postgres_settings, _mcp_fabrik(migrated))

        ergebnis = kuration.create_concept(
            scope="personal", concept_type="Note", title="Vom Agenten", actor="agent:test"
        )

        assert ergebnis.concept is not None
        with _mcp_fabrik(migrated)(defaults.STORE_PERSONAL) as uow:
            assert uow.concepts.get(ergebnis.concept.id) is not None
