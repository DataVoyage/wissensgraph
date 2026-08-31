/**
 * Der Server-Zustand als TanStack-Query-Hooks (§17.1).
 *
 * Der Grundsatz dahinter steht in §17.3: "Jede Kuration ist sofort persistent und im change_log
 * sichtbar." Deshalb hält diese Oberfläche keinen gespiegelten Zustand — sie liest, schreibt und
 * lädt danach neu. Was auf dem Bildschirm steht, ist damit immer das, was in der Datenbank steht,
 * und nicht das, was die Oberfläche für wahrscheinlich hält.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";

import { get, send } from "./client";
import type {
  ClusterCreate,
  ClusterMerge,
  ClusterPatch,
  ClusterSplit,
  ConceptCreate,
  ConceptPatch,
  EdgeCreate,
  EmbedRun,
  MembersAdd,
  OrphanRun,
  ScopeRun,
  SearchRequest,
  SyncRun,
  TraverseRequest,
} from "./schema";
import type {
  ChangeEntry,
  ClusterDetail,
  ClusterSummary,
  Concept,
  ConceptDetail,
  CurationResult,
  CurationTask,
  EffectiveConfig,
  ModelRoute,
  Page,
  Run,
  SearchResult,
  Source,
  StoreStats,
  Traversal,
  UsageRow,
} from "./types";

/** Filter des Dokumentenbrowsers (§17.2 Ansicht 2). */
export interface ConceptFilters {
  store?: string;
  scope?: string;
  type?: string;
  status?: string;
  q?: string;
  cluster_id?: string;
  orphan?: boolean;
  curated?: boolean;
  unverified?: boolean;
  limit?: number;
  cursor?: string;
}

export function useConfig(): UseQueryResult<EffectiveConfig> {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => get<EffectiveConfig>("/api/v1/config/effective"),
    // Die Konfiguration ändert sich nur beim Neustart eines Prozesses; sie bei jedem
    // Ansichtswechsel neu zu holen wäre Last ohne Erkenntnis.
    staleTime: Infinity,
  });
}

export function useStats(): UseQueryResult<{ stores: StoreStats[] }> {
  return useQuery({
    queryKey: ["stats"],
    queryFn: () => get<{ stores: StoreStats[] }>("/api/v1/stats"),
  });
}

export function useConcepts(filter: ConceptFilters): UseQueryResult<Page<Concept>> {
  return useQuery({
    queryKey: ["concepts", filter],
    queryFn: () => get<Page<Concept>>("/api/v1/concepts", { ...filter }),
  });
}

export function useConcept(
  id: string | null,
  store?: string,
): UseQueryResult<ConceptDetail> {
  return useQuery({
    queryKey: ["concept", id, store],
    enabled: id !== null,
    queryFn: () => get<ConceptDetail>(`/api/v1/concepts/${encodeURI(id ?? "")}`, { store }),
  });
}

export function useHistory(
  id: string | null,
  store?: string,
): UseQueryResult<{ items: ChangeEntry[] }> {
  return useQuery({
    queryKey: ["history", id, store],
    enabled: id !== null,
    queryFn: () =>
      get<{ items: ChangeEntry[] }>(`/api/v1/concepts/${encodeURI(id ?? "")}/history`, { store }),
  });
}

export function useSimilar(
  id: string | null,
  store?: string,
): UseQueryResult<{ model_key: string | null; items: Array<Concept & { similarity: number }> }> {
  return useQuery({
    queryKey: ["similar", id, store],
    enabled: id !== null,
    queryFn: () =>
      get<{ model_key: string | null; items: Array<Concept & { similarity: number }> }>(
        `/api/v1/concepts/${encodeURI(id ?? "")}/similar`,
        { store },
      ),
  });
}

export function useNeighbors(
  id: string | null,
  store?: string,
): UseQueryResult<Traversal> {
  return useQuery({
    queryKey: ["neighbors", id, store],
    enabled: id !== null,
    queryFn: () => get<Traversal>(`/api/v1/graph/neighbors/${encodeURI(id ?? "")}`, { store }),
  });
}

