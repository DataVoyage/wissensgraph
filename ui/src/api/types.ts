/**
 * Die Antwortformen der API, wie die Oberfläche sie benutzt (§16.2).
 *
 * Von Hand beschrieben und nicht erzeugt: Die Antworten entstehen in den Diensten als
 * `as_dict()` und stehen deshalb nicht vollständig im OpenAPI-Schema. Was hier steht, ist die
 * Teilmenge, auf die sich diese Oberfläche verlässt — und damit zugleich die Liste dessen, was
 * eine Schemaänderung brechen würde.
 */

/** Ein Konzept in Listen (ohne `body`) und in der Detailansicht (mit). */
export interface Concept {
  id: string;
  store: string;
  scope: string;
  type: string;
  title: string | null;
  description: string | null;
  resource: string | null;
  tags: string[];
  audience: string[];
  status: string;
  source_name: string | null;
  external_id: string | null;
  source_updated_at: string | null;
  generated_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
  curated: boolean;
  created_at: string;
  updated_at: string;
}

/** Eine Kante mit allem, was §17.2 für die visuelle Kodierung braucht. */
export interface Edge {
  id: string;
  from_store: string;
  from_id: string;
  to_store: string;
  to_id: string;
  kind: string;
  weight: number | null;
  confidence: number | null;
  reasoning: string | null;
  resolved: boolean;
  generated_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
  curated: boolean;
  created_at: string;
}

/** Die Detailansicht eines Konzepts (§16.2). */
export interface ConceptDetail extends Concept {
  body: string | null;
  content_hash: string | null;
  outgoing: Edge[];
  incoming: Edge[];
  clusters: Array<{ id: string; title: string | null }>;
  /** Felder, die an diesem Konzept gesperrt sind — §17.3 verlangt sie *sichtbar*. */
  locked_fields: string[];
}

/** Ein Eintrag des Änderungsjournals (§7.4). */
export interface ChangeEntry {
  id: number | null;
  change_type: string;
  actor: string;
  concept_id: string | null;
  edge_id: string | null;
  run_id: string | null;
  changed_at: string | null;
  detail: Record<string, unknown> | null;
  /** Ob sich dieser Eintrag zurücknehmen lässt (§17.3). */
  undoable: boolean;
}

/** Das Ergebnis einer Kuration — mit dem Journaleintrag für das Undo. */
export interface CurationResult {
  entry: ChangeEntry;
  concept: Concept | null;
  edge: Edge | null;
  detail: Record<string, unknown> | null;
}

/** Ein Knoten einer Traversierung (§12.3). */
export interface GraphNode {
  id: string;
  store: string;
  scope: string;
  type: string;
  title: string | null;
  status: string;
  hop: number;
  score: number;
  density: number;
}

/** Das Ergebnis einer Traversierung (§12.1). */
export interface Traversal {
  /** Die Startknoten als `"<store>:<id>"`. */
  start: string[];
  nodes: GraphNode[];
  edges: Edge[];
  /** Die Anzahl der Kanten — die CLI zeigt sie in ihrer Zusammenfassung. */
  edge_count?: number;
  hops: number;
  truncated: boolean;
  queries: number;
}

/**
 * Das Ergebnis einer Suche; `mode` sagt, wie gesucht wurde (§12.4).
 *
 * Das Feld heißt `hits` und nicht `nodes`: Ein Treffer ist etwas anderes als ein Knoten einer
 * Traversierung — er hat keine Hop-Distanz, sondern einen Rang. Die API unterscheidet beides,
 * und diese Beschreibung tut es auch.
 */
export interface SearchResult {
  store: string;
  query: string;
  mode: "lexical" | "cluster" | "hybrid";
  hits: GraphNode[];
}

/** Ein Cluster mit Mitgliederzahl (§16.2). */
export interface ClusterSummary extends Concept {
  member_count: number;
  centroid_age_seconds: number | null;
}

/** Die Detailansicht eines Clusters (§17.2 Ansicht 3). */
export interface ClusterDetail extends Concept {
  members: Concept[];
  related: Array<{ id: string; title: string | null; similarity: number }>;
  centroid_age_seconds: number | null;
  /** Ob der Titel von Hand gesetzt wurde — dann bleibt die Neubetitelung aus (§13.2). */
  manual_title: boolean;
}

/** Ein offener Posten der Kurationsliste (§16.2). */
export interface CurationTask {
  kind: "unverified_edge" | "supersedes" | "relabel" | "cluster_suggestion";
  store: string;
  confidence: number | null;
  edge: Edge | null;
  concepts: Concept[];
  entry: ChangeEntry | null;
}

/** Ein Lauf (§7.4). */
export interface Run {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  started_at: string | null;
  finished_at: string | null;
  progress: number;
  stats: Record<string, unknown>;
  error: string | null;
  store?: string;
}

/** Eine konfigurierte Quelle mit Zustand und letztem Lauf (§16.2). */
export interface Source {
  name: string;
  adapter: string;
  enabled: boolean;
  id_prefix: string;
  scope: string;
  usable: boolean;
  health: { state: string; detail: string };
  capabilities: Record<string, unknown>;
  last_run: Run | null;
}

/** Die Bestandszahlen eines Stores (§16.2). */
export interface StoreStats {
  store: string;
  concepts: number;
  edges: number;
  clusters: number;
  loose: number;
  by_scope: Record<string, number>;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
}

/** Ein aufgelöstes Task-Profil (§11.3). */
export interface ModelRoute {
  task: string;
  provider: string;
  model: string;
  model_key: string;
  local: boolean;
  dim: number | null;
  temperature: number | null;
  configured: boolean;
  fallbacks: string[];
  /** Nur bei Vertex belegt: der Host, der tatsächlich angesprochen wird. */
  endpoint: string | null;
  generated_by: string;
}

/** Eine Zeile der Modellnutzung (§21.2). */
export interface UsageRow {
  task: string;
  provider: string;
  model: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_estimate_eur: number;
  cache_hits: number;
  errors: number;
}

/** Eine Seite einer cursor-basierten Liste (§16.1). */
export interface Page<T> {
  store: string;
  items: T[];
  next_cursor: string | null;
}

/**
 * Die aufgelöste Konfiguration, soweit die Oberfläche sie braucht (§17.1).
 *
 * Sie ist der Grund, warum in dieser Oberfläche keine Fachregel steht: Welche Scopes es gibt,
 * welche Typen wo erlaubt sind und welche Kantenarten existieren, kommt von hier.
 */
export interface EffectiveConfig {
  env: string;
  scopes: Array<{ name: string; store: string; description: string | null }>;
  concept_types: Array<{ name: string; stores: string[]; source_mirrored: boolean }>;
  edge_kinds: { structural: string[]; semantic: string[] };
  stores: Record<string, unknown>;
  orphans: Record<string, unknown>;
  api: { auth_mode: string };
}
