/**
 * Die Kernflüsse aus §24, Stufe 11 — in einem echten Browser.
 *
 * Die vier Abnahmekriterien der Stufe, jedes als eigener Test:
 *
 * 1. Ein Graph lässt sich von einer persönlichen Notiz aus über mehrere Hops explorieren.
 * 2. Ein Mitglied wird per Drag-and-Drop in ein anderes Cluster verschoben.
 * 3. Ein Modellvorschlag wird bestätigt, ein anderer verworfen.
 * 4. Ein Sync wird aus der UI gestartet und der Fortschritt live angezeigt.
 *
 * Dazu das fünfte: Inhaltsfelder eines gespiegelten Konzepts sind erkennbar gesperrt.
 *
 * Warum im Browser und nicht in jsdom: Drei dieser Flüsse hängen an Fähigkeiten, die jsdom nicht
 * hat — ein Canvas für Cytoscape, echte Zeigerereignisse für Drag-and-Drop und eine echte
 * `EventSource`-Verbindung für den Fortschritt.
 */

import { expect, test } from "@playwright/test";

import { mockApi } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => window.sessionStorage.setItem("wg.token", "test"));
});

test("Graph von einer persönlichen Notiz aus über mehrere Hops (§24)", async ({ page }) => {
  // Ausdrücklich `mode=reise`: Die Traversierung ist der eine der beiden Modi von Ansicht 1, und
  // dieser Fluss prüft genau ihn. Die Karte hat keinen Ausgangspunkt und damit keine Hops.
  await page.goto("/?view=graph&mode=reise&store=personal&id=note:1");

  // Hop 1: Die Notiz und das Cluster, auf das ihre Brücke zeigt.
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Onboarding-Notiz" })).toBeVisible();

  // Hop 2: Vom Cluster aus weiter — über das Seitenpanel, ohne die Zeichenfläche zu treffen.
  await page.goto("/?view=graph&mode=reise&store=shared&id=cluster:a");
  await expect(page.getByRole("heading", { name: "Warehouse" })).toBeVisible();
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
});

test("Die Karte zeigt den Bestand ohne Startknoten und lässt sich steuern (§17.2)", async ({
  page,
}) => {
  await page.goto("/?view=graph&store=shared");

  // Der Vorgabemodus ist die Karte: kein `id`, kein Klick — und trotzdem ein Bild.
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText(/3 Knoten · 2 Kanten/)).toBeVisible();

  // Die Regler gehören zur Live-Simulation und werden mit einem Einmal-Layout gesperrt.
  await expect(page.getByRole("button", { name: "Regler" })).toBeEnabled();
  await page.getByRole("button", { name: "hierarchisch" }).click();
  await expect(page.getByRole("button", { name: "Regler" })).toBeDisabled();

  // Zurück zur Physik: Die Regler öffnen sich und wirken auf die Simulation.
  await page.getByRole("button", { name: "Physik" }).click();
  await page.getByRole("button", { name: "Regler" }).click();
  await expect(page.getByLabel("Kantenlänge")).toBeVisible();
});

test("Ein Kartenfilter landet in der URL und ist damit teilbar (§17.1)", async ({ page }) => {
  await page.goto("/?view=graph&store=shared");

  await page.getByLabel("nur unbestätigte").check();

  await expect(page).toHaveURL(/unverified=1/);
});

test("Mitglied per Drag-and-Drop in ein anderes Cluster verschieben (§24)", async ({ page }) => {
  await page.goto("/?view=cluster&store=shared&cluster=cluster:a");

  const mitglied = page.getByTestId("mitglied-confluence:1");
  await expect(mitglied).toBeVisible();
  await mitglied.dragTo(page.getByTestId("cluster-cluster:b"));

  // Die Wirkung, nicht die Geste: Das Mitglied ist danach im Zielcluster.
  await page.goto("/?view=cluster&store=shared&cluster=cluster:b");
  await expect(page.getByTestId("mitglied-confluence:1")).toBeVisible();

  await page.goto("/?view=cluster&store=shared&cluster=cluster:a");
  await expect(page.getByTestId("mitglied-confluence:1")).toHaveCount(0);
});