export function useClusters(
  store?: string,
  scope?: string,
): UseQueryResult<Page<ClusterSummary>> {
  return useQuery({
    queryKey: ["clusters", store, scope],
    queryFn: () => get<Page<ClusterSummary>>("/api/v1/clusters", { store, scope, limit: 200 }),
  });
}

export function useCluster(
  id: string | null,
  store?: string,
): UseQueryResult<ClusterDetail> {
  return useQuery({
    queryKey: ["cluster", id, store],
    enabled: id !== null,
    queryFn: () => get<ClusterDetail>(`/api/v1/clusters/${encodeURI(id ?? "")}`, { store }),
  });
}

export function useQueue(store?: string): UseQueryResult<{ items: CurationTask[] }> {
  return useQuery({
    queryKey: ["queue", store],
    queryFn: () => get<{ items: CurationTask[] }>("/api/v1/curation/queue", { store }),
  });
}

export function useJournal(store?: string): UseQueryResult<{ items: ChangeEntry[] }> {
  return useQuery({
    queryKey: ["journal", store],
    queryFn: () => get<{ items: ChangeEntry[] }>("/api/v1/curation/journal", { store }),
  });
}

export function useRuns(store?: string): UseQueryResult<{ items: Run[] }> {
  return useQuery({
    queryKey: ["runs", store],
    queryFn: () => get<{ items: Run[] }>("/api/v1/runs", { store }),
  });
}

export function useSources(): UseQueryResult<{ items: Source[] }> {
  return useQuery({ queryKey: ["sources"], queryFn: () => get<{ items: Source[] }>("/api/v1/sources") });
}

export function useModels(): UseQueryResult<{
  tasks: ModelRoute[];
  policies: Record<string, string[] | null>;
  budget: Record<string, unknown>;
}> {
  return useQuery({
    queryKey: ["models"],
    queryFn: () =>
      get<{
        tasks: ModelRoute[];
        policies: Record<string, string[] | null>;
        budget: Record<string, unknown>;
      }>("/api/v1/models"),
  });
}

export function useUsage(store?: string): UseQueryResult<{ items: UsageRow[] }> {
  return useQuery({
    queryKey: ["usage", store],
    queryFn: () => get<{ items: UsageRow[] }>("/api/v1/models/usage", { store }),
  });
}

export function useSearch(): UseMutationResult<SearchResult, Error, SearchRequest> {
  return useMutation({
    mutationFn: (anfrage: SearchRequest) =>
      send<SearchResult>("POST", "/api/v1/graph/search", anfrage),
  });
}

export function useTraverse(): UseMutationResult<Traversal, Error, TraverseRequest> {
  return useMutation({
    mutationFn: (anfrage: TraverseRequest) =>
      send<Traversal>("POST", "/api/v1/graph/traverse", anfrage),
  });
}

/**
 * Nach jeder Kuration wird alles neu geladen, was sich geändert haben *kann*.
 *
 * Bewusst grob: Eine Kante zwischen zwei Konzepten berührt beide Detailansichten, die
 * Warteschlange, das Journal und womöglich ein Cluster. Ein feiner Abgleich wäre eine zweite
 * Modellierung der Fachregeln in der Oberfläche — genau das, was §17.1 ausschließt.
 */
function useInvalidierung(): () => Promise<void> {
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries();
  };
}

export function useCreateConcept(): UseMutationResult<CurationResult, Error, ConceptCreate> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (entwurf: ConceptCreate) =>
      send<CurationResult>("POST", "/api/v1/concepts", entwurf),
    onSuccess: neuladen,
  });
}

export function usePatchConcept(): UseMutationResult<
  CurationResult,
  Error,
  { id: string; store?: string; patch: ConceptPatch }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ id, store, patch }) =>
      send<CurationResult>("PATCH", `/api/v1/concepts/${encodeURI(id)}`, patch, { store }),
    onSuccess: neuladen,
  });
}

