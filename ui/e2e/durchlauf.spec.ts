/**
 * Ein Durchlauf durch die ganze Oberfläche — sichtbar, langsam, mit großen Datenmengen.
 *
 * Kein Abnahmetest, sondern eine Vorführung zum Zusehen: Er fährt alle elf Ansichten der drei
 * Arbeitsbereiche ab, hält an jeder Station kurz an und prüft dabei jeweils die eine Aussage,
 * auf die es dort ankommt. Die Mengen sind bewusst groß — 5.000 Graphknoten, 400 Dokumente,
 * 180 Cluster, 120 offene Kurationsposten —, damit nicht die halbleere Testfassung vorgeführt
 * wird, sondern das, was die Oberfläche im Betrieb aushalten muss.
 *
 * Die API ist nachgebildet und nicht der laufende Stack: Nur so lassen sich in *allen*
 * Ansichten große Mengen zeigen, und der echte Bestand wird dabei nicht angefasst. Echt sind
 * der Browser, das gebaute Bündel, WebGL, die Physik und jede Interaktion.
 *
 * Läuft nur mit ``WG_DURCHLAUF=1``, mit sichtbarem Fenster:
 *
 *     WG_DURCHLAUF=1 npx playwright test e2e/durchlauf.spec.ts
 */

import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.WG_DURCHLAUF !== "1", "Vorführung; nur auf Anforderung");

// Sichtbar, entspannt und groß genug, dass die Panels nebeneinander Platz haben.
test.use({
  headless: false,
  viewport: { width: 1600, height: 950 },
  launchOptions: { slowMo: 120 },
});

const KNOTEN = 5000;
const DOKUMENTE = 400;
const CLUSTER = 180;
const KURATION = 120;

/** Zeit zum Hinsehen. */
async function zeigen(page: Page, was: string, ms = 2600): Promise<void> {
  console.log(`   → ${was}`);
  await page.waitForTimeout(ms);
}

