"""Die Router der HTTP-API (§16.2).

Ein Modul je Themengebiet, und jedes bleibt eine dünne Hülle: HTTP hinein, Dienstaufruf hinaus.
Die Aufteilung folgt den Abschnitten aus §16.2 und nicht der Datenbankstruktur — wer die
Endpunkte des Cluster-Arbeitsplatzes sucht, findet sie in ``clusters``.
"""

from wissensgraph.api.routers import (
    clusters,
    concepts,
    config,
    curation,
    graph,
    health,
    models,
    runs,
)

__all__ = [
    "clusters",
    "concepts",
    "config",
    "curation",
    "graph",
    "health",
    "models",
    "runs",
]
