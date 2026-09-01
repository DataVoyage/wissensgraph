import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright gegen den **laufenden Stack** statt gegen einen frisch gebauten Preview-Server.
 *
 * Der Unterschied ist der Zweck: `playwright.config.ts` prüft das gebaute Bündel gegen eine
 * nachgebildete API — reproduzierbar, ohne Datenbank, für die Abnahme. Diese Konfiguration
 * zeigt das Gegenteil: die echte Oberfläche mit dem echten Bestand, so wie sie gerade im
 * Container läuft. Deshalb auch **kein** `webServer`-Eintrag; gestartet wird hier nichts,
 * es wird nur hingesehen.
 */
export default defineConfig({
  testDir: "./e2e-live",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: process.env.WG_UI_URL ?? "http://localhost:5173",
    viewport: { width: 1600, height: 950 },
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      // Sichtbares Fenster, kein Headless: Der Graph wird über WebGL gezeichnet, und
      // Headless-Chromium schaltet dafür auf den Software-Rasterizer SwiftShader um. Was dabei
      // herauskommt — Bildrate wie Bild — ist keine Aussage über das, was ein Nutzer sieht.
      // Wer die Darstellung beurteilen will, braucht die Hardwarebeschleunigung.
      use: { ...devices["Desktop Chrome"], headless: false },
    },
  ],
});
