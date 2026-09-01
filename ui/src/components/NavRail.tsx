/**
 * Die Navigationsleiste links — drei Arbeitsbereiche, eine Spalte (§17.5).
 *
 * Schmal zeigt sie Monogramme, breit die Namen; der Zustand gehört der Werkbank und nicht der
 * URL. Der Kurationszähler hängt an seinem Eintrag und ist in beiden Breiten sichtbar: Eine
 * Warteschlange, die man erst sieht, wenn man hinsieht, wächst unbemerkt.
 */

import { BEREICHE } from "../bereiche";
import type { ViewName } from "../state";

export interface NavRailProps {
  aktiv: ViewName;
  breit: boolean;
  offeneKuration: number;
  onNavigieren: (view: ViewName) => void;
  onBreite: (breit: boolean) => void;
}

export function NavRail({
  aktiv,
  breit,
  offeneKuration,
  onNavigieren,
  onBreite,
}: NavRailProps): JSX.Element {
  return (
    <nav
      aria-label="Arbeitsbereiche"
      className={`flex shrink-0 flex-col border-r border-ton-200 bg-ton-0 transition-all duration-ruhig ${
        breit ? "w-44" : "w-14"
      }`}
    >
      <div className="flex-1 overflow-y-auto py-2">
        {BEREICHE.map((bereich) => (
          <section key={bereich.name} aria-label={bereich.label} className="mb-3 px-2">
            <h2
              className="mb-1 flex h-5 items-center px-1.5 text-2xs font-semibold uppercase tracking-wider text-ton-400"
              title={bereich.label}
            >
              {breit ? bereich.label : bereich.kuerzel}
            </h2>
            <ul className="space-y-0.5">
              {bereich.ansichten.map((ansicht) => {
                const istAktiv = aktiv === ansicht.name;
                return (
                  <li key={ansicht.name}>
                    <button
                      type="button"
                      className={`wg-eintrag flex items-center gap-2 ${istAktiv ? "wg-eintrag-aktiv" : ""} ${
                        breit ? "" : "justify-center px-0"
                      }`}
                      aria-current={istAktiv ? "page" : undefined}
                      aria-label={ansicht.label}
                      title={ansicht.label}
                      onClick={() => onNavigieren(ansicht.name)}
                    >
                      <span
                        aria-hidden="true"
                        className={`grid h-5 w-5 shrink-0 place-items-center rounded text-2xs font-semibold ${
                          istAktiv ? "bg-ton-0/20 text-ton-0" : "bg-ton-100 text-ton-500"
                        }`}
                      >
                        {ansicht.label.slice(0, 2)}
                      </span>
                      {breit && <span className="truncate">{ansicht.label}</span>}
                      {ansicht.name === "kuration" && offeneKuration > 0 && (
                        <span
                          className={`wg-zaehler ${breit ? "ml-auto" : "absolute -mt-4 ml-6"}`}
                          title={`${offeneKuration} offene Posten`}
                        >
                          {offeneKuration}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>

      <button
        type="button"
        className="wg-button wg-button-still m-2 justify-center"
        aria-pressed={breit}
        aria-label="Navigation breit"
        title={breit ? "Navigation schmal stellen" : "Navigation breit stellen"}
        onClick={() => onBreite(!breit)}
      >
        <span aria-hidden="true">{breit ? "«" : "»"}</span>
      </button>
    </nav>
  );
}
