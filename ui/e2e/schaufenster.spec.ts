/**
 * Ein Schaufenster-Lauf: Screenshots des Gerüsts für die Durchsicht — kein Abnahmetest.
 *
 * Läuft nur, wenn `WG_SCHAUFENSTER=1` gesetzt ist, damit die reguläre Suite ihn nicht mitzählt.
 */

import { expect, test } from "@playwright/test";

import { mockApi } from "./fixtures";

test.skip(process.env.WG_SCHAUFENSTER !== "1", "nur für die Durchsicht");

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => window.sessionStorage.setItem("wg.token", "test"));
});

test("Gerüst: Karte mit offenem Inspektor", async ({ page }) => {
  await page.goto("/?view=graph&store=shared&id=confluence:1");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: "test-results/schaufenster-graph.png", fullPage: false });
});

test("Erkunden: Suche offen, Persönlich mit Inspektor", async ({ page }) => {
  await page.goto("/?view=persoenlich&store=personal&id=note:1");
  await page.keyboard.press("/");
  await page.keyboard.type("Warehouse");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);
  await page.screenshot({ path: "test-results/schaufenster-erkunden.png", fullPage: false });
});

test("Gerüst: Dokumente, Inspektor eingeklappt, Rail schmal", async ({ page }) => {
  await page.goto("/?view=browser&store=shared");
  await page.getByRole("button", { name: "Inspektor einklappen" }).click();
  await page.getByRole("button", { name: "Navigation breit" }).click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: "test-results/schaufenster-browser.png", fullPage: false });
});
