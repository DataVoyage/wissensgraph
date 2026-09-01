/**
 * Globale Einrichtung der UI-Tests.
 *
 * Die Tests laufen in einem echten Chromium (Vitest Browser Mode) — es gibt hier deshalb keine
 * nachgebauten Fähigkeiten mehr: Canvas, Maße, `matchMedia` bringt der Browser selbst mit.
 * Früher stand an dieser Stelle ein jsdom-Flickwerk aus Canvas-Attrappe und festen
 * Elementgrößen; dass es weg ist, ist der Punkt der Umstellung.
 */

import "@testing-library/jest-dom/vitest";
// Das echte Stylesheet, nicht nur die Komponenten: `h-full` und Co. sind Tailwind-Klassen, und
// ohne sie misst eine Zeichenfläche im Test 800×0 — sigma zeichnet dann in einen Strich.
import "./index.css";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Im Browser-Modus teilen sich alle Tests einer Datei dieselbe Seite. Ohne Aufräumen bleibt der
// gerenderte Baum des vorigen Tests stehen, und `getByText` findet plötzlich zwei Treffer.
afterEach(() => {
  cleanup();
});
