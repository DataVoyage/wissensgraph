/**
 * Aufklappen im echten Browser: Werden die Kinder eines aufgeklappten Clusters geladen und
 * gezeichnet? (§17.2, "Inkrementelles Aufklappen Hop für Hop … kein Vorabladen")
 *
 * Aufgeklappt wird über die Kernspace-Übersicht und nicht über einen Klick auf die Fläche,
 * und das ist keine Abkürzung: Beide Wege laufen durch dieselbe Stelle. Die Übersicht setzt
 * `state.id`, ein Klick auf einen Knoten setzt `state.id`, und daran hängt `aufklappen`.
 * Der Unterschied ist allein, dass sich ein Listeneintrag greifen lässt — die Knoten sind
 * WebGL und haben kein DOM-Element, über dem sich nicht einmal der Mauszeiger ändert.
 *
 *     WG_API_TOKEN=… WG_UI_URL=http://localhost:5173 \
 *       npx playwright test --config playwright.live.config.ts aufklappen.spec.ts
 */
import { expect, test, type Page } from "@playwright/test";

const TOKEN = process.env.WG_API_TOKEN ?? "";

/** Was die Kopfzeile über den gezeichneten Ausschnitt sagt: "43 Knoten · 44 Kanten". */
async function zaehlung(page: Page): Promise<{ text: string; knoten: number }> {
  const text = (await page.getByText(/\d+ Knoten/).first().innerText()).trim();
  return { text, knoten: Number(/(\d+) Knoten/.exec(text)?.[1] ?? 0) };
}

/** Klappt den Eintrag der Kernspace-Übersicht mit dem angegebenen Titel auf. */
async function aufklappen(page: Page, titel: string): Promise<void> {
  await page.getByRole("button", { name: titel, exact: false }).first().click();
  // Ein Hop = eine Abfrage; danach rechnet die Sternenkarte die Anordnung neu.
  await page.waitForTimeout(5000);
  await page.getByRole("button", { name: "Alles zeigen" }).click();
  await page.waitForTimeout(1200);
}

test("Ein aufgeklapptes Cluster bringt seine Mitglieder mit", async ({ page }) => {
  test.setTimeout(300_000);
  await page.addInitScript((wert) => window.sessionStorage.setItem("wg.token", wert), TOKEN);

  await page.goto("/?view=graph&mode=reise&store=shared");
  // Ohne Startknoten ist die Fläche leer und sagt das auch — §17.2 verlangt für die
  // Erkundung ausdrücklich "kein Vorabladen des Gesamtgraphen".
  await expect(page.getByText("Nichts zu zeichnen.")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Identity Provider Trust")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results/aufklappen-1-leer.png" });

  // Der Einstieg: ein Cluster mit dreizehn Mitgliedern.
  await aufklappen(page, "Identity Provider Trust");
  const erste = await zaehlung(page);
  console.log(`   Erster Hop:  ${erste.text}`);
  await page.screenshot({ path: "test-results/aufklappen-2-erstes-cluster.png" });
  expect(erste.knoten).toBeGreaterThan(13);

  // Ein zweites Cluster dazu — der Ausschnitt *wächst*, er wird nicht ersetzt (§17.2).
  await aufklappen(page, "Automating SAP Cloud In");
  const zweite = await zaehlung(page);
  console.log(`   Zweiter Hop: ${zweite.text}`);
  await page.screenshot({ path: "test-results/aufklappen-3-zweites-cluster.png" });
  expect(zweite.knoten).toBeGreaterThan(erste.knoten);

  // Und ein drittes, damit auch der wiederholte Fall im Bild steht.
  await aufklappen(page, "Core Data Services Gener");
  const dritte = await zaehlung(page);
  console.log(`   Dritter Hop: ${dritte.text}`);
  await page.screenshot({ path: "test-results/aufklappen-4-drittes-cluster.png" });
  expect(dritte.knoten).toBeGreaterThan(zweite.knoten);

  // -- Die drei Gesten auf der Fläche ----------------------------------------
  //
  // Ein Knoten auf der Zeichenfläche hat kein DOM-Element, an dem ein Test ihn greifen könnte,
  // und über ihm ändert sich nicht einmal der Mauszeiger. Was ihn verrät, ist die Ansicht
  // selbst: Eine Auswahl steht in der URL. Damit lässt sich die Fläche abtasten — und danach
  // dieselbe Stelle noch einmal treffen, denn ein Klick verschiebt seit der Gestentrennung
  // nichts mehr.
  const feld = (await page.getByTestId("graph-canvas").boundingBox())!;
  const vorGesten = await zaehlung(page);
  let punkt: { x: number; y: number } | null = null;
  suche: for (let x = feld.x + 12; x < feld.x + feld.width - 12; x += 7) {
    for (let y = feld.y + 12; y < feld.y + feld.height - 12; y += 7) {
      await page.mouse.click(x, y);
      if (new URL(page.url()).searchParams.get("id") !== null) {
        punkt = { x, y };
        break suche;
      }
    }
  }
  expect(punkt, "kein Knoten auf der Fläche gefunden").not.toBeNull();

  // 1. Ein Klick wählt aus — und holt keinen Hop nach.
  await page.waitForTimeout(2500);
  const nachKlick = await zaehlung(page);
  console.log(`   Klick auf einen Knoten: ${nachKlick.text} (vorher ${vorGesten.text})`);
  await page.screenshot({ path: "test-results/gesten-1-klick.png" });
  expect(nachKlick.knoten).toBe(vorGesten.knoten);
  // Was der Klick stattdessen tut: Er zeigt Felder, Provenienz und Verbindungen (§17.2).
  const inspektor = page.getByRole("complementary", { name: "Inspektor" });
  await expect(inspektor).toContainText("Provenienz");
  await expect(inspektor).toContainText("Ausgehend");

  // 2. Der Doppelklick klappt auf.
  await page.mouse.dblclick(punkt!.x, punkt!.y);
  await page.waitForTimeout(5000);
  await page.getByRole("button", { name: "Alles zeigen" }).click();
  await page.waitForTimeout(1200);
  const nachDoppel = await zaehlung(page);
  console.log(`   Doppelklick auf denselben Knoten: ${nachDoppel.text}`);
  await page.screenshot({ path: "test-results/gesten-2-doppelklick.png" });
  expect(nachDoppel.knoten).toBeGreaterThan(nachKlick.knoten);

  // 3. Ein Klick ins Leere hebt die Auswahl auf — und springt nicht in den Graphen.
  await page.mouse.click(feld.x + 8, feld.y + 8);
  await page.waitForTimeout(1200);
  console.log(`   Klick ins Leere: id=${new URL(page.url()).searchParams.get("id")}`);
  await page.screenshot({ path: "test-results/gesten-3-daneben.png" });
  expect(new URL(page.url()).searchParams.get("id")).toBeNull();
  await expect(page.getByText("Kein Knoten ausgewählt.")).toBeVisible();
  expect((await zaehlung(page)).knoten).toBe(nachDoppel.knoten);
});
