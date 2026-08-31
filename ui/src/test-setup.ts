/** Globale Einrichtung der UI-Tests. */

import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

/*
 * jsdom hat kein Canvas. Cytoscape bricht ohne eines beim Anlegen ab — nicht weil der Code
 * falsch wäre, sondern weil die Umgebung eine Fähigkeit nicht hat, die jeder Browser mitbringt.
 *
 * Deshalb ein minimaler 2D-Kontext statt des Pakets `canvas`: Das brächte eine native
 * Übersetzung mit und damit eine Abhängigkeit von einem Compiler auf jedem Rechner, auf dem
 * jemand die Tests laufen lässt — für ein Bild, das in einem Test ohnehin niemand ansieht.
 * Was die Zeichenfläche *tut*, prüft der Playwright-Test in einem echten Browser.
 */
const kontext = new Proxy(
  {},
  {
    get: (_ziel, name) => {
      if (name === "canvas") {
        return { width: 800, height: 600 };
      }
      if (name === "measureText") {
        return () => ({ width: 0 });
      }
      if (name === "getImageData") {
        return () => ({ data: new Uint8ClampedArray(4) });
      }
      return () => undefined;
    },
  },
);

Object.defineProperty(window.HTMLCanvasElement.prototype, "getContext", {
  value: vi.fn(() => kontext),
  writable: true,
});

/*
 * jsdom misst jedes Element mit 0 × 0. Ein Layout-Algorithmus, der eine Fläche verteilt, teilt
 * dann durch null. Die Zahlen sind willkürlich — es geht nur darum, dass es überhaupt eine
 * Fläche gibt.
 */
for (const [name, wert] of [
  ["clientWidth", 800],
  ["clientHeight", 600],
  ["offsetWidth", 800],
  ["offsetHeight", 600],
] as const) {
  Object.defineProperty(window.HTMLElement.prototype, name, {
    configurable: true,
    value: wert,
  });
}
