"""Domänenkern: Konzepte, Kanten, Änderungen und die Regeln der Kernoperation (§7, §10.2).

Dieses Paket kennt keine Datenbank, keine HTTP-Schicht und keine Quellsysteme — durchgesetzt
durch einen import-linter-Kontrakt (Leitprinzip 13). Was hier steht, ist ohne jede Infrastruktur
ausführbar und damit auch ohne jede Infrastruktur zu prüfen.
"""

from __future__ import annotations

from wissensgraph.domain.changes import (
    CONFLICT_SOURCE_HASH_KEY,
    ChangeEntry,
    ChangeType,
)
from wissensgraph.domain.concepts import Concept, ConceptDraft, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft, new_edge_id
from wissensgraph.domain.hashing import content_hash
from wissensgraph.domain.ids import (
    InvalidConceptIdError,
    concept_id,
    is_valid_concept_id,
    new_cluster_id,
    new_note_id,
    project_id,
    source_concept_id,
    split_concept_id,
)
from wissensgraph.domain.references import extract_references
from wissensgraph.domain.runs import Run, RunKind, RunStatus, new_run_id
from wissensgraph.domain.upsert import UpsertOutcome, UpsertPlan, plan_upsert

__all__ = [
    "CONFLICT_SOURCE_HASH_KEY",
    "ChangeEntry",
    "ChangeType",
    "Concept",
    "ConceptDraft",
    "ConceptStatus",
    "Edge",
    "EdgeDraft",
    "InvalidConceptIdError",
    "Run",
    "RunKind",
    "RunStatus",
    "UpsertOutcome",
    "UpsertPlan",
    "concept_id",
    "content_hash",
    "extract_references",
    "is_valid_concept_id",
    "new_cluster_id",
    "new_edge_id",
    "new_note_id",
    "new_run_id",
    "plan_upsert",
    "project_id",
    "source_concept_id",
    "split_concept_id",
]
