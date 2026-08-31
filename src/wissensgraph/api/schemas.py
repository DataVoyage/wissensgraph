"""Die Ein- und Ausgabeformen der HTTP-API (§16.2).

Nur die **Eingaben** sind hier als Modelle beschrieben, die Ausgaben nicht. Der Grund ist keine
Bequemlichkeit: Die Antwortform entsteht in den Diensten (``as_dict()``), und ein zweites Schema
daneben wäre eine zweite Wahrheit über dieselbe Struktur — genau die Doppelung, die zwischen zwei
Stufen auseinanderdriftet. Was die API garantiert, prüfen die Tests gegen die Dienste, nicht gegen
eine Abschrift.

Für die Eingaben gilt das Gegenteil. Sie kommen von außen, und §16.1 verlangt für einen
Schemaverstoß eine eindeutige Antwort. Ein Pydantic-Modell liefert sie — und zugleich die Hälfte
des OpenAPI-Schemas, aus der der TypeScript-Client entsteht.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wissensgraph.config import defaults


class ApiModel(BaseModel):
    """Basis aller Eingabeformen: unbekannte Felder sind ein Fehler.

    ``extra='forbid'`` ist Absicht. Ein durchgereichtes Feld, das niemand liest, sieht für den
    Aufrufer aus wie eine angenommene Angabe — und wird erst dann auffällig, wenn jemand sich auf
    seine Wirkung verlässt.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Konzepte (§16.2)
# ---------------------------------------------------------------------------


class ConceptCreate(ApiModel):
    """``POST /api/v1/concepts`` — nur im ``personal``-Store (§16.2, §17.4)."""

    scope: str = Field(min_length=1)
    type: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    body: str | None = None
    tags: tuple[str, ...] = ()


class ConceptPatch(ApiModel):
    """``PATCH /api/v1/concepts/{id}``.

    Alle Felder sind optional, aber nicht alle sind an jedem Konzept erlaubt: An einem
    quellgespiegelten Konzept sind die Inhaltsfelder gesperrt (§17.4). Welche das sind, sagt die
    Detailansicht über ``locked_fields`` — die Oberfläche muss es *vor* dem Tippen wissen (§17.3).
    """

    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    body: str | None = None
    resource: str | None = None
    status: str | None = None
    tags: tuple[str, ...] | None = None
    audience: tuple[str, ...] | None = None

    def changes(self) -> dict[str, Any]:
        """Nur die tatsächlich gesetzten Felder — ``None`` heißt "nicht angefasst"."""
        return self.model_dump(exclude_unset=True)


# ---------------------------------------------------------------------------
# Graph (§16.2)
# ---------------------------------------------------------------------------


class TraverseRequest(ApiModel):
    """``POST /api/v1/graph/traverse`` (§12.1, §12.3)."""

    start_id: str = Field(min_length=1)
    store: str | None = None
    hops: int | None = Field(default=None, ge=1, le=defaults.TRAVERSAL_MAX_HOPS)
    kinds: tuple[str, ...] | None = None
    stores: tuple[str, ...] | None = None
    max_nodes: int | None = Field(default=None, ge=1)
    ranking_overrides: RankingOverrides | None = None


class RankingOverrides(ApiModel):
    """Abweichende Ranking-Gewichte für eine einzelne Abfrage (§12.3).

    Sie stehen im Request und nicht in der Konfiguration, weil §12.3 sie ausdrücklich als
    "``ranking_overrides``" der Traversierung führt: Wer eine Frage anders gewichten will, soll
    dafür nicht die Installation umstellen müssen.
    """

    hop_weight: float | None = Field(default=None, ge=0.0)
    density_weight: float | None = Field(default=None, ge=0.0)
    recency_weight: float | None = Field(default=None, ge=0.0)
    recency_half_life_days: float | None = Field(default=None, gt=0.0)


