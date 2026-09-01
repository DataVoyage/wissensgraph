/**
 * Die Bedienleiste über der Zeichenfläche (§17.2, "Layouts").
 *
 * Sie liegt als schwebende Leiste *auf* dem Graphen und nicht daneben. Der Grund ist der Platz:
 * Jedes Bedienelement, das eine eigene Spalte bekommt, nimmt ihn dem Bild weg — und das Bild ist
 * hier der Inhalt. Was selten gebraucht wird (die drei Regler), ist zusätzlich eingeklappt.
 *
 * Die Regler sind nicht Spielerei. §17.2 nennt drei Layouts, weil verschiedene Fragen verschiedene
 * Anordnungen brauchen; dieselbe Begründung gilt innerhalb der Physik: Ein weit gespreizter Graph
 * zeigt Struktur, ein dicht gezogener zeigt Gruppen.
 */

import { useState } from "react";

import type { LayoutName, PhysikWerte } from "./GraphCanvas";

export interface GraphControlsProps {
  layout: LayoutName;
  onLayout: (layout: LayoutName) => void;
  physik: PhysikWerte;
  onPhysik: (werte: PhysikWerte) => void;
  labels: boolean;
  onLabels: (an: boolean) => void;
  onEinpassen: () => void;
  knoten: number;
  kanten: number;
  /** Steht in der Leiste, wenn der Ausschnitt gedeckelt ist (§17.3). */
  gedeckelt?: boolean;
}

const LAYOUTS: ReadonlyArray<{ wert: LayoutName; label: string; titel: string }> = [
  { wert: "physik", label: "Physik", titel: "Live-Simulation — Knoten lassen sich ziehen" },
  { wert: "cose", label: "kraftbasiert", titel: "Einmaliges kraftbasiertes Layout, animiert" },
  { wert: "concentric", label: "konzentrisch", titel: "Ringe nach Hop-Distanz zum Startknoten" },
  { wert: "breadthfirst", label: "hierarchisch", titel: "Baum entlang der member-Kanten" },
];

const REGLER: ReadonlyArray<{
  feld: keyof PhysikWerte;
  label: string;
  min: number;
  max: number;
  schritt: number;
}> = [
  { feld: "abstossung", label: "Abstoßung", min: 4, max: 90, schritt: 2 },
  { feld: "kantenlaenge", label: "Kantenlänge", min: 30, max: 320, schritt: 10 },
  { feld: "schwerkraft", label: "Zusammenhalt", min: 0, max: 0.9, schritt: 0.05 },
];

export function GraphControls({
  layout,
  onLayout,
  physik,
  onPhysik,
  labels,
  onLabels,
  onEinpassen,
  knoten,
  kanten,
  gedeckelt = false,
}: GraphControlsProps): JSX.Element {
  const [reglerOffen, setzeReglerOffen] = useState(false);

  return (
    <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-col items-start gap-2">
      <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-lg border border-ton-200 bg-ton-0/95 p-1.5 shadow-schwebend backdrop-blur">
        <div className="wg-segment" role="group" aria-label="Layout">
          {LAYOUTS.map((eintrag) => (
            <button
              key={eintrag.wert}
              type="button"
              title={eintrag.titel}
              aria-pressed={layout === eintrag.wert}
              onClick={() => onLayout(eintrag.wert)}
            >
              {eintrag.label}
            </button>
          ))}
        </div>

        <span className="h-5 w-px bg-ton-200" aria-hidden="true" />

        <button
          type="button"
          className={`wg-toggle wg-button-klein ${reglerOffen ? "wg-toggle-an" : ""}`}
          aria-expanded={reglerOffen}
          disabled={layout !== "physik"}
          title={
            layout === "physik"
              ? "Abstoßung, Kantenlänge und Zusammenhalt einstellen"
              : "Die Regler wirken nur auf die Live-Simulation"
          }
          onClick={() => setzeReglerOffen((vorher) => !vorher)}
        >
          Regler
        </button>
        <button
          type="button"
          className={`wg-toggle wg-button-klein ${labels ? "wg-toggle-an" : ""}`}
          aria-pressed={labels}
          title="Beschriftungen ein- und ausblenden — ohne sie bleibt ein dichter Graph lesbar"
          onClick={() => onLabels(!labels)}
        >
          Titel
        </button>
        <button
          type="button"
          className="wg-button wg-button-klein"
          onClick={onEinpassen}
          title="Den ganzen Ausschnitt ins Bild rücken"
        >
          Alles zeigen
        </button>

        <span className="h-5 w-px bg-ton-200" aria-hidden="true" />

        <span className="px-1 text-2xs tabular-nums text-ton-500">
          {knoten} Knoten · {kanten} Kanten
          {gedeckelt && (
            <span className="ml-1 font-semibold text-signal-600" title="Der Ausschnitt ist gedeckelt (§17.3)">
              (Ausschnitt)
            </span>
          )}
        </span>
      </div>

      {reglerOffen && layout === "physik" && (
        <div className="pointer-events-auto w-64 animate-einblenden rounded-lg border border-ton-200 bg-ton-0/95 p-3 shadow-schwebend backdrop-blur">
          {REGLER.map((regler) => (
            <label key={regler.feld} className="mb-2 block last:mb-0">
              <span className="flex items-baseline justify-between">
                <span className="wg-label mb-0">{regler.label}</span>
                <span className="text-2xs tabular-nums text-ton-400">
                  {physik[regler.feld]}
                </span>
              </span>
              <input
                type="range"
                className="w-full accent-signal-500"
                aria-label={regler.label}
                min={regler.min}
                max={regler.max}
                step={regler.schritt}
                value={physik[regler.feld]}
                onChange={(ereignis) =>
                  onPhysik({ ...physik, [regler.feld]: Number(ereignis.target.value) })
                }
              />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
