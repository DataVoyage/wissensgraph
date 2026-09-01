/**
 * Die Legende der visuellen Kodierung (§17.2).
 *
 * §17.2 legt eine Kodierung fest — Form für den Store, Farbe für den Typ, Linienfarbe für die
 * Provenienz —, und eine festgelegte Kodierung ist nur dann eine Information, wenn sie irgendwo
 * steht. Ohne Legende ist eine kräftige rote Linie ein hübsches Detail; mit Legende ist sie
 * ein unbestätigter Modellvorschlag, der auf jemanden wartet (Leitprinzip 6).
 *
 * Die Farben kommen aus `theme.ts` — derselben Quelle, aus der die Zeichenfläche sie nimmt. Eine
 * Legende mit eigenen Farbwerten wäre eine Behauptung über das Bild statt einer Beschreibung.
 */

import { PROVENIENZ, farbeFuerTyp } from "../theme";

export interface GraphLegendProps {
  /** Die Typen, die im gezeigten Ausschnitt tatsächlich vorkommen. */
  typen: string[];
  /**
   * Alle Typen dieser Installation in konfigurierter Reihenfolge.
   *
   * Nicht dasselbe wie `typen`: Die Farbe eines Typs hängt an seinem Platz in der *vollen* Liste
   * und nicht an seinem Platz im gerade gezeigten Ausschnitt. Sonst wechselte ein Typ die Farbe,
   * sobald ein Filter einen anderen ausblendet.
   */
  alleTypen?: readonly string[];
}

export function GraphLegend({ typen, alleTypen = [] }: GraphLegendProps): JSX.Element {
  return (
    <section aria-label="Legende">
      <h2 className="wg-panel-titel">Legende</h2>

      {typen.length > 0 && (
        <div className="mb-3">
          <p className="wg-label">Typ — Farbe</p>
          <ul className="space-y-0.5">
            {typen.map((typ) => (
              <li key={typ} className="flex items-center gap-2 text-xs text-ton-700">
                <span
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 rounded-full border-2 border-ton-0"
                  style={{ backgroundColor: farbeFuerTyp(typ, alleTypen) }}
                />
                {typ}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-3">
        <p className="wg-label">Store — Form</p>
        <ul className="space-y-0.5 text-xs text-ton-700">
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="h-3 w-3 shrink-0 rounded-full bg-ton-400" />
            geteilt
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="h-3 w-3 shrink-0 rotate-45 bg-ton-700" />
            persönlich
          </li>
        </ul>
      </div>

      <div>
        <p className="wg-label">Kante — Herkunft</p>
        <ul className="space-y-0.5">
          {PROVENIENZ.map((eintrag) => (
            <li key={eintrag.label} className="flex items-center gap-2 text-xs text-ton-700">
              <span
                aria-hidden="true"
                className="h-0.5 w-5 shrink-0 rounded"
                style={{ backgroundColor: eintrag.farbe }}
              />
              <span title={eintrag.erklaerung}>{eintrag.label}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-2xs leading-relaxed text-ton-400">
          Größe = Gewicht, kräftige Linie = Struktur (member). Voll deckend heißt: von einem
          Modell vorgeschlagen, von niemandem bestätigt — Geprüftes tritt zurück.
        </p>
      </div>
    </section>
  );
}