class SearchRequest(ApiModel):
    """``POST /api/v1/graph/search`` (§12.4)."""

    query: str = Field(min_length=1)
    scope: str | None = None
    store: str | None = None
    granularity: Literal["cluster", "document", "auto"] = defaults.SEARCH_GRANULARITY_AUTO
    limit: int | None = Field(default=None, ge=1, le=200)


# ---------------------------------------------------------------------------
# Kanten und Kuration (§16.2)
# ---------------------------------------------------------------------------


class EdgeCreate(ApiModel):
    """``POST /api/v1/edges`` — von Hand gesetzt, also ``curated = true``."""

    store: str | None = None
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    to_store: str | None = None
    kind: str = defaults.EDGE_KIND_REFERENCES


class EdgeReject(ApiModel):
    """``POST /api/v1/edges/{id}/reject``."""

    reason: str | None = Field(default=None, max_length=500)


class UndoRequest(ApiModel):
    """``POST /api/v1/curation/undo`` (§17.3)."""

    entry_id: int = Field(ge=1)
    store: str | None = None


# ---------------------------------------------------------------------------
# Cluster (§16.2)
# ---------------------------------------------------------------------------


class ClusterCreate(ApiModel):
    """``POST /api/v1/clusters`` — ein Cluster von Hand, aus einer Auswahl."""

    store: str | None = None
    scope: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    member_ids: tuple[str, ...] = ()


class ClusterPatch(ApiModel):
    """``PATCH /api/v1/clusters/{id}`` — sperrt die automatische Neubetitelung (§13.2)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class MembersAdd(ApiModel):
    """``POST /api/v1/clusters/{id}/members``."""

    concept_ids: tuple[str, ...] = Field(min_length=1)


class ClusterSplit(ApiModel):
    """``POST /api/v1/clusters/{id}/split``."""

    concept_ids: tuple[str, ...] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ClusterMerge(ApiModel):
    """``POST /api/v1/clusters/merge`` — die Kanten des Quellclusters werden umgehängt."""

    store: str | None = None
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Läufe (§16.2, §16.3)
# ---------------------------------------------------------------------------


class SyncRun(ApiModel):
    """``POST /api/v1/runs/sync``."""

    source: str = Field(min_length=1)
    full: bool = False
    dry_run: bool = False


class EmbedRun(ApiModel):
    """``POST /api/v1/runs/embed``."""

    scope: str = Field(min_length=1)
    rebuild: bool = False


class ScopeRun(ApiModel):
    """``POST /api/v1/runs/cluster`` und ``/runs/relations``."""

    scope: str = Field(min_length=1)
    dry_run: bool = False


class OrphanRun(ApiModel):
    """``POST /api/v1/runs/link-orphans`` — alle Parameter aus §15.4 als Body-Felder."""

    scope: str = Field(min_length=1)
    loose_threshold: int | None = Field(default=None, ge=0)
    proximity_top_n: int | None = Field(default=None, ge=1)
    proximity_auto_commit: float | None = Field(default=None, ge=0.0, le=1.0)
    proximity_candidate_band: float | None = Field(default=None, ge=0.0, le=1.0)
    use_llm: bool | None = None
    cluster_suggestion_limit: int | None = Field(default=None, ge=0)
    cluster_preview_members: int | None = Field(default=None, ge=1)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    pattern_files: tuple[str, ...] = ()
    dry_run: bool = False


TraverseRequest.model_rebuild()

__all__ = [
    "ApiModel",
    "ClusterCreate",
    "ClusterMerge",
    "ClusterPatch",
    "ClusterSplit",
    "ConceptCreate",
    "ConceptPatch",
    "EdgeCreate",
    "EdgeReject",
    "EmbedRun",
    "MembersAdd",
    "OrphanRun",
    "RankingOverrides",
    "ScopeRun",
    "SearchRequest",
    "SyncRun",
    "TraverseRequest",
    "UndoRequest",
]
