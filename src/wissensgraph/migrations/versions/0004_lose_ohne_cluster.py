"""Cluster-Knoten sind keine losen Knoten.

Revision ID: 0004_lose_ohne_cluster
Revises: 0003_edge_rejections

An echten Daten gefunden: Ein ``link-orphans``-Lauf meldete ``loose_before: 34`` und danach
``loose_after: 57`` — er erzeugte mehr lose Knoten, als er anband. Die Differenz von 23 waren
genau die Cluster, die derselbe Lauf für die Waisen neu angelegt hatte.

Der Grund steckt in der Sicht selbst. ``semantic_degree`` zählt nach §7.7 bewusst nur semantische
Kanten und lässt ``member`` außen vor — sonst wäre jedes Cluster-Mitglied schon durch seine
Zugehörigkeit angebunden, und die Frage aus §15.1 ("hängt dieser Inhalt irgendwo im Wissen?")
wäre nicht mehr gestellt. Für die Mitglieder ist das richtig. Für das Cluster selbst kehrt es sich
um: Ein Cluster *besteht* aus ``member``-Kanten und hat von Natur aus keine semantischen. Es fiel
damit immer und unvermeidlich in die eigene Waisenliste.

Damit war die Anbindung selbstverneinend: Jede erfolgreich angebundene Gruppe erhöhte die Zahl der
losen Knoten. Die Kennzahl, an der §24 den Erfolg des Laufs misst, maß das Gegenteil.

Ein Cluster ist ein Strukturknoten und kein Inhalt — es kann nicht verwaisen, weil es nichts gibt,
woran es hängen müsste. Die Sicht nimmt es deshalb aus. Der Typname steht hier fest wie ``member``
eine Zeile darüber: Beide sind Bausteine des Schemas aus §7 und keine Taxonomie-Einträge, die eine
Quelle mitbringt.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_lose_ohne_cluster"
down_revision: str | None = "0003_edge_rejections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SICHT = """
CREATE OR REPLACE VIEW v_loose_concepts AS
SELECT c.id, c.scope, c.type, c.title,
       count(e.id) FILTER (WHERE e.kind <> 'member') AS semantic_degree
FROM concepts c
LEFT JOIN edges e
  ON (e.from_id = c.id AND e.from_store = c.store)
  OR (e.to_id   = c.id AND e.to_store   = c.store)
WHERE c.status <> 'tombstone'{zusatz}
GROUP BY c.id, c.scope, c.type, c.title
"""


def upgrade() -> None:
    """Nimmt Cluster aus der Sicht der losen Knoten."""
    op.execute(_SICHT.format(zusatz="\n  AND c.type <> 'Cluster'"))


def downgrade() -> None:
    """Stellt die Sicht aus 0001 wieder her."""
    op.execute(_SICHT.format(zusatz=""))
