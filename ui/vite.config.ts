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
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    coverage: {
      provider: "v8",
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
