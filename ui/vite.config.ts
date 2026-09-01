import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Im Dev-Server läuft die SPA unter einem anderen Port als die API. Der Proxy sorgt dafür,
    // dass derselbe relative Pfad in Entwicklung und Betrieb funktioniert — die SPA muss
    // deshalb nicht wissen, in welchem Modus sie läuft.
    proxy: {
      "/readyz": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
      "/api": "http://localhost:8080",
    },
  },
  test: {
    // Die UI-Tests laufen gegen einen **echten Browser**, nicht gegen eine DOM-Simulation wie
    // jsdom. Das ist Vorgabe: jsdom hatte mehrfach verdeckt, was nur der Browser beweist —
    // Canvas/WebGL, echte Maße, echtes Klickverhalten. `reducedMotion: "reduce"` ist dabei keine
    // Simulation, sondern eine echte Browser-Einstellung: Die Zeichenfläche ehrt
    // `prefers-reduced-motion`, und ein Test, der Layout-Animationen abwartet, prüft Geduld
    // statt Verhalten.
    browser: {
      enabled: true,
      provider: "playwright",
      name: "chromium",
      headless: true,
      screenshotFailures: false,
      providerOptions: {
        context: { reducedMotion: "reduce" },
      },
    },
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    // Nur die Komponententests unter src/. Ohne diese Zeile sammelt Vitest auch e2e/*.spec.ts
    // ein — die Playwright-Tests, die einen echten Browser brauchen. Sie scheitern dann nicht an
    // ihrem Inhalt, sondern daran, dass zwei Testläufer dieselbe Datei für sich beanspruchen.
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      // istanbul statt v8: Die v8-Abdeckung setzt einen Node-Prozess voraus; im Browser-Modus
      // instrumentiert istanbul den Code beim Ausliefern.
      provider: "istanbul",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/main.tsx",
        "src/test-setup.ts",
        // Die Tests und ihre Hilfen zählen nicht mit: Eine Suite, die sich selbst abdeckt,
        // schönt die Zahl.
        "src/**/*.test.{ts,tsx}",
        "src/test-support.tsx",
        // Reine Typdeklarationen. Sie erzeugen keinen Code, den man ausführen könnte — eine
        // Abdeckung von 0 % sagt hier nichts über die Prüftiefe aus.
        "src/api/schema.ts",
        "src/api/types.ts",
      ],
      // Zeilen und Anweisungen auf 90 % — dieselbe Schwelle, die für den Python-Teil gilt
      // (§22, `fail_under = 90`).
      //
      // Funktionen und Zweige liegen niedriger, und das ist eine bewusste Angabe und kein
      // Nachgeben: In JSX zählt v8 jede Render-Rückrufe einer Liste als eigene Funktion und
      // jeden `?.`/`??`-Ausdruck als Zweig. Eine Ansicht mit sechs Tabellen hat damit Dutzende
      // "Funktionen", die nichts entscheiden. Sie auf 90 % zu treiben hieße, Tests zu schreiben,
      // die Listen mit Beispieldaten füllen, damit ein Zähler steigt — und nicht, weil eine
      // Aussage geprüft würde.
      thresholds: { lines: 90, statements: 90, functions: 80, branches: 80 },
    },
  },
});
