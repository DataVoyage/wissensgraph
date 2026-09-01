/**
 * Verwalten → Läufe (§17.2 Ansicht 6, U5): Historie, Live-Fortschritt, Abbrechen.
 *
 * Angestoßen wird anderswo — Syncs bei den Quellen, Aufbauläufe in der Automatisierung (mit
 * Probelauf zuerst). Hier steht, was lief und läuft: §17.3 verlangt, dass Läufe die UI nie
 * blockieren, und diese Ansicht ist die Einlösung — verfolgen statt warten.
 */

import { useCancelRun, useRuns } from "../api/hooks";
import { Fortschritt } from "../components/Fortschritt";
import { Laden, Leer, Schaltflaeche } from "../components/basis";
import type { UiState } from "../state";

export interface RunsViewProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function RunsView({ state, onChange }: RunsViewProps): JSX.Element {
  const laeufe = useRuns(state.store);
  const abbrechen = useCancelRun();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        {state.run !== undefined && (
          <section className="wg-panel space-y-2" aria-label="Verfolgter Lauf">
            <h2 className="wg-panel-titel">Verfolgter Lauf</h2>
            <Fortschritt runId={state.run} />
          </section>
        )}

        <section className="wg-panel space-y-2" aria-label="Lauf-Historie">
          <h2 className="wg-panel-titel">Lauf-Historie ({state.store})</h2>
          {laeufe.isPending && <Laden was="Läufe werden geladen" />}
          {laeufe.data?.items.length === 0 && (
            <Leer titel="Noch kein Lauf in diesem Store." />
          )}
          {(laeufe.data?.items.length ?? 0) > 0 && (
            <table className="wg-tabelle text-xs">
              <thead>
                <tr>
                  <th>Art</th>
                  <th>Status</th>
                  <th>Start</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(laeufe.data?.items ?? []).map((lauf) => (
                  <tr key={lauf.id} aria-selected={state.run === lauf.id}>
                    <td className="font-medium text-ton-800">
                      {lauf.kind}
                      {lauf.params?.dry_run === true && (
                        <span className="wg-chip ml-1.5">Probelauf</span>
                      )}
                    </td>
                    <td>
                      <span className={`wg-chip ${lauf.status === "failed" ? "wg-chip-signal" : ""}`}>
                        {lauf.status}
                      </span>
                    </td>
                    <td className="tabular-nums text-ton-500">
                      {lauf.started_at?.slice(0, 19) ?? "—"}
                    </td>
                    <td>
                      <div className="flex justify-end gap-1">
                        <Schaltflaeche
                          art="still"
                          klein
                          onClick={() => onChange({ run: lauf.id })}
                        >
                          verfolgen
                        </Schaltflaeche>
                        {(lauf.status === "queued" || lauf.status === "running") && (
                          <Schaltflaeche
                            art="gefahr"
                            klein
                            onClick={() => abbrechen.mutate(lauf.id)}
                          >
                            abbrechen
                          </Schaltflaeche>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
