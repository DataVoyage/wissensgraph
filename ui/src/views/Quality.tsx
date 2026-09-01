/**
 * Ansicht 8 — Qualität (§17.2, U4): Arbeitet die Automatisierung gut?
 *
 * Erste Ausbaustufe, wie im Konzept (Abschnitt 6) festgelegt: Die Oberfläche verdichtet, was
 * vorhandene Endpunkte hergeben — `/stats`, die Kurationswarteschlange, die Clusterliste. Die
 * Quoten aus dem Journal (bestätigt/verworfen je Zeitraum) brauchen einen eigenen Endpunkt und
 * kommen später; hier steht deshalb keine Zahl, die geraten wäre.
 */

import { useClusters, useQueue, useStats } from "../api/hooks";
import { Laden, Leer } from "../components/basis";
import type { UiState } from "../state";

export interface QualityProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function Quality({ state, onChange }: QualityProps): JSX.Element {
  const zahlen = useStats();
  const warteschlange = useQueue(state.store);
  const cluster = useClusters(state.store);

  const stores = zahlen.data?.stores ?? [];
  const offen = warteschlange.data?.items ?? [];
  const aeltester = offen
    .map((aufgabe) => aufgabe.edge?.created_at)
    .filter((wert): wert is string => typeof wert === "string")
    .sort()[0];
  const unbetitelt = (cluster.data?.items ?? []).filter((eintrag) => !eintrag.curated);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        <section className="wg-panel space-y-2" aria-label="Vernetzung">
          <h2 className="wg-panel-titel">Vernetzung je Store</h2>
          <p className="wg-hinweis">
            Lose Knoten sind der Rückstand der Waisen-Anbindung (§15). Der Weg zu weniger:
            Automatisierung → Waisen-Anbindung, Probelauf zuerst.
          </p>
          {zahlen.isPending ? (
            <Laden was="Bestandszahlen werden geladen" />
          ) : (
            <table className="wg-tabelle text-xs">
              <thead>
                <tr>
                  <th>Store</th>
                  <th>Konzepte</th>
                  <th>Cluster</th>
                  <th>lose</th>
                  <th>Anteil lose</th>
                </tr>
              </thead>
              <tbody>
                {stores.map((eintrag) => {
                  const anteil = eintrag.concepts === 0 ? 0 : eintrag.loose / eintrag.concepts;
                  return (
                    <tr key={eintrag.store}>
                      <td>{eintrag.store}</td>
                      <td className="text-right tabular-nums">{eintrag.concepts}</td>
                      <td className="text-right tabular-nums">{eintrag.clusters}</td>
                      <td className="text-right tabular-nums">{eintrag.loose}</td>
                      <td
                        className={`text-right tabular-nums ${
                          anteil > 0.2 ? "font-semibold text-signal-700" : ""
                        }`}
                      >
                        {(anteil * 100).toFixed(1)} %
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        <section className="wg-panel space-y-2" aria-label="Warteschlange">
          <h2 className="wg-panel-titel">Kurationswarteschlange ({state.store})</h2>
          {offen.length === 0 ? (
            <Leer titel="Nichts offen.">
              Alles, was Modelle vorgeschlagen haben, ist entschieden — der beste Zustand dieser
              Seite.
            </Leer>
          ) : (
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm text-ton-700">
              <span>
                <strong className="text-2xl tabular-nums text-ton-900">{offen.length}</strong>{" "}
                {offen.length === 1 ? "offener Posten" : "offene Posten"}
              </span>
              {aeltester !== undefined && (
                <span>
                  ältester vom{" "}
                  <strong className="tabular-nums">{aeltester.slice(0, 10)}</strong> — eine
                  Warteschlange, die altert, wird nicht mehr gelesen
                </span>
              )}
              <button
                type="button"
                className="wg-button wg-button-klein"
                onClick={() => onChange({ view: "kuration" })}
              >
                zur Kuration
              </button>
            </div>
          )}
        </section>

        <section className="wg-panel space-y-2" aria-label="Betitelung">
          <h2 className="wg-panel-titel">Cluster ohne kuratierten Titel ({state.store})</h2>
          <p className="wg-hinweis">
            Automatische Titel sind Platzhalter. Ein Titel von Hand sperrt die Neubetitelung
            (§13.2) — das ist der Moment, in dem ein Cluster ein Thema wird.
          </p>
          {unbetitelt.length === 0 ? (
            <Leer titel="Alle Cluster sind von Hand betitelt." />
          ) : (
            <ul className="-mx-1 columns-2 text-sm">
              {unbetitelt.slice(0, 30).map((eintrag) => (
                <li key={eintrag.id} className="break-inside-avoid">
                  <button
                    type="button"
                    className="wg-eintrag truncate"
                    onClick={() => onChange({ view: "cluster", cluster: eintrag.id })}
                  >
                    {eintrag.title ?? eintrag.id}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {unbetitelt.length > 30 && (
            <p className="wg-hinweis">… und {unbetitelt.length - 30} weitere.</p>
          )}
        </section>
      </div>
    </div>
  );
}
