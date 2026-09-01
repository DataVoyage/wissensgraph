/**
 * Der abgefangene API-Bestand für die Playwright-Läufe.
 *
 * Klein und handgeschrieben: Ein Cluster mit zwei Mitgliedern, ein zweites Cluster als Ziel, eine
 * persönliche Notiz mit einer Brücke, ein unbestätigter Modellvorschlag und ein Lauf. Mehr braucht
 * keiner der Kernflüsse aus §24 — und weniger reicht für keinen.
 */

import type { Page, Route } from "@playwright/test";

export const KONFIGURATION = {
  env: "test",
  scopes: [
    { name: "engineering", store: "shared", description: null },
    { name: "personal", store: "personal", description: null },
  ],
  concept_types: [
    { name: "Confluence Page", stores: ["shared"], source_mirrored: true },
    { name: "Cluster", stores: ["shared", "personal"], source_mirrored: false },
    { name: "Note", stores: ["personal"], source_mirrored: false },
  ],
  edge_kinds: { structural: ["member", "related"], semantic: ["references", "depends_on"] },
  stores: { shared: {}, personal: {} },
  orphans: {
    loose_threshold: 1,
    proximity_top_n: 30,
    proximity_auto_commit: 0.85,
    proximity_candidate_band: 0.6,
    use_llm: true,
    min_confidence: 0.6,
  },
  api: { auth_mode: "token" },
};

function konzept(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "confluence:1",
    store: "shared",
    scope: "engineering",
    type: "Confluence Page",
    title: "Faktentabellen laden",
    description: "Die nächtliche Ladestrecke.",
    resource: null,
    tags: [],
    audience: [],
    status: "stable",
    source_name: "confluence-eng",
    external_id: "1",
    source_updated_at: null,
    generated_by: null,
    verified_by: null,
    verified_at: null,
    curated: false,
    created_at: "2026-03-01T12:00:00+00:00",
    updated_at: "2026-03-01T12:00:00+00:00",
    ...over,
  };
}

const NOTIZ = konzept({
  id: "note:1",
  store: "personal",
  scope: "personal",
  type: "Note",
  title: "Onboarding-Notiz",
  description: "Was ich mir zum Warehouse gemerkt habe.",
  source_name: null,
  external_id: null,
  curated: true,
});

const CLUSTER_A = konzept({
  id: "cluster:a",
  type: "Cluster",
  title: "Warehouse",
  source_name: null,
  external_id: null,
});

const CLUSTER_B = konzept({
  id: "cluster:b",
  type: "Cluster",
  title: "Incident Response",
  source_name: null,
  external_id: null,
});

const MITGLIED_2 = konzept({ id: "confluence:2", title: "Partitionen pflegen" });

function kante(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    from_store: "shared",
    from_id: "confluence:1",
    to_store: "shared",
    to_id: "confluence:2",
    kind: "references",
    weight: null,
    confidence: 0.82,
    reasoning: "Beide beschreiben dieselbe Ladestrecke.",
    resolved: true,
    generated_by: "gemini:m/relation_extraction@v1",
    verified_by: null,
    verified_at: null,
    curated: false,
    created_at: "2026-03-01T12:00:00+00:00",
    ...over,
  };
}

function knoten(concept: Record<string, unknown>, hop: number): Record<string, unknown> {
  return {
    id: concept.id,
    store: concept.store,
    scope: concept.scope,
    type: concept.type,
    title: concept.title,
    status: "stable",
    hop,
    score: 1 - hop * 0.3,
    density: 0,
  };
}

/** Aufgezeichnete schreibende Aufrufe — die Grundlage der Zusicherungen. */
export interface Protokoll {
  writes: Array<{ method: string; url: string; body: unknown }>;
}

/**
 * Hängt die API an die Seite und gibt das Protokoll zurück.
 *
 * Der Zustand ist bewusst veränderlich: Ein Kernfluss aus §24 besteht darin, dass eine Aktion
 * *wirkt* — ein verschobenes Mitglied muss danach im anderen Cluster stehen. Eine unveränderliche
 * Nachbildung könnte das nicht zeigen.
 */