function konzept(id: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id,
    store: "shared",
    scope: "engineering",
    type: "Confluence Page",
    title: `Dokument ${id.split(":")[1]}`,
    description: "Kurz beschrieben.",
    resource: "https://confluence.example/x",
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

function kante(id: string, von: string, nach: string, kind: string): Record<string, unknown> {
  const ausModell = kind !== "member";
  return {
    id,
    from_store: "shared",
    from_id: von,
    to_store: "shared",
    to_id: nach,
    kind,
    weight: null,
    confidence: 0.82,
    reasoning: "Beide beschreiben dieselbe Ladestrecke.",
    resolved: true,
    generated_by: ausModell ? "gemini:m/relation_extraction@v1" : "code:clustering@v1",
    verified_by: null,
    verified_at: null,
    curated: false,
    created_at: "2026-03-01T12:00:00+00:00",
  };
}

/**
 * Ein absichtlich **ungleichmäßiger** Bestand.
 *
 * Die erste Fassung war zu brav: gleich große Cluster, jeder Knoten verbunden, überall
 * dieselbe Dichte. So sieht kein gewachsener Wissensbestand aus, und ein Layout, das damit
 * gut aussieht, beweist wenig. Hier gibt es stattdessen:
 *
 * * **Cluster sehr verschiedener Größe** — von drei Mitgliedern bis über zweihundert.
 * * **Verschiedene Dichte je Cluster**: manche innen stark quervernetzt, andere nur ein Stern
 *   um ihre Mitte.
 * * **Einzelgänger** — rund ein Sechstel der Knoten hat gar keine Kante (die „losen" aus §15).
 * * **Inseln** aus zwei bis fünf Knoten, die untereinander hängen und sonst mit nichts.
 * * **Ein paar Naben** mit auffällig vielen Verbindungen quer über den Bestand.
 *
 * Deterministisch bleibt es trotzdem: Der Zufallsgenerator ist gesetzt, damit zwei Läufe
 * dasselbe Bild ergeben und ein Unterschied etwas bedeutet.
 */
function bestand() {
  // Ein winziger, gesetzter Generator — `Math.random()` wäre bei jedem Lauf ein anderes Bild.
  let saat = 42;
  const zufall = (): number => {
    saat = (saat * 1664525 + 1013904223) % 4294967296;
    return saat / 4294967296;
  };
  const streuen = <T,>(werte: readonly T[]): T => werte[Math.floor(zufall() * werte.length)] as T;

  const karte = { nodes: [] as unknown[], edges: [] as unknown[] };
  const grad = new Map<number, number>();
  const merken = (i: number) => grad.set(i, (grad.get(i) ?? 0) + 1);

  // 1. Cluster verschiedener Größe, solange der Vorrat reicht.
  const gruppen: Array<{ mitte: number; mitglieder: number[] }> = [];
  let naechster = 0;
  const einzelgaenger = Math.floor(KNOTEN * 0.16);
  const fuerGruppen = KNOTEN - einzelgaenger;
  while (naechster < fuerGruppen - 1) {
    // Stark ungleiche Größen: viele kleine, wenige sehr große.
    const roh = Math.pow(zufall(), 2.2);
    const groesse = Math.max(3, Math.min(fuerGruppen - naechster - 1, Math.round(3 + roh * 220)));
    const mitte = naechster;
    const mitglieder = Array.from({ length: groesse - 1 }, (_, k) => mitte + 1 + k);
    gruppen.push({ mitte, mitglieder });
    naechster += groesse;
  }

  for (let i = 0; i < KNOTEN; i += 1) {
    const istMitte = gruppen.some((g) => g.mitte === i);
    karte.nodes.push({
      id: `synth:${i}`,
      store: zufall() < 0.06 ? "personal" : "shared",
      scope: "engineering",
      type: istMitte
        ? "Cluster"
        : streuen(["Confluence Page", "Confluence Page", "Confluence Page", "Jira Issue"]),
      title: istMitte ? `Thema ${i}` : `Konzept ${i}`,
      status: zufall() < 0.012 ? "tombstone" : streuen(["stable", "stable", "stable", "draft"]),
      degree: 0,
    });
  }

  for (const gruppe of gruppen) {
    for (const mitglied of gruppe.mitglieder) {
      karte.edges.push(kante(`m${mitglied}`, `synth:${gruppe.mitte}`, `synth:${mitglied}`, "member"));
      merken(gruppe.mitte);
      merken(mitglied);
    }
    // Die innere Dichte schwankt stark: von „nur ein Stern" bis „alles quervernetzt".
    const dichte = zufall() * zufall();
    const quer = Math.round(gruppe.mitglieder.length * dichte * 1.5);
    for (let k = 0; k < quer; k += 1) {
      const a = streuen(gruppe.mitglieder);
      const b = streuen(gruppe.mitglieder);
      if (a !== b) {
        karte.edges.push(kante(`q${gruppe.mitte}-${k}`, `synth:${a}`, `synth:${b}`, "references"));
        merken(a);
        merken(b);
      }
    }
  }

  // 2. Ein paar Brücken zwischen Themen — sonst zerfällt das Bild in lauter Inseln.
  for (let k = 0; k < gruppen.length; k += 1) {
    if (zufall() < 0.45 && gruppen.length > 1) {
      const von = streuen(gruppen).mitte;
      const nach = streuen(gruppen).mitte;
      if (von !== nach) {
        karte.edges.push(kante(`b${k}`, `synth:${von}`, `synth:${nach}`, "related"));
        merken(von);
        merken(nach);
      }
    }
  }

  // 3. Naben: wenige Knoten mit auffällig vielen Verbindungen quer durch den Bestand.
  //    Bewusst sparsam. Naben gibt es in echten Beständen (das „Onboarding"-Dokument, auf das
  //    alles zeigt), aber jede von ihnen zieht den halben Graphen in die Mitte — mit sechs
  //    kräftigen Naben sah die Karte aus wie ein einziger Knäuel, und die Themen darin waren
  //    nicht mehr zu unterscheiden.
  for (let n = 0; n < 3; n += 1) {
    const nabe = Math.floor(zufall() * fuerGruppen);
    for (let k = 0; k < 15 + Math.floor(zufall() * 25); k += 1) {
      const ziel = Math.floor(zufall() * fuerGruppen);
      if (ziel !== nabe) {
        karte.edges.push(kante(`h${n}-${k}`, `synth:${nabe}`, `synth:${ziel}`, "references"));
        merken(nabe);
        merken(ziel);
      }
    }
  }

  // 4. Kleine Inseln am Rand: zwei bis fünf Knoten, die nur untereinander hängen.
  let insel = fuerGruppen;
  while (insel < KNOTEN - 3) {
    const groesse = 2 + Math.floor(zufall() * 4);
    if (zufall() < 0.35) {
      for (let k = 1; k < groesse && insel + k < KNOTEN; k += 1) {
        karte.edges.push(kante(`i${insel}-${k}`, `synth:${insel}`, `synth:${insel + k}`, "references"));
        merken(insel);
        merken(insel + k);
      }
    }
    insel += groesse;
  }
  // Der Rest bleibt, was er ist: lose Knoten ohne jede Kante.

  const hoechster = Math.max(1, ...grad.values());
  for (const [i, wert] of grad) {
    (karte.nodes[i] as { degree: number }).degree = Math.max(1, Math.round((wert / hoechster) * 12));
  }

  const dokumente = Array.from({ length: DOKUMENTE }, (_, i) =>
    konzept(`confluence:${i}`, {
      title: `Faktentabellen laden — Teil ${i}`,
      type: i % 3 === 0 ? "Jira Issue" : "Confluence Page",
      status: ["stable", "draft", "deprecated"][i % 3],
    }),
  );

  const cluster = Array.from({ length: CLUSTER }, (_, i) => ({
    ...konzept(`cluster:${i}`, {
      title: i % 4 === 0 ? `Warehouse ${i}` : `Automatisch benannt ${i}`,
      type: "Cluster",
      curated: i % 4 === 0,
    }),
    member_count: 3 + (i % 25),
    centroid_age_seconds: 120 + i,
  }));

  const warteschlange = Array.from({ length: KURATION }, (_, i) => ({
    kind: "unverified_edge",
    store: "shared",
    confidence: 0.95 - i * 0.004,
    edge: kante(`k${i}`, `confluence:${i}`, `confluence:${i + 1}`, "references"),
    concepts: [konzept(`confluence:${i}`), konzept(`confluence:${i + 1}`)],
    entry: null,
  }));

  return { karte, dokumente, cluster, warteschlange };
}

const KONFIGURATION = {
  env: "vorfuehrung",
  scopes: [
    { name: "engineering", store: "shared", description: null },
    { name: "personal", store: "personal", description: null },
  ],
  concept_types: [
    { name: "Confluence Page", stores: ["shared"], source_mirrored: true },
    { name: "Jira Issue", stores: ["shared"], source_mirrored: true },
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

/** Hängt die nachgebildete API mit dem großen Bestand an die Seite. */
async function mitBestand(page: Page): Promise<void> {
  const daten = bestand();
  let laufNummer = 0;

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const pfad = url.pathname;
    const json = (koerper: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(koerper) });

    if (pfad.endsWith("/config/effective")) return json(KONFIGURATION);
    if (pfad.endsWith("/graph/map"))
      return json({
        store: "shared",
        nodes: daten.karte.nodes,
        edges: daten.karte.edges,
        edge_count: daten.karte.edges.length,
        next_cursor: null,
        truncated: true,
      });
    if (pfad.includes("/graph/neighbors/"))
      return json({
        start: [["shared", "synth:40"]],
        nodes: daten.karte.nodes.slice(40, 92).map((k) => ({ ...(k as object), hop: 1, score: 0.7, density: 0.4 })),
        edges: daten.karte.edges.slice(0, 60),
        hops: 1,
        truncated: false,
        queries: 1,
      });
    if (pfad.endsWith("/graph/search"))
      return json({ store: "shared", query: "warehouse", mode: "two_stage", hits: daten.karte.nodes.slice(0, 8).map((k) => ({ ...(k as object), hop: 0, score: 0.9, density: 0 })) });
    if (pfad.endsWith("/concepts")) return json({ store: "shared", items: daten.dokumente, next_cursor: "confluence:400" });
    if (pfad.endsWith("/history")) return json({ items: [{ id: 1, change_type: "created", actor: "sync:confluence-eng", changed_at: "2026-03-01T12:00:00+00:00", undoable: false }] });
    if (pfad.startsWith("/api/v1/concepts/"))
      return json({
        ...konzept(decodeURIComponent(pfad.slice("/api/v1/concepts/".length))),
        body: "# Ladestrecke\n\nDie nächtliche Verarbeitung läuft in **drei Stufen**:\n\n- Extraktion\n- Transformation\n- Laden\n\nDetails im `runbook`.",
        content_hash: "abc",
        outgoing: daten.karte.edges.slice(0, 6),
        incoming: daten.karte.edges.slice(6, 10),
        clusters: [{ id: "cluster:0", title: "Warehouse 0" }],
        locked_fields: ["description", "body"],
      });
    if (pfad.endsWith("/clusters")) return json({ store: "shared", items: daten.cluster, next_cursor: null });
    if (pfad.startsWith("/api/v1/clusters/"))
      return json({
        ...daten.cluster[0],
        members: daten.dokumente.slice(0, 18),
        related: daten.cluster.slice(1, 6).map((c, i) => ({ id: c.id, title: c.title, similarity: 0.9 - i * 0.07 })),
        centroid_age_seconds: 120,
        manual_title: false,
      });
    if (pfad.endsWith("/curation/queue")) return json({ store: "shared", items: daten.warteschlange });
    if (pfad.endsWith("/stats"))
      return json({
        stores: [
          { store: "shared", concepts: 4820, edges: 11402, clusters: CLUSTER, loose: 1288, by_scope: {}, by_type: {}, by_status: {} },
          { store: "personal", concepts: 180, edges: 240, clusters: 12, loose: 9, by_scope: {}, by_type: {}, by_status: {} },
        ],
      });
    if (pfad.endsWith("/sources"))
      return json({
        items: ["confluence-eng", "jira-platform", "confluence-ops"].map((name, i) => ({
          name,
          adapter: name.split("-")[0],
          enabled: true,
          id_prefix: name.split("-")[0],
          scope: "engineering",
          usable: i !== 2,
          health: { state: i === 2 ? "degraded" : "healthy", detail: "" },
          capabilities: {},
          last_run: i === 0 ? { id: "r1", kind: "sync", status: "succeeded", started_at: "2026-09-01T08:00:00+00:00", progress: 1, stats: {}, error: null, params: {} } : null,
        })),
      });
    if (pfad.endsWith("/models"))
      return json({
        tasks: [
          { task: "embedding", provider: "gemini", model: "text-embedding-004", model_key: "gemini:text-embedding-004", local: false, dim: 768, temperature: null, configured: true, fallbacks: [], endpoint: null, generated_by: "x" },
          { task: "relation_extraction", provider: "gemini", model: "gemini-2.0-flash", model_key: "gemini:gemini-2.0-flash", local: false, dim: null, temperature: 0.2, configured: true, fallbacks: [], endpoint: null, generated_by: "x" },
          { task: "cluster_labeling", provider: "ollama", model: "llama3", model_key: "ollama:llama3", local: true, dim: null, temperature: 0.3, configured: false, fallbacks: [], endpoint: null, generated_by: "x" },
        ],
        policies: {},
        budget: {},
      });
    if (pfad.endsWith("/models/usage"))
      return json({
        store: "shared",
        items: [
          { task: "embedding", provider: "gemini", model: "text-embedding-004", calls: 4820, tokens_in: 1_240_500, tokens_out: 0, cost_estimate_eur: 0.1861, cache_hits: 312, errors: 0 },
          { task: "relation_extraction", provider: "gemini", model: "gemini-2.0-flash", calls: 1180, tokens_in: 890_200, tokens_out: 47_300, cost_estimate_eur: 0.4127, cache_hits: 96, errors: 3 },
        ],
      });
    if (pfad.endsWith("/doctor"))
      return json({
        healthy: false,
        checks: [
          { name: "konfiguration", status: "ok", detail: "gültig geladen", context: {} },
          { name: "stores", status: "ok", detail: "shared und personal erreichbar", context: {} },
          { name: "schema", status: "ok", detail: "beide Stores auf head", context: {} },
          { name: "store_trennung", status: "ok", detail: "personal ohne Ausgang", context: {} },
          { name: "provider:gemini", status: "ok", detail: "Schlüssel hinterlegt", context: {} },
          { name: "provider:ollama", status: "fail", detail: "nicht erreichbar unter http://ollama:11434", context: {} },
          { name: "adapter:confluence-ops", status: "warn", detail: "Health degraded", context: {} },
          { name: "broker", status: "ok", detail: "Redis antwortet", context: {} },
        ],
      });
    if (pfad === "/api/v1/runs")
      return json({
        store: "shared",
        items: Array.from({ length: 12 }, (_, i) => ({
          id: `run-${i}`,
          kind: ["sync", "embed", "cluster", "relations", "link_orphans"][i % 5],
          params: { dry_run: i % 4 === 0 },
          status: ["succeeded", "succeeded", "failed", "running"][i % 4],
          started_at: `2026-09-0${(i % 8) + 1}T09:${String(i * 5).padStart(2, "0")}:00+00:00`,
          finished_at: null,
          progress: i % 4 === 3 ? 0.45 : 1,
          stats: { seen: 120 * i },
          error: i % 4 === 2 ? "Provider antwortete nicht" : null,
        })),
      });
    if (pfad.includes("/events")) {
      // Ein echter SSE-Strom: erst "läuft", dann "fertig" mit Statistik.
      const lauf = { id: `run-neu-${laufNummer}`, kind: "cluster", params: { dry_run: true }, status: "running", started_at: "2026-09-01T10:00:00+00:00", finished_at: null, progress: 0.4, stats: {}, error: null };
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
        body:
          `event: progress\ndata: ${JSON.stringify(lauf)}\n\n` +
          `event: done\ndata: ${JSON.stringify({ ...lauf, status: "succeeded", progress: 1, stats: { clusters_created: 23, clusters_matched: 157, members_added: 1841 } })}\n\n`,
      });
    }
    if (route.request().method() === "POST" && pfad.includes("/runs/")) {
      laufNummer += 1;
      return json({ id: `run-neu-${laufNummer}`, kind: "cluster", params: {}, status: "queued", started_at: null, finished_at: null, progress: 0, stats: {}, error: null });
    }
    return json({ items: [] });
  });

  await page.addInitScript(() => window.sessionStorage.setItem("wg.token", "vorfuehrung"));
}