export function useAddEdge(): UseMutationResult<CurationResult, Error, EdgeCreate> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (entwurf: EdgeCreate) => send<CurationResult>("POST", "/api/v1/edges", entwurf),
    onSuccess: neuladen,
  });
}

export function useEdgeAction(): UseMutationResult<
  CurationResult,
  Error,
  { id: string; action: "verify" | "reject" | "delete"; store?: string; reason?: string }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ id, action, store, reason }) => {
      if (action === "delete") {
        return send<CurationResult>("DELETE", `/api/v1/edges/${id}`, undefined, { store });
      }
      const koerper = action === "reject" ? { reason: reason ?? null } : undefined;
      return send<CurationResult>("POST", `/api/v1/edges/${id}/${action}`, koerper, { store });
    },
    onSuccess: neuladen,
  });
}

export function useUndo(): UseMutationResult<
  CurationResult,
  Error,
  { entry_id: number; store?: string }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (anfrage) => send<CurationResult>("POST", "/api/v1/curation/undo", anfrage),
    onSuccess: neuladen,
  });
}

export function useCreateCluster(): UseMutationResult<CurationResult, Error, ClusterCreate> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (entwurf) => send<CurationResult>("POST", "/api/v1/clusters", entwurf),
    onSuccess: neuladen,
  });
}

export function usePatchCluster(): UseMutationResult<
  CurationResult,
  Error,
  { id: string; store?: string; patch: ClusterPatch }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ id, store, patch }) =>
      send<CurationResult>("PATCH", `/api/v1/clusters/${encodeURI(id)}`, patch, { store }),
    onSuccess: neuladen,
  });
}

export function useAddMembers(): UseMutationResult<
  { items: CurationResult[] },
  Error,
  { id: string; store?: string; body: MembersAdd }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ id, store, body }) =>
      send<{ items: CurationResult[] }>(
        "POST",
        `/api/v1/clusters/${encodeURI(id)}/members`,
        body,
        { store },
      ),
    onSuccess: neuladen,
  });
}

export function useRemoveMember(): UseMutationResult<
  CurationResult,
  Error,
  { clusterId: string; conceptId: string; store?: string }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ clusterId, conceptId, store }) =>
      send<CurationResult>(
        "DELETE",
        `/api/v1/clusters/${encodeURI(clusterId)}/members/${encodeURI(conceptId)}`,
        undefined,
        { store },
      ),
    onSuccess: neuladen,
  });
}

export function useSplitCluster(): UseMutationResult<
  CurationResult,
  Error,
  { id: string; store?: string; body: ClusterSplit }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ id, store, body }) =>
      send<CurationResult>("POST", `/api/v1/clusters/${encodeURI(id)}/split`, body, { store }),
    onSuccess: neuladen,
  });
}

export function useMergeClusters(): UseMutationResult<CurationResult, Error, ClusterMerge> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (anfrage) => send<CurationResult>("POST", "/api/v1/clusters/merge", anfrage),
    onSuccess: neuladen,
  });
}

/** Die Lauf-Arten, die sich aus der Betriebsansicht anstoßen lassen (§17.2 Ansicht 6). */
export type RunKind = "sync" | "embed" | "cluster" | "relations" | "link-orphans";

export function useStartRun(): UseMutationResult<
  Run,
  Error,
  { kind: RunKind; body: SyncRun | EmbedRun | ScopeRun | OrphanRun }
> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: ({ kind, body }) => send<Run>("POST", `/api/v1/runs/${kind}`, body),
    onSuccess: neuladen,
  });
}

export function useCancelRun(): UseMutationResult<Run, Error, string> {
  const neuladen = useInvalidierung();
  return useMutation({
    mutationFn: (id: string) => send<Run>("POST", `/api/v1/runs/${id}/cancel`),
    onSuccess: neuladen,
  });
}
