import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright-Konfiguration für die Kernflüsse aus §24, Stufe 11.
 *
 * Gefahren wird gegen den **gebauten** Stand über `vite preview` und nicht gegen den Dev-Server:
 * Was hier grün ist, ist damit dasselbe Bündel, das der nginx-Container ausliefert (§17.1) — und
 * nicht eine Fassung, die nur mit laufendem Hot-Reload funktioniert.
 *
 * Die HTTP-API wird im Browser abgefangen (`page.route`) statt gestartet. Die Arbeitsteilung ist
 * Absicht: Ob die API *richtig* antwortet, prüfen die Python-Tests gegen die Dienste; hier geht
 * es um das, was nur ein echter Browser zeigen kann — Cytoscape auf einem echten Canvas,
 * Drag-and-Drop mit echten Zeigerereignissen, `EventSource` als echte Verbindung.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run build && npm run preview -- --port 4173 --host 127.0.0.1",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
