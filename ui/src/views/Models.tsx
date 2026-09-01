/**
 * Verwalten → Modelle & Kosten (§17.2 Ansicht 6, U5).
 *
 * Nutzung je Task und Modell mit Kostenschätzung, dazu die aufgelösten Routen aus `/models` —
 * dieselbe Auskunft wie `wg models describe` und `wg models usage` (§19, Leitprinzip 14).
 */

import { useModels, useUsage } from "../api/hooks";
import { Laden, Leer } from "../components/basis";
import type { UiState } from "../state";

export interface ModelsProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function Models({ state }: ModelsProps): JSX.Element {
  const modelle = useModels();
  const nutzung = useUsage(state.store);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        <section className="wg-panel space-y-2" aria-label="Modellnutzung">
          <h2 className="wg-panel-titel">Modellnutzung ({state.store})</h2>
          {nutzung.isPending && <Laden was="Nutzung wird geladen" />}
          {nutzung.data?.items.length === 0 && (
            <Leer titel="Noch keine Modellaufrufe verbucht." />
          )}
          {(nutzung.data?.items.length ?? 0) > 0 && (
            <table className="wg-tabelle text-xs">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Modell</th>
                  <th>Aufrufe</th>
                  <th>Token ein</th>
                  <th>Token aus</th>
                  <th>€ (geschätzt)</th>
                </tr>
              </thead>
              <tbody>
                {(nutzung.data?.items ?? []).map((zeile) => (
                  <tr key={`${zeile.task}-${zeile.model}`}>
                    <td>{zeile.task}</td>
                    <td className="font-mono">{zeile.model}</td>
                    <td className="text-right tabular-nums">{zeile.calls}</td>
                    <td className="text-right tabular-nums">{zeile.tokens_in}</td>
                    <td className="text-right tabular-nums">{zeile.tokens_out}</td>
                    <td className="text-right tabular-nums">{zeile.cost_estimate_eur.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="wg-panel space-y-2" aria-label="Aufgelöste Routen">
          <h2 className="wg-panel-titel">Aufgelöste Routen (§11.3)</h2>
          <ul className="space-y-1 text-xs text-ton-700">
            {(modelle.data?.tasks ?? []).map((route) => (
              <li key={route.task} className="flex flex-wrap items-baseline gap-1.5">
                <span className="w-44 font-medium">{route.task}</span>
                <code className="wg-chip">{route.model_key}</code>
                {route.local && <span className="wg-chip">lokal</span>}
                {/* Nur bei Vertex belegt. Aus 'eu' folgt ein anderer Ort der Verarbeitung als
                    aus 'europe-west4' — sichtbar wird der Unterschied allein am Endpunkt. */}
                {route.endpoint && <code className="wg-chip">{route.endpoint}</code>}
                {!route.configured && (
                  <span className="wg-chip wg-chip-signal">kein Schlüssel hinterlegt</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
