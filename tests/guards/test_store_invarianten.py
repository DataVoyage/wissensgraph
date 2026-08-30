"""Guard-Tests der Store-Trennung auf Datenbankebene (§20.1).

§20.1 führt fünf Guard-Tests als Teil der Pflicht-Testsuite. Nummer 4 gehört zu Stufe 1:

    "Ein ``INSERT`` in ``shared.edges`` mit ``to_store = 'personal'`` muss vom CHECK-Constraint
    abgelehnt werden."

Der Unterschied zu einem gewöhnlichen Integrationstest ist die Fragestellung. Hier geht es nicht
darum, ob eine Funktion tut, was sie soll, sondern darum, ob eine Schutzregel auch dann greift,
wenn jemand sie umgehen will — deshalb wird bewusst am Anwendungscode vorbei direkt in die
Datenbank geschrieben.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from wissensgraph.config.schema import Settings
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.introspection import constraint_exists
from wissensgraph.infrastructure.db.migrations import upgrade_all

pytestmark = [pytest.mark.guard, pytest.mark.integration]


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    """Beide Stores auf dem Stand des Schemas aus §7.4."""
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


def _insert_edge(registry: StoreRegistry, store: str, **werte: str) -> None:
    """Schreibt eine Kante direkt in die Datenbank, ohne Anwendungslogik dazwischen."""
    with registry.engine(store).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO edges (from_store, from_id, to_store, to_id, kind) "
                "VALUES (:from_store, :from_id, :to_store, :to_id, :kind)"
            ),
            werte,
        )


class TestSharedVerweistNichtAufPersonal:
    """§20.1, Guard 4."""

    def test_kante_auf_ein_persoenliches_ziel_wird_abgelehnt(self, migrated: StoreRegistry) -> None:
        with pytest.raises(IntegrityError, match="ck_shared_no_personal_ref"):
            _insert_edge(
                migrated,
                "shared",
                from_store="shared",
                from_id="confluence:1",
                to_store="personal",
                to_id="note:1",
                kind="references",
            )

    def test_kante_von_einer_persoenlichen_quelle_wird_abgelehnt(
        self, migrated: StoreRegistry
    ) -> None:
        """Auch die Gegenrichtung: Der geteilte Store kennt den persönlichen gar nicht."""
        with pytest.raises(IntegrityError, match="ck_shared_no_personal_ref"):
            _insert_edge(
                migrated,
                "shared",
                from_store="personal",
                from_id="note:1",
                to_store="shared",
                to_id="confluence:1",
                kind="references",
            )

    def test_kante_innerhalb_des_geteilten_stores_ist_erlaubt(
        self, migrated: StoreRegistry
    ) -> None:
        """Die Gegenprobe — der Constraint darf nicht einfach alles ablehnen."""
        _insert_edge(
            migrated,
            "shared",
            from_store="shared",
            from_id="confluence:1",
            to_store="shared",
            to_id="confluence:2",
            kind="related",
        )

    def test_der_persoenliche_store_darf_die_bruecke_schlagen(
        self, migrated: StoreRegistry
    ) -> None:
        """§7.3: Ein Brücken-Konzept in 'personal' verweist per Kante auf 'shared'.

        Das ist die erlaubte Richtung. Der Constraint gilt deshalb ausdrücklich nur im
        shared-Store — hier wird geprüft, dass er im personal-Store gerade *nicht* existiert.
        """
        _insert_edge(
            migrated,
            "personal",
            from_store="personal",
            from_id="project:finance",
            to_store="shared",
            to_id="confluence:1",
            kind="references",
        )

        with migrated.engine("personal").connect() as connection:
            assert not constraint_exists(connection, "edges", "ck_shared_no_personal_ref")


class TestWeitereInvarianten:
    def test_selbstkante_wird_abgelehnt(self, migrated: StoreRegistry) -> None:
        """``ck_edges_no_self`` aus §7.4."""
        with pytest.raises(IntegrityError, match="ck_edges_no_self"):
            _insert_edge(
                migrated,
                "personal",
                from_store="personal",
                from_id="note:1",
                to_store="personal",
                to_id="note:1",
                kind="related",
            )

    def test_konzept_mit_fremdem_store_wert_wird_abgelehnt(self, migrated: StoreRegistry) -> None:
        """Die Spalte 'concepts.store' muss zur Datenbank passen, in der die Zeile liegt.

        Ergänzung über §7.4 hinaus: Ohne diese Prüfung wäre die als "redundant, aber explizit"
        gedachte Spalte eine stille Fehlerquelle — ein falsch geroutetes Upsert würde eine Zeile
        anlegen, die über sich selbst die Unwahrheit sagt.
        """
        with (
            pytest.raises(IntegrityError, match="ck_concepts_store"),
            migrated.engine("shared").begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO concepts (id, store, scope, type) "
                    "VALUES ('note:1', 'personal', 'personal', 'Note')"
                )
            )
