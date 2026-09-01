/**
 * Bilder vom laufenden Stand — die echte Oberfläche mit dem echten Bestand.
 *
 * Kein Abnahmetest und keine Nachbildung: Dieser Lauf meldet sich am laufenden Container an
 * und fotografiert, was gerade da ist. Er schreibt nichts; jede Zusicherung darin ist nur eine
 * Wartebedingung, damit kein Bild einen halb geladenen Zustand zeigt.
 *
 *     WG_API_TOKEN=… npx playwright test --config playwright.live.config.ts
 */

import { expect, test, type Page } from "@playwright/test";

const TOKEN = process.env.WG_API_TOKEN ?? "";

test.beforeEach(async ({ page }) => {
  // Die SPA holt ihr Token aus der Sitzung — dieselbe Stelle, an der es nach der Eingabe im
  // Anmeldefeld landet (§17.1).
  await page.addInitScript((wert) => window.sessionStorage.setItem("wg.token", wert), TOKEN);
});

async function bild(page: Page, name: string, was: string): Promise<void> {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `test-results/live-${name}.png` });
  console.log(`   → ${was}`);
}

test("Der laufende Stand in Bildern", async ({ page }) => {
  test.setTimeout(300_000);

  console.log("\n== ERKUNDEN ==");
  await page.goto("/?view=graph&store=shared");
  await expect(page.getByTestId("graph-canvas")).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(9000); // die Physik einschwingen lassen
  await bild(page, "graph-karte", "Karte über den echten Bestand");

  await page.goto("/?view=browser&store=shared");
  await expect(page.getByRole("table")).toBeVisible({ timeout: 30_000 });
  await bild(page, "dokumente", "Dokumente: die synchronisierte SAP-Doku");

  // Ein Dokument mit Inhalt öffnen — der Inspektor zeigt gerendertes Markdown.
  const ersteZeile = page.getByRole("row").nth(1).getByRole("button").first();
  if (await ersteZeile.count()) {
    await ersteZeile.click();
    await expect(page.getByRole("complementary", { name: "Details" })).toBeVisible();
    await bild(page, "dokument-inspektor", "Inspektor mit gerendertem Fließtext");
  }

  console.log("\n== ANALYSIEREN ==");
  await page.goto("/?view=cluster&store=shared");
  await page.waitForTimeout(2500);
  await bild(page, "cluster", "Cluster-Arbeitsplatz mit den erzeugten Themen");

  await page.goto("/?view=qualitaet&store=shared");
  await page.waitForTimeout(2500);
  await bild(page, "qualitaet", "Qualität: die echten Bestandszahlen");

  await page.goto("/?view=kuration&store=shared");
  await page.waitForTimeout(2500);
  await bild(page, "kuration", "Kuration: die offenen Modellvorschläge");

  console.log("\n== VERWALTEN ==");
  await page.goto("/?view=laeufe&store=shared");
  await page.waitForTimeout(2000);
  await bild(page, "laeufe", "Läufe: die Historie dieses Abends");

  await page.goto("/?view=modelle&store=shared");
  await page.waitForTimeout(2000);
  await bild(page, "modelle", "Modelle & Kosten: der tatsächliche Verbrauch");

  console.log("");
});
