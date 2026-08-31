/**
 * ERZEUGT — NICHT VON HAND ÄNDERN.
 *
 * Quelle: das OpenAPI-Schema der HTTP-API (§16.1).
 * Neu erzeugen mit: `uv run python scripts/generate_client.py`
 *
 * Enthalten sind die Eingabeformen der API und die Liste ihrer Endpunkte. Die Antwortformen
 * stehen in `types.ts` und sind bewusst von Hand beschrieben: Sie entstehen in den Diensten und
 * nicht in einem Schema, und eine abgeleitete Beschreibung behauptete eine Genauigkeit, die es
 * nicht gibt.
 */



/** ``POST /api/v1/clusters`` — ein Cluster von Hand, aus einer Auswahl. */
export interface ClusterCreate {
  store?: string | null;
  scope: string;
  title: string;
  description?: string | null;
  member_ids?: Array<string>;
}

/** ``POST /api/v1/clusters/merge`` — die Kanten des Quellclusters werden umgehängt. */
export interface ClusterMerge {
  store?: string | null;
  source_id: string;
  target_id: string;
}

/** ``PATCH /api/v1/clusters/{id}`` — sperrt die automatische Neubetitelung (§13.2). */
export interface ClusterPatch {
  title?: string | null;
  description?: string | null;
}

/** ``POST /api/v1/clusters/{id}/split``. */
export interface ClusterSplit {
  concept_ids: Array<string>;
  title: string;
  description?: string | null;
}

/** ``POST /api/v1/concepts`` — nur im ``personal``-Store (§16.2, §17.4). */
export interface ConceptCreate {
  scope: string;
  type: string;
  title: string;
  description?: string | null;
  body?: string | null;
  tags?: Array<string>;
}

/** ``PATCH /api/v1/concepts/{id}``. */
export interface ConceptPatch {
  title?: string | null;
  description?: string | null;
  body?: string | null;
  resource?: string | null;
  status?: string | null;
  tags?: Array<string> | null;
  audience?: Array<string> | null;
}

/** ``POST /api/v1/edges`` — von Hand gesetzt, also ``curated = true``. */
export interface EdgeCreate {
  store?: string | null;
  from_id: string;
  to_id: string;
  to_store?: string | null;
  kind?: string;
}

/** ``POST /api/v1/edges/{id}/reject``. */
export interface EdgeReject {
  reason?: string | null;
}

/** ``POST /api/v1/runs/embed``. */
export interface EmbedRun {
  scope: string;
  rebuild?: boolean;
}

/** ``POST /api/v1/clusters/{id}/members``. */
export interface MembersAdd {
  concept_ids: Array<string>;
}

/** ``POST /api/v1/runs/link-orphans`` — alle Parameter aus §15.4 als Body-Felder. */
export interface OrphanRun {
  scope: string;
  loose_threshold?: number | null;
  proximity_top_n?: number | null;
  proximity_auto_commit?: number | null;
  proximity_candidate_band?: number | null;
  use_llm?: boolean | null;
  cluster_suggestion_limit?: number | null;
  cluster_preview_members?: number | null;
  min_confidence?: number | null;
  pattern_files?: Array<string>;
  dry_run?: boolean;
}

/** Abweichende Ranking-Gewichte für eine einzelne Abfrage (§12.3). */
export interface RankingOverrides {
  hop_weight?: number | null;
  density_weight?: number | null;
  recency_weight?: number | null;
  recency_half_life_days?: number | null;
}

/** Die Arten eines Laufs (§7.4, Spalte ``runs.kind``). */
export interface RunKind {
}

/** ``POST /api/v1/runs/cluster`` und ``/runs/relations``. */
export interface ScopeRun {
  scope: string;
  dry_run?: boolean;
}

/** ``POST /api/v1/graph/search`` (§12.4). */
export interface SearchRequest {
  query: string;
  scope?: string | null;
  store?: string | null;
  granularity?: "cluster" | "document" | "auto";
  limit?: number | null;
}

/** ``POST /api/v1/runs/sync``. */
export interface SyncRun {
  source: string;
  full?: boolean;
  dry_run?: boolean;
}

/** ``POST /api/v1/graph/traverse`` (§12.1, §12.3). */
export interface TraverseRequest {
  start_id: string;
  store?: string | null;
  hops?: number | null;
  kinds?: Array<string> | null;
  stores?: Array<string> | null;
  max_nodes?: number | null;
  ranking_overrides?: RankingOverrides | null;
}

/** ``POST /api/v1/curation/undo`` (§17.3). */
export interface UndoRequest {
  entry_id: number;
  store?: string | null;
}

/** Alle Pfade der API — eine Vertippsicherung für den Client. */
export type ApiPath =
  | "/api/v1/clusters"
  | "/api/v1/clusters/merge"
  | "/api/v1/clusters/{cluster_id}"
  | "/api/v1/clusters/{cluster_id}/members"
  | "/api/v1/clusters/{cluster_id}/members/{concept_id}"
  | "/api/v1/clusters/{cluster_id}/split"
  | "/api/v1/concepts"
  | "/api/v1/concepts/{concept_id}"
  | "/api/v1/concepts/{concept_id}/history"
  | "/api/v1/concepts/{concept_id}/similar"
  | "/api/v1/config/effective"
  | "/api/v1/curation/journal"
  | "/api/v1/curation/queue"
  | "/api/v1/curation/undo"
  | "/api/v1/edges"
  | "/api/v1/edges/{edge_id}"
  | "/api/v1/edges/{edge_id}/reject"
  | "/api/v1/edges/{edge_id}/verify"
  | "/api/v1/graph/loose"
  | "/api/v1/graph/neighbors/{concept_id}"
  | "/api/v1/graph/overview"
  | "/api/v1/graph/search"
  | "/api/v1/graph/traverse"
  | "/api/v1/models"
  | "/api/v1/models/usage"
  | "/api/v1/runs"
  | "/api/v1/runs/cluster"
  | "/api/v1/runs/embed"
  | "/api/v1/runs/link-orphans"
  | "/api/v1/runs/relations"
  | "/api/v1/runs/sync"
  | "/api/v1/runs/{run_id}"
  | "/api/v1/runs/{run_id}/cancel"
  | "/api/v1/runs/{run_id}/events"
  | "/api/v1/sources"
  | "/api/v1/stats"
  | "/healthz"
  | "/readyz";
