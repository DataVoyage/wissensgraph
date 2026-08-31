/**
 * Die Hooks als Vertrag: Pfad, Methode, Körper (§16.2).
 *
 * Sie sind die einzige Stelle, an der die Oberfläche Endpunkte benennt. Ein Tippfehler dort fällt
 * sonst erst im Betrieb auf — als leere Liste oder als `404`, den irgendeine Ansicht als "nichts
 * gefunden" anzeigt. Deshalb wird hier nicht das Ergebnis geprüft, sondern die *Anfrage*.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useAddEdge,
  useAddMembers,
  useCancelRun,
  useCluster,
  useConcept,
  useCreateCluster,
  useCreateConcept,
  useEdgeAction,
  useHistory,
  useJournal,
  useMergeClusters,
  useNeighbors,
  usePatchCluster,
  usePatchConcept,
  useRemoveMember,
  useRuns,
  useSimilar,
  useSplitCluster,
  useStartRun,
  useTraverse,
  useUndo,
} from "./hooks";
import { FakeApi } from "../test-support";

let api: FakeApi;

/** Der Query-Cache für `renderHook` — je Test ein frischer, damit nichts überdauert. */
function Wrapper({ children }: { children: ReactNode }): JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  api = new FakeApi();
  api.on("GET", /.*/, () => ({ items: [], store: "shared", next_cursor: null }));
  api.on("POST", /.*/, () => ({ entry: {}, concept: null, edge: null }));
  api.on("PATCH", /.*/, () => ({ entry: {}, concept: null, edge: null }));
  api.on("DELETE", /.*/, () => ({ entry: {}, concept: null, edge: null }));
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ein Prüfling, der eine Mutation beim Rendern genau einmal auslöst. */
function ausloesen(mutate: () => void): void {
  mutate();
}

describe("Lesende Hooks", () => {
  it.each([
    ["Detail", () => useConcept("confluence:1", "shared"), "/api/v1/concepts/confluence:1"],
    ["Historie", () => useHistory("confluence:1"), "/api/v1/concepts/confluence:1/history"],
    ["Ähnliche", () => useSimilar("confluence:1"), "/api/v1/concepts/confluence:1/similar"],
    ["Nachbarn", () => useNeighbors("confluence:1"), "/api/v1/graph/neighbors/confluence:1"],
    ["Cluster", () => useCluster("cluster:a"), "/api/v1/clusters/cluster:a"],
    ["Journal", () => useJournal("shared"), "/api/v1/curation/journal"],
    ["Läufe", () => useRuns("shared"), "/api/v1/runs"],
  ])("%s fragt den richtigen Pfad", async (_name, hook, pfad) => {
    // `hook` ist über die Tabelle hinweg unterschiedlich typisiert; hier zählt nur, *dass* er
    // etwas abfragt — und wohin.
    const { result } = renderHook(hook as () => { isFetched: boolean }, { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isFetched).toBe(true));
    expect(api.calls.some((aufruf) => aufruf.url.startsWith(pfad))).toBe(true);
  });

  it("fragt nichts ab, solange keine ID ausgewählt ist", async () => {
    renderHook(() => useConcept(null), { wrapper: Wrapper });

    expect(api.calls).toEqual([]);
  });
});