/**
 * Nur das Kartenbild, für die Beurteilung von Abstand und Kantendichte.
 *
 * Er läuft schnell und ohne Fenster: Wer an der Spreizung schraubt, will das Ergebnis sehen,
 * ohne jedes Mal den ganzen Durchlauf abzuwarten.
 */
test("Kartenbild mit großem, ungleichmäßigem Bestand", async ({ page }) => {
  test.setTimeout(180_000);
  await mitBestand(page);

  await page.goto("/?view=graph&store=shared");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText(`${KNOTEN} Knoten`, { exact: false })).toBeVisible({ timeout: 60_000 });
  // Einschwingen abwarten — die Simulation hält nach `einschwingzeitMs` von selbst an.
  await page.waitForTimeout(11_000);
  await page.screenshot({ path: "test-results/kartenbild-gross.png" });

  await page.getByRole("button", { name: "Alles zeigen" }).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: "test-results/kartenbild-eingepasst.png" });
});

test("Durchlauf durch alle Ansichten mit großem Bestand", async ({ page }) => {
  test.setTimeout(600_000);
  await mitBestand(page);

  // ---------------------------------------------------------------- ERKUNDEN
  console.log(`\n== ERKUNDEN ==  ${KNOTEN} Knoten`);
  await page.goto("/?view=graph&store=shared");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText(`${KNOTEN} Knoten`, { exact: false })).toBeVisible({ timeout: 60_000 });
  await zeigen(page, "Karte: 5.000 Knoten — Themen verschiedener Größe, Inseln, lose Knoten", 6500);

  await page.mouse.move(800, 500);
  for (let i = 0; i < 4; i += 1) await page.mouse.wheel(0, -240);
  await zeigen(page, "Hineinzoomen — Beschriftungen erscheinen erst, wenn sie lesbar sind", 3000);
  for (let i = 0; i < 4; i += 1) await page.mouse.wheel(0, 240);

  await page.getByRole("button", { name: "konzentrisch" }).click();
  await zeigen(page, "Layout konzentrisch: schwere Knoten nach innen");
  await page.getByRole("button", { name: "hierarchisch" }).click();
  await zeigen(page, "Layout hierarchisch: Ebenen entlang der member-Kanten");
  await page.getByRole("button", { name: "Physik" }).click();
  await zeigen(page, "zurück zur Live-Physik", 3200);

  await page.getByRole("button", { name: "Regler" }).click();
  const regler = page.getByLabel("Kantenlänge");
  await regler.fill("260");
  await zeigen(page, "Regler: Kantenlänge hochgedreht, der Graph spreizt sich", 3200);
  await regler.fill("110");
  await page.getByRole("button", { name: "Regler" }).click();

  await page.getByRole("button", { name: "Traversierung" }).click();
  await zeigen(page, "Betriebsart Traversierung");
  await page.goto("/?view=graph&mode=reise&store=shared&id=synth:40");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await zeigen(page, "Traversierung von einem Knoten aus, Inspektor rechts", 3200);

  console.log("\n== ERKUNDEN: Suche ==");
  await page.keyboard.press("/");
  await page.keyboard.type("warehouse", { delay: 90 });
  await page.keyboard.press("Enter");
  await expect(page.getByRole("listbox", { name: "Suchtreffer" })).toBeVisible();
  await zeigen(page, "Globale Suche: zweistufig, Treffer lesen oder im Graphen öffnen", 3200);
  await page.keyboard.press("Escape");

  await page.goto("/?view=browser&store=shared&id=confluence:7");
  await expect(page.getByRole("complementary", { name: "Details" })).toBeVisible();
  await zeigen(page, `Dokumente: ${DOKUMENTE} Zeilen, Inspektor mit gerendertem Markdown`, 4000);
  await expect(page.getByTestId("gesperrt-Fließtext")).toBeVisible();
  await zeigen(page, "gespiegelte Felder sichtbar gesperrt (§17.3)", 2200);

  const griff = page.getByRole("separator", { name: "Inspektorbreite" });
  await griff.hover();
  await page.mouse.down();
  await page.mouse.move(1050, 500, { steps: 20 });
  await page.mouse.up();
  await zeigen(page, "Inspektor breiter gezogen", 2200);
  await page.getByRole("button", { name: "Inspektor einklappen" }).click();
  await zeigen(page, "Inspektor eingeklappt", 1800);
  await page.getByRole("button", { name: "Inspektor öffnen" }).click();

  await page.goto("/?view=persoenlich&store=personal");
  await zeigen(page, "Persönlicher Bereich — angeschrieben: verlässt diesen Rechner nicht", 3000);

  // ------------------------------------------------------------- ANALYSIEREN
  console.log("\n== ANALYSIEREN ==");
  await page.goto("/?view=kuration&store=shared");
  await expect(page.getByRole("listitem").first()).toBeVisible();
  await zeigen(page, `Kuration: ${KURATION} offene Posten, nach Confidence sortiert`, 3000);
  await page.keyboard.press("j");
  await page.keyboard.press("j");
  await zeigen(page, "Tastaturbedienung: j/k bewegen durch den Stapel", 2600);

  await page.goto("/?view=cluster&store=shared&cluster=cluster:0");
  await expect(page.getByLabel("Neuer Titel")).toBeVisible();
  await zeigen(page, `Cluster-Arbeitsplatz: ${CLUSTER} Cluster, Mitglieder per Zug verschiebbar`, 3600);

  await page.goto("/?view=automatisierung&store=shared");
  await zeigen(page, "Automatisierung: Formulare mit Vorbelegung aus der Konfiguration", 3000);
  const clusterKarte = page.getByRole("region", { name: "Clustering" });
  await clusterKarte.getByRole("button", { name: "Probelauf" }).click();
  await expect(page.getByTestId("fortschritt")).toBeVisible();
  await zeigen(page, "Probelauf läuft — Fortschritt über Server-Sent Events", 3000);
  await expect(page.getByRole("button", { name: /scharf ausführen/ })).toBeVisible();
  await zeigen(page, "Vorschau da: 23 Cluster entstünden neu — erst jetzt gibt es den scharfen Knopf", 4200);

  await page.goto("/?view=qualitaet&store=shared");
  await expect(page.getByText("26.7 %")).toBeVisible();
  await zeigen(page, "Qualität: 1.288 von 4.820 lose — über 20 %, deshalb rot", 3600);

  // ---------------------------------------------------------------- VERWALTEN
  console.log("\n== VERWALTEN ==");
  await page.goto("/?view=quellen&store=shared");
  await expect(page.getByText("confluence-eng")).toBeVisible();
  await zeigen(page, "Quellen: drei Quellen, eine degraded (roter Punkt)", 3000);
  await page.getByLabel(/Trockenlauf/).check();
  await page.getByRole("button", { name: "Sync", exact: true }).first().click();
  await expect(page.getByTestId("fortschritt")).toBeVisible();
  await zeigen(page, "Sync als Trockenlauf gestartet, Fortschritt live", 3000);

  await page.goto("/?view=laeufe&store=shared");
  await zeigen(page, "Läufe: Historie mit Probelauf-Kennzeichnung und Fehlern", 3400);

  await page.goto("/?view=modelle&store=shared");
  await zeigen(page, "Modelle & Kosten: 6.000 Aufrufe, Kostenschätzung, fehlender Schlüssel", 3600);

  await page.goto("/?view=diagnose&store=shared");
  await page.getByRole("button", { name: "Diagnose ausführen" }).click();
  await expect(page.getByText(/Mindestens eine Prüfung ist fehlgeschlagen/)).toBeVisible();
  await zeigen(page, "Diagnose: acht Prüfungen mit Ampel, ollama rot", 4200);

  await page.getByRole("button", { name: "Navigation breit" }).click();
  await zeigen(page, "Navigation schmal — der Kurationszähler bleibt sichtbar", 2600);
  await page.getByRole("button", { name: "Navigation breit" }).click();

  console.log("\n== fertig ==\n");
  await zeigen(page, "Durchlauf beendet", 2500);
});
