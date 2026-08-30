"""Ausschlussvermerk in der Kandidatentabelle des Clusterings.

Revision ID: 0002_cluster_exclusions
Revises: 0001_initial_schema
Erstellt: Stufe 8 des Stufenplans (§24)

§13.4 verlangt für ein von Hand entferntes Mitglied: "wird nicht erneut zugeordnet; Ausschluss in
``cluster_assignment_candidates`` vermerkt." Der Vermerk braucht einen Ort, und der Ort war im
Ausgangsschema nicht vorgesehen.

Warum ausgerechnet diese Tabelle und keine eigene: Eine gelöschte ``member``-Kante hinterlässt
nichts. Ohne Vermerk fände der nächste Clustering-Lauf dieselbe Nähe wieder und schriebe dieselbe
Zuordnung — die Handarbeit wäre nach einem Lauf verschwunden, und das ist genau der Fall, den
Leitprinzip 15 ausschließt. Die Kandidatentabelle ist der einzige Ort, an dem eine *nicht*
bestehende Mitgliedschaft überhaupt festgehalten werden kann.

Die Spalte kommt mit ``DEFAULT FALSE``: Jede bestehende Zeile ist ein gewöhnlicher Kandidat, und
kein Ausschluss entsteht rückwirkend.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_cluster_exclusions"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fügt den Ausschlussvermerk hinzu."""
    op.execute(
        """
        ALTER TABLE cluster_assignment_candidates
            ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    # Der Teilindex deckt genau die Abfrage ab, die jeder Clustering-Lauf einmal je Scope stellt:
    # "welche Zuordnungen sind gesperrt?". Ohne ihn wäre es ein Full Scan über eine Tabelle, die
    # überwiegend aus gewöhnlichen Kandidaten besteht.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_candidates_excluded
            ON cluster_assignment_candidates (concept_id)
            WHERE excluded
        """
    )


def downgrade() -> None:
    """Nimmt Spalte und Index zurück."""
    op.execute("DROP INDEX IF EXISTS ix_candidates_excluded")
    op.execute("ALTER TABLE cluster_assignment_candidates DROP COLUMN IF EXISTS excluded")