describe("Schreibende Hooks", () => {
  it("legt ein Konzept an", async () => {
    const { result } = renderHook(() => useCreateConcept(), { wrapper: Wrapper });

    ausloesen(() =>
      result.current.mutate({ scope: "personal", type: "Note", title: "Neu" }),
    );

    await waitFor(() =>
      expect(
        api.calls.some(
          (aufruf) => aufruf.method === "POST" && aufruf.url === "/api/v1/concepts",
        ),
      ).toBe(true),
    );
  });

  it("ändert ein Konzept im richtigen Store", async () => {
    const { result } = renderHook(() => usePatchConcept(), { wrapper: Wrapper });

    ausloesen(() =>
      result.current.mutate({
        id: "note:1",
        store: "personal",
        patch: { status: "deprecated" },
      }),
    );

    await waitFor(() =>
      expect(
        api.calls.some((aufruf) => aufruf.url === "/api/v1/concepts/note:1?store=personal"),
      ).toBe(true),
    );
  });

  it("legt eine Kante an", async () => {
    const { result } = renderHook(() => useAddEdge(), { wrapper: Wrapper });

    ausloesen(() => result.current.mutate({ from_id: "a", to_id: "b" }));

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url === "/api/v1/edges")).toBe(true),
    );
  });

  it.each([
    ["verify", "POST", "/api/v1/edges/e1/verify"],
    ["reject", "POST", "/api/v1/edges/e1/reject"],
    ["delete", "DELETE", "/api/v1/edges/e1"],
  ] as const)("führt die Kantenaktion %s aus", async (action, methode, pfad) => {
    const { result } = renderHook(() => useEdgeAction(), { wrapper: Wrapper });

    ausloesen(() => result.current.mutate({ id: "e1", action }));

    await waitFor(() =>
      expect(
        api.calls.some((aufruf) => aufruf.method === methode && aufruf.url === pfad),
      ).toBe(true),
    );
  });

  it("nimmt einen Journaleintrag zurück", async () => {
    const { result } = renderHook(() => useUndo(), { wrapper: Wrapper });

    ausloesen(() => result.current.mutate({ entry_id: 3, store: "shared" }));

    await waitFor(() =>
      expect(
        api.calls.some((aufruf) => aufruf.url === "/api/v1/curation/undo"),
      ).toBe(true),
    );
  });

  it.each([
    [
      "Cluster anlegen",
      () => useCreateCluster(),
      (mutate: (wert: never) => void) =>
        mutate({ scope: "engineering", title: "Neu" } as never),
      "/api/v1/clusters",
    ],
    [
      "Cluster umbenennen",
      () => usePatchCluster(),
      (mutate: (wert: never) => void) =>
        mutate({ id: "cluster:a", patch: { title: "X" } } as never),
      "/api/v1/clusters/cluster:a",
    ],
    [
      "Mitglieder hinzufügen",
      () => useAddMembers(),
      (mutate: (wert: never) => void) =>
        mutate({ id: "cluster:a", body: { concept_ids: ["c1"] } } as never),
      "/api/v1/clusters/cluster:a/members",
    ],
    [
      "Mitglied entfernen",
      () => useRemoveMember(),
      (mutate: (wert: never) => void) =>
        mutate({ clusterId: "cluster:a", conceptId: "c1" } as never),
      "/api/v1/clusters/cluster:a/members/c1",
    ],
    [
      "Ausgliedern",
      () => useSplitCluster(),
      (mutate: (wert: never) => void) =>
        mutate({ id: "cluster:a", body: { concept_ids: ["c1"], title: "T" } } as never),
      "/api/v1/clusters/cluster:a/split",
    ],
    [
      "Verschmelzen",
      () => useMergeClusters(),
      (mutate: (wert: never) => void) =>
        mutate({ source_id: "cluster:a", target_id: "cluster:b" } as never),
      "/api/v1/clusters/merge",
    ],
    [
      "Lauf starten",
      () => useStartRun(),
      (mutate: (wert: never) => void) =>
        mutate({ kind: "embed", body: { scope: "engineering" } } as never),
      "/api/v1/runs/embed",
    ],
    [
      "Lauf abbrechen",
      () => useCancelRun(),
      (mutate: (wert: never) => void) => mutate("r1" as never),
      "/api/v1/runs/r1/cancel",
    ],
    [
      "Traversieren",
      () => useTraverse(),
      (mutate: (wert: never) => void) => mutate({ start_id: "c1" } as never),
      "/api/v1/graph/traverse",
    ],
  ])("%s spricht den richtigen Endpunkt an", async (_name, hook, ausloesen_, pfad) => {
    const { result } = renderHook(hook as never, { wrapper: Wrapper });

    ausloesen_((result.current as { mutate: (wert: never) => void }).mutate);

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.startsWith(pfad))).toBe(true),
    );
  });
});