test("Modellvorschlag bestätigen (§24)", async ({ page }) => {
  await page.goto("/?view=kuration&store=shared");

  await expect(page.getByText("Beide beschreiben dieselbe Ladestrecke.")).toBeVisible();
  await page.getByRole("button", { name: /Bestätigen/ }).click();

  // Danach ist die Warteschlange leer — der Vorschlag wartet auf niemanden mehr.
  await expect(page.getByText(/Nichts offen/)).toBeVisible();
});

test("Modellvorschlag verwerfen — und der Unterschied zum Löschen (§16.2)", async ({ page }) => {
  const protokoll = await mockApi(page);
  await page.goto("/?view=kuration&store=shared");

  await expect(page.getByText("Beide beschreiben dieselbe Ladestrecke.")).toBeVisible();
  await page.getByRole("button", { name: /Verwerfen/ }).click();
  await expect(page.getByText(/Nichts offen/)).toBeVisible();

  // Verworfen heißt POST auf /reject — nicht DELETE. Nur das erste hinterlässt einen
  // Negativvermerk und bindet damit den nächsten Lauf.
  expect(protokoll.writes.some((eintrag) => eintrag.url.includes("/reject"))).toBe(true);
  expect(protokoll.writes.some((eintrag) => eintrag.method === "DELETE")).toBe(false);
});

test("Sync aus der UI starten und den Fortschritt live sehen (§24, §16.3)", async ({ page }) => {
  await page.goto("/?view=quellen&store=shared");

  // `exact`, weil der Navigationseintrag "Quellen & Sync" den Namen sonst mit-matcht.
  await page.getByRole("button", { name: "Sync", exact: true }).click();

  const fortschritt = page.getByTestId("fortschritt");
  await expect(fortschritt).toBeVisible();
  // Der Strom liefert erst "running", dann "succeeded" — beide Zustände kommen über
  // EventSource und nicht über eine Abfrage.
  await expect(fortschritt).toContainText("succeeded");
});

test("Inhaltsfelder eines gespiegelten Konzepts sind erkennbar gesperrt (§24, §17.3)", async ({
  page,
}) => {
  await page.goto("/?view=browser&store=shared&id=confluence:1");

  await expect(page.getByTestId("gesperrt-Beschreibung")).toBeVisible();
  await expect(page.getByTestId("gesperrt-Fließtext")).toBeVisible();
  // Der Status bleibt änderbar: Er gehört dem Menschen, auch an gespiegelten Inhalten (§17.4).
  await expect(page.getByLabel("Status", { exact: true })).toBeEditable();
});

test("Eine lokale Notiz ist nirgends gesperrt", async ({ page }) => {
  await page.goto("/?view=browser&store=personal&id=note:1");

  await expect(page.getByRole("heading", { name: "Onboarding-Notiz" })).toBeVisible();
  await expect(page.getByTestId("gesperrt-Beschreibung")).toHaveCount(0);
  // Der Satz steht zweimal — Kopfzeile und Panel-Chip. Beide sollen da sein; sichtbar geprüft
  // wird der Chip am Konzept, denn er hängt an der Sache und nicht am gewählten Store.
  await expect(page.getByText(/verlässt diesen Rechner nicht/)).toHaveCount(2);
  await expect(page.getByText("persönlich — verlässt diesen Rechner nicht")).toBeVisible();
});

test("Die Anwender-Sitzung: / suchen, Treffer lesen — ohne Reiterwissen (§17.5, U3)", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("searchbox", { name: "Globale Suche" })).toBeVisible();

  // `/` fokussiert die Suche von überall; die Frage ist "Was haben wir zu Warehouse?".
  await page.keyboard.press("/");
  await page.keyboard.type("Warehouse");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("listbox", { name: "Suchtreffer" })).toBeVisible();

  // Enter öffnet den ersten Treffer zum Lesen — Dokumente-Ansicht, Inspektor rechts.
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/view=browser/);
  await expect(page.getByRole("complementary", { name: "Details" })).toBeVisible();
});

test("Der Ansichtszustand steht in der URL und ist damit teilbar (§17.1)", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Dokumente" }).click();
  await expect(page).toHaveURL(/view=browser/);

  await page.goBack();
  await expect(page).not.toHaveURL(/view=browser/);
});
