/**
 * Der Lasttest der Zielmarke aus dem Konzept (Abschnitt 3.3): **5.000 Knoten mit laufender
 * Physik bedienbar** — Bildrate während der Simulation, Reaktion auf Zoom und Auswahl.
 *
 * Die API wird gemockt und liefert 5.000 synthetische Knoten mit ~7.500 Kanten; gemessen wird
 * die Zeichenfläche, nicht der Server. (Die echte API deckelt eine Kartenseite bei 2.000 —
 * das ist eine Nutzlast-Entscheidung, keine Grenze des Motors.)
 *
 * Läuft nur mit `WG_LASTTEST=1`: Er misst und gehört nicht in jeden Suite-Lauf.
 */

import { expect, test } from "@playwright/test";

import { KONFIGURATION } from "./fixtures";

test.skip(process.env.WG_LASTTEST !== "1", "nur zum Messen");

// Mit sichtbarem Fenster statt headless: Headless-Chromium zeichnet in Software (SwiftShader),
// und eine WebGL-Messung ohne GPU misst den Software-Rasterizer statt der Zeichenfläche.
test.use({ headless: false });

const KNOTEN = 5000;

function bestand(): { nodes: unknown[]; edges: unknown[] } {
  const nodes = [];
  const edges = [];
  for (let i = 0; i < KNOTEN; i += 1) {
    nodes.push({
      id: `synth:${i}`,
      store: "shared",
      scope: "engineering",
      type: i % 40 === 0 ? "Cluster" : "Confluence Page",
      title: `Synthetisches Konzept ${i}`,
      status: "stable",
      degree: 1 + (i % 5),
    });
  }
  // Cluster-Struktur plus Querverweise — dieselbe Mischung wie ein echter Bestand.
  for (let i = 0; i < KNOTEN; i += 1) {
    if (i % 40 !== 0) {
      edges.push(kante(`m${i}`, `synth:${40 * Math.floor(i / 40)}`, `synth:${i}`, "member"));
    }
    if (i % 2 === 0) {
      edges.push(
        kante(`r${i}`, `synth:${i}`, `synth:${(i * 37 + 11) % KNOTEN}`, "references"),
      );
    }
  }
  return { nodes, edges };
}

function kante(id: string, von: string, nach: string, kind: string): Record<string, unknown> {
  return {
    id,
    from_store: "shared",
    from_id: von,
    to_store: "shared",
    to_id: nach,
    kind,
    weight: null,
    confidence: null,
    reasoning: null,
    resolved: true,
    generated_by: kind === "member" ? "code:clustering@v1" : null,
    verified_by: null,
    verified_at: null,
    curated: kind !== "member",
    created_at: "2026-03-01T12:00:00+00:00",
  };
}

test("5000 Knoten: Physik läuft, Bedienung bleibt flüssig", async ({ page }) => {
  const daten = bestand();
  await page.route("**/api/v1/**", async (route) => {
    const pfad = new URL(route.request().url()).pathname;
    if (pfad.endsWith("/config/effective")) {
      return route.fulfill({ json: KONFIGURATION });
    }
    if (pfad.endsWith("/graph/map")) {
      return route.fulfill({
        json: {
          store: "shared",
          nodes: daten.nodes,
          edges: daten.edges,
          edge_count: daten.edges.length,
          next_cursor: null,
          truncated: false,
        },
      });
    }
    if (pfad.endsWith("/curation/queue")) {
      return route.fulfill({ json: { store: "shared", items: [] } });
    }
    if (pfad.endsWith("/clusters")) {
      return route.fulfill({ json: { store: "shared", items: [], next_cursor: null } });
    }
    return route.fulfill({ json: { items: [] } });
  });
  await page.addInitScript(() => window.sessionStorage.setItem("wg.token", "test"));

  await page.goto("/?view=graph&store=shared");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText(`${KNOTEN} Knoten`, { exact: false })).toBeVisible({
    timeout: 30_000,
  });

  // 1) Bildrate, während die Simulation läuft (sie läuft nach dem Laden mehrere Sekunden).
  const fps = await page.evaluate(
    () =>
      new Promise<number>((fertig) => {
        let bilder = 0;
        const start = performance.now();
        const zaehlen = (): void => {
          bilder += 1;
          if (performance.now() - start < 2000) {
            requestAnimationFrame(zaehlen);
          } else {
            fertig(Math.round((bilder * 1000) / (performance.now() - start)));
          }
        };
        requestAnimationFrame(zaehlen);
      }),
  );

  // 2) Zoom: Wie lange braucht ein Radschritt, bis das nächste Bild steht?
  const zoomMs = await page.evaluate(
    () =>
      new Promise<number>((fertig) => {
        const flaeche = document.querySelector('[data-testid="graph-canvas"] canvas');
        const start = performance.now();
        flaeche?.dispatchEvent(
          new WheelEvent("wheel", { deltaY: -120, bubbles: true, cancelable: true }),
        );
        requestAnimationFrame(() => fertig(Math.round(performance.now() - start)));
      }),
  );

  console.log(`[lasttest] ${KNOTEN} Knoten / ${daten.edges.length} Kanten`);
  console.log(`[lasttest] Bildrate bei laufender Simulation: ${fps} fps`);
  console.log(`[lasttest] Zoomschritt bis zum nächsten Bild: ${zoomMs} ms`);

  expect(fps).toBeGreaterThanOrEqual(30);
  expect(zoomMs).toBeLessThan(100);
});
