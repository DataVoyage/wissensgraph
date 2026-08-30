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
      exclude: ["src/main.tsx", "src/test-setup.ts"],
      thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
    },
  },
});
