"""Negativvermerk für verworfene Kanten.

Revision ID: 0003_edge_rejections
Revises: 0002_cluster_exclusions
Erstellt: Stufe 11 des Stufenplans (§24)

§16.2 verlangt für ``POST /edges/{id}/reject``: "Kante wird entfernt und als Negativ vermerkt,
damit sie nicht neu entsteht." §24 macht daraus ein Abnahmekriterium: "ein Modellvorschlag wird
bestätigt und einer verworfen — der verworfene entsteht im Folgelauf nicht neu."

Das ist derselbe Gedanke wie der Ausschlussvermerk aus Migration 0002, nur für die andere Art von
Kante. Eine gelöschte Zeile hinterlässt nichts: Der nächste Lauf der Kantenerkennung fände dasselbe
Paar mit derselben Ähnlichkeit, fragte dasselbe Modell und bekäme dieselbe Antwort. Ohne einen Ort
für das *Nein* wäre die Kuration nach einem Lauf verschwunden — genau der Fall, den Leitprinzip 15
ausschließt.

Der Schlüssel ist das Kantentripel aus §7.4 (``ux_edges_triple``) und nicht die ``edge_id``: Die ID
verschwindet mit der Kante, das Tripel ist das, was ein Folgelauf erneut erzeugen würde.

Die Richtung ist Teil des Schlüssels. Wer ``a depends_on b`` verwirft, hat nichts über
``b depends_on a`` gesagt — das kann die richtige Richtung derselben Beobachtung sein, und §17.2
bietet dafür ausdrücklich die Aktion "Richtung umdrehen" an.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_edge_rejections"
down_revision: str | None = "0002_cluster_exclusions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Legt die Tabelle der verworfenen Kantentripel an."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS edge_rejections (
            from_store   TEXT NOT NULL,
            from_id      TEXT NOT NULL,
            to_store     TEXT NOT NULL,
            to_id        TEXT NOT NULL,
            kind         TEXT NOT NULL,
            rejected_by  TEXT NOT NULL,
            reason       TEXT,
            rejected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (from_store, from_id, to_store, to_id, kind)
        )
        """
    )
    # Die Kantenerkennung fragt je Paar, ob es gesperrt ist — und zwar in beiden Richtungen, weil
    # sie die Richtung erst vom Modell erfährt (§14.3). Der Index deckt die Abfrage über den
    # Ausgangspunkt ab; die Gegenrichtung läuft über den Primärschlüssel.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_edge_rejections_from
            ON edge_rejections (from_store, from_id)
        """
    )


def downgrade() -> None:
    """Nimmt die Tabelle zurück."""
    op.execute("DROP INDEX IF EXISTS ix_edge_rejections_from")
    op.execute("DROP TABLE IF EXISTS edge_rejections")