export async function mockApi(page: Page): Promise<Protokoll> {
  const protokoll: Protokoll = { writes: [] };
  const mitglieder = new Map<string, string[]>([
    ["cluster:a", ["confluence:1", "confluence:2"]],
    ["cluster:b", []],
  ]);
  const konzepte = new Map<string, Record<string, unknown>>([
    ["confluence:1", konzept()],
    ["confluence:2", MITGLIED_2],
    ["note:1", NOTIZ],
    ["cluster:a", CLUSTER_A],
    ["cluster:b", CLUSTER_B],
  ]);
  let bestaetigt = false;
  let laufStatus = "queued";

  async function json(route: Route, koerper: unknown, status = 200): Promise<void> {
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(koerper),
    });
  }

  await page.route("**/api/v1/**", async (route) => {
    const anfrage = route.request();
    const pfad = new URL(anfrage.url()).pathname;
    const suche = new URL(anfrage.url()).searchParams;
    const methode = anfrage.method();

    if (methode !== "GET") {
      protokoll.writes.push({
        method: methode,
        url: `${pfad}${suche.toString() ? `?${suche}` : ""}`,
        body: anfrage.postDataJSON?.() ?? null,
      });
    }

    // -- Betrieb --------------------------------------------------------------
    if (pfad.endsWith("/config/effective")) {
      return json(route, KONFIGURATION);
    }
    if (pfad.endsWith("/stats")) {
      return json(route, { stores: [] });
    }
    if (pfad.endsWith("/sources")) {
      return json(route, {
        items: [
          {
            name: "confluence-eng",
            adapter: "confluence",
            enabled: true,
            id_prefix: "confluence",
            scope: "engineering",
            usable: true,
            health: { state: "healthy", detail: "" },
            capabilities: {},
            last_run: null,
          },
        ],
      });
    }
    if (pfad.endsWith("/models")) {
      return json(route, { tasks: [], policies: {}, budget: {} });
    }
    if (pfad.endsWith("/models/usage")) {
      return json(route, { store: "shared", items: [] });
    }

    // -- Läufe ----------------------------------------------------------------
    if (pfad === "/api/v1/runs" && methode === "GET") {
      return json(route, { store: "shared", items: [] });
    }
    if (pfad.startsWith("/api/v1/runs/") && pfad.endsWith("/events")) {
      // Ein echter Server-Sent-Events-Strom: zuerst "läuft", dann "fertig".
      laufStatus = "succeeded";
      const lauf = {
        id: "run-1",
        kind: "sync",
        params: {},
        status: "running",
        started_at: "2026-03-01T12:00:00+00:00",
        finished_at: null,
        progress: 0.5,
        stats: { seen: 7 },
        error: null,
      };
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
        body:
          `event: progress\ndata: ${JSON.stringify(lauf)}\n\n` +
          `event: done\ndata: ${JSON.stringify({
            ...lauf,
            status: "succeeded",
            progress: 1,
            finished_at: "2026-03-01T12:01:00+00:00",
          })}\n\n`,
      });
    }
    if (pfad.startsWith("/api/v1/runs/") && methode === "POST") {
      return json(route, {
        id: "run-1",
        kind: "sync",
        params: {},
        status: laufStatus,
        started_at: null,
        finished_at: null,
        progress: 0,
        stats: {},
        error: null,
      });
    }

    // -- Cluster --------------------------------------------------------------
    if (pfad === "/api/v1/clusters" && methode === "GET") {
      return json(route, {
        store: "shared",
        items: [...mitglieder.keys()].map((id) => ({
          ...(konzepte.get(id) as Record<string, unknown>),
          member_count: mitglieder.get(id)?.length ?? 0,
          centroid_age_seconds: 120,
        })),
        next_cursor: null,
      });
    }
    if (pfad.startsWith("/api/v1/clusters/") && pfad.includes("/members") && methode !== "GET") {
      const teile = pfad.split("/");
      const clusterId = teile[4] ?? "";
      if (methode === "DELETE") {
        const conceptId = teile.slice(6).join("/");
        mitglieder.set(
          clusterId,
          (mitglieder.get(clusterId) ?? []).filter((id) => id !== conceptId),
        );
        return json(route, { entry: { id: 1, undoable: true }, concept: null, edge: null });
      }
      const koerper = anfrage.postDataJSON() as { concept_ids: string[] };
      mitglieder.set(clusterId, [...(mitglieder.get(clusterId) ?? []), ...koerper.concept_ids]);
      return json(route, { items: [] });
    }
    if (pfad.startsWith("/api/v1/clusters/") && methode === "GET") {
      const clusterId = pfad.slice("/api/v1/clusters/".length);
      const eigene = mitglieder.get(clusterId) ?? [];
      return json(route, {
        ...(konzepte.get(clusterId) as Record<string, unknown>),
        members: eigene.map((id) => konzepte.get(id)),
        related: [],
        centroid_age_seconds: 120,
        manual_title: false,
      });
    }

    // -- Kuration -------------------------------------------------------------
    if (pfad.endsWith("/curation/queue")) {
      return json(route, {
        store: "shared",
        items: bestaetigt
          ? []
          : [
              {
                kind: "unverified_edge",
                store: "shared",
                confidence: 0.82,
                edge: kante(),
                concepts: [konzept(), MITGLIED_2],
                entry: null,
              },
            ],
      });
    }
    if (pfad.includes("/edges/") && methode === "POST") {
      bestaetigt = true;
      return json(route, {
        entry: { id: 2, undoable: true },
        concept: null,
        edge: kante({ curated: true, verified_by: "user:token" }),
      });
    }
    if (pfad.endsWith("/curation/journal")) {
      return json(route, { store: "shared", items: [] });
    }

    // -- Graph ----------------------------------------------------------------
    if (pfad.startsWith("/api/v1/graph/neighbors/")) {
      const id = decodeURIComponent(pfad.slice("/api/v1/graph/neighbors/".length));
      if (id === "note:1") {
        return json(route, {
          start: [["personal", "note:1"]],
          nodes: [knoten(NOTIZ, 0), knoten(CLUSTER_A, 1)],
          edges: [
            kante({
              id: "brücke",
              from_store: "personal",
              from_id: "note:1",
              to_store: "shared",
              to_id: "cluster:a",
              generated_by: null,
              curated: true,
            }),
          ],
          hops: 1,
          truncated: false,
          queries: 2,
        });
      }
      if (id === "cluster:a") {
        return json(route, {
          start: [["shared", "cluster:a"]],
          nodes: [knoten(CLUSTER_A, 0), knoten(konzept(), 1), knoten(MITGLIED_2, 1)],
          edges: [
            kante({
              id: "m1",
              from_id: "cluster:a",
              to_id: "confluence:1",
              kind: "member",
              generated_by: "code:clustering",
            }),
            kante({
              id: "m2",
              from_id: "cluster:a",
              to_id: "confluence:2",
              kind: "member",
              generated_by: "code:clustering",
            }),
          ],
          hops: 1,
          truncated: false,
          queries: 1,
        });
      }
      return json(route, {
        start: [["shared", id]],
        nodes: [knoten(konzepte.get(id) ?? konzept(), 0)],
        edges: [],
        hops: 1,
        truncated: false,
        queries: 1,
      });
    }
    if (pfad.endsWith("/graph/map")) {
      // Der Vorgabemodus der Graph-Ansicht (§17.2 Ansicht 1). Ein Kartenknoten trägt `degree`
      // statt `score` — eine Karte hat keinen Ausgangspunkt, relativ zu dem ein Score entstünde.
      const karte = (eintrag: ReturnType<typeof konzept>, degree: number) => ({
        id: eintrag.id,
        store: eintrag.store,
        scope: eintrag.scope,
        type: eintrag.type,
        title: eintrag.title,
        status: eintrag.status,
        degree,
      });
      return json(route, {
        store: "shared",
        nodes: [karte(CLUSTER_A, 2), karte(konzept(), 1), karte(MITGLIED_2, 1)],
        edges: [
          kante({
            id: "m1",
            from_id: "cluster:a",
            to_id: "confluence:1",
            kind: "member",
            generated_by: "code:clustering",
          }),
          kante({
            id: "m2",
            from_id: "cluster:a",
            to_id: "confluence:2",
            kind: "member",
            generated_by: "code:clustering",
          }),
        ],
        edge_count: 2,
        next_cursor: null,
        truncated: false,
      });
    }
    if (pfad.endsWith("/graph/overview") || pfad.endsWith("/graph/loose")) {
      return json(route, { store: "shared", items: [], next_cursor: null });
    }
    if (pfad.endsWith("/graph/search")) {
      return json(route, {
        store: "shared",
        query: "warehouse",
        mode: "hybrid",
        hits: [knoten(CLUSTER_A, 0)],
      });
    }

    // -- Konzepte -------------------------------------------------------------
    if (pfad === "/api/v1/concepts" && methode === "GET") {
      const store = suche.get("store") ?? "shared";
      return json(route, {
        store,
        items: [...konzepte.values()].filter((eintrag) => eintrag.store === store),
        next_cursor: null,
      });
    }
    if (pfad.endsWith("/history")) {
      return json(route, { items: [] });
    }
    if (pfad.startsWith("/api/v1/concepts/") && methode === "GET") {
      const id = decodeURIComponent(pfad.slice("/api/v1/concepts/".length));
      const eintrag = konzepte.get(id) ?? konzept();
      return json(route, {
        ...eintrag,
        body: "Der vollständige Text.",
        content_hash: "abc",
        outgoing: [],
        incoming: [],
        clusters: [],
        locked_fields: eintrag.source_name ? ["title", "description", "body", "resource"] : [],
      });
    }

    return json(route, { title: "Nicht gefunden", detail: pfad }, 404);
  });

  return protokoll;
}
