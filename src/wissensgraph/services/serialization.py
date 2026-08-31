"""Die JSON-Form der Domänenobjekte — an genau einer Stelle (§16.2).

Konzept, Kante und Journaleintrag verlassen das System über drei Wege: die HTTP-API, die CLI und
den MCP-Server. Beschriebe jeder von ihnen dieselben Felder selbst, driftete die Beschreibung
auseinander, sobald einer ein Feld ergänzt — und zwar still, weil ein fehlendes Feld in JSON kein
Fehler ist, sondern schlicht nicht da.

Deshalb steht die Übersetzung hier und nicht bei ihren Aufrufern. Der unmittelbare Anlass war ein
Fehler: :meth:`Traversal.as_dict` gab ``edges`` als *Zahl* aus, weil die CLI nur eine Zahl
brauchte. §16.2 verlangt für ``/graph/traverse`` aber "Knoten + Kanten + Scores", und die
Graph-Ansicht konnte deshalb keine einzige Kante zeichnen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge

#: Änderungsarten, die sich zurücknehmen lassen (§17.3). Alles andere ist entweder eine
#: Feststellung über die Welt (``source_deleted`` — die Quelle hat gelöscht, das nimmt kein Undo
#: zurück) oder ein Vermerk ohne eigene Wirkung (``curation_conflict``).
UNDOABLE_CHANGES: frozenset[ChangeType] = frozenset(
    {
        ChangeType.EDGE_ADDED,
        ChangeType.EDGE_REMOVED,
        ChangeType.CLUSTER_ASSIGNED,
        ChangeType.CLUSTER_REMOVED,
        ChangeType.VERIFIED,
        ChangeType.REJECTED,
        ChangeType.CREATED,
        ChangeType.UPDATED,
        ChangeType.STATUS_CHANGED,
    }
)


def zeit(wert: datetime | None) -> str | None:
    """ISO-8601 oder ``None`` — die Form, die ein JSON-Client ohne Konvertierung versteht."""
    return None if wert is None else wert.isoformat()


def konzept_dict(concept: Concept) -> dict[str, Any]:
    """Die Serialisierung eines Konzepts.

    ``body`` fehlt absichtlich: Eine Tabelle mit zweihundert Zeilen würde sonst zweihundert
    Fließtexte über die Leitung schicken, von denen keiner angezeigt wird. Wer ihn braucht, holt
    die Detailansicht.
    """
    return {
        "id": concept.id,
        "store": concept.store,
        "scope": concept.scope,
        "type": concept.type,
        "title": concept.title,
        "description": concept.description,
        "resource": concept.resource,
        "tags": list(concept.tags),
        "audience": list(concept.audience),
        "status": str(concept.status),
        "source_name": concept.source_name,
        "external_id": concept.external_id,
        "source_updated_at": zeit(concept.source_updated_at),
        "generated_by": concept.generated_by,
        "verified_by": concept.verified_by,
        "verified_at": zeit(concept.verified_at),
        "curated": concept.curated,
        "created_at": zeit(concept.created_at),
        "updated_at": zeit(concept.updated_at),
    }


def kante_dict(edge: Edge) -> dict[str, Any]:
    """Die Serialisierung einer Kante (§17.2, visuelle Kodierung).

    Vollständig, weil die Graph-Ansicht jedes dieser Felder in ein sichtbares Merkmal übersetzt:
    ``kind`` in den Linienstil, ``generated_by`` in die Linienfarbe, und die Kombination aus
    ``generated_by``, ``curated`` und ``verified_at`` in "gestrichelt" — Leitprinzip 6.
    """
    return {
        "id": str(edge.id),
        "from_store": edge.from_store,
        "from_id": edge.from_id,
        "to_store": edge.to_store,
        "to_id": edge.to_id,
        "kind": edge.kind,
        "weight": edge.weight,
        "confidence": edge.confidence,
        "reasoning": edge.reasoning,
        "resolved": edge.resolved,
        "generated_by": edge.generated_by,
        "verified_by": edge.verified_by,
        "verified_at": zeit(edge.verified_at),
        "curated": edge.curated,
        "created_at": zeit(edge.created_at),
    }


def journal_dict(entry: ChangeEntry) -> dict[str, Any]:
    """Die Serialisierung eines Journaleintrags (§16.2, ``/concepts/{id}/history``)."""
    return {
        "id": entry.id,
        "change_type": str(entry.change_type),
        "actor": entry.actor,
        "concept_id": entry.concept_id,
        "edge_id": None if entry.edge_id is None else str(entry.edge_id),
        "run_id": None if entry.run_id is None else str(entry.run_id),
        "changed_at": zeit(entry.changed_at),
        "detail": entry.detail,
        "undoable": entry.change_type in UNDOABLE_CHANGES,
    }


__all__ = [
    "UNDOABLE_CHANGES",
    "journal_dict",
    "kante_dict",
    "konzept_dict",
    "zeit",
]
