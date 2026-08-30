/**
 * Laufzeitkonfiguration der SPA (§17.1).
 *
 * Die Basis-URL der API wird NICHT zur Bauzeit eingebrannt, sondern zur Laufzeit aus
 * `/config.js` gelesen. Damit ist ein Umgebungswechsel eine Änderung an einer Datei im
 * nginx-Container und kein neuer Build — genau das verlangt Leitprinzip 12 für die UI-Seite.
 */

export interface RuntimeConfig {
  /** Basis-URL der HTTP-API, z. B. `http://localhost:8080`. */
  apiBaseUrl: string;
}

declare global {
  interface Window {
    __WG_CONFIG__?: Partial<RuntimeConfig>;
  }
}

/** Fällt auf denselben Ursprung zurück, unter dem die SPA ausgeliefert wurde. */
const FALLBACK_API_BASE_URL = "";

/**
 * Liest die zur Laufzeit injizierte Konfiguration.
 *
 * Fehlt `/config.js` (etwa im `vite dev`-Server), wird der gleiche Ursprung angenommen. Das ist
 * kein Fehler: In der Entwicklung leitet der Vite-Proxy weiter.
 */
export function loadConfig(): RuntimeConfig {
  const injected = window.__WG_CONFIG__ ?? {};
  return {
    apiBaseUrl: injected.apiBaseUrl ?? FALLBACK_API_BASE_URL,
  };
}
