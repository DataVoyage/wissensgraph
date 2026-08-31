/**
 * Ansicht 6 — Betriebsansicht (§17.2).
 *
 * Quellen mit Zustand, Läufe anstoßen und verfolgen, Modellnutzung, aufgelöste Konfiguration.
 *
 * Der Live-Fortschritt hängt an `EventSource` und nicht an einem Abfrageintervall: §17.3 verlangt
 * "Läufe blockieren die UI nie", und ein Strom, den der Server schließt, sagt der Oberfläche von
 * selbst, wann sie aufhören darf zu warten.
 */

import { useEffect, useState } from "react";

import { subscribeToRun } from "../api/events";
import {
  useCancelRun,
  useConfig,
  useModels,
  useRuns,
  useSources,
  useStartRun,
  useStats,
  useUsage,
  type RunKind,
} from "../api/hooks";
import type { Run } from "../api/types";
import type { UiState } from "../state";

export interface OperationsProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function Operations({ state, onChange }: OperationsProps): JSX.Element {
  const quellen = useSources();
  const laeufe = useRuns(state.store);
  const zahlen = useStats();
  const modelle = useModels();
  const nutzung = useUsage(state.store);
  const konfiguration = useConfig();
  const starten = useStartRun();
  const abbrechen = useCancelRun();
  const [scope, setzeScope] = useState("");

  const scopes = konfiguration.data?.scopes ?? [];
  const gewaehlterScope = scope || scopes[0]?.name || "";

  function anstossen(kind: RunKind, koerper: Record<string, unknown>): void {
    starten.mutate(
      { kind, body: koerper as never },
      { onSuccess: (lauf) => onChange({ run: lauf.id }) },
    );
  }

  return (
    <div className="grid h-full grid-cols-2 gap-3 overflow-y-auto">
      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Quellen</h2>
        <ul className="space-y-1 text-sm">
          {(quellen.data?.items ?? []).map((quelle) => (
            <li key={quelle.name} className="flex items-center gap-2">
              <span
                className={`h-2 w-2 rounded-full ${
                  quelle.usable ? "bg-emerald-500" : "bg-red-500"
                }`}
                aria-label={quelle.usable ? "benutzbar" : "nicht benutzbar"}
              />
              <span className="flex-1">{quelle.name}</span>
              <span className="text-xs text-slate-500">
                {quelle.last_run?.status ?? "noch kein Lauf"}
              </span>
              <button
                type="button"
                className="wg-button"
                disabled={!quelle.usable}
                onClick={() => anstossen("sync", { source: quelle.name })}
              >
                Sync
              </button>
            </li>
          ))}
          {quellen.data?.items.length === 0 && (
            <li className="text-xs text-slate-500">Keine Quelle eingeschaltet.</li>
          )}
        </ul>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Läufe anstoßen</h2>
        <label className="block text-sm">
          Scope
          <select
            className="wg-input"
            aria-label="Scope des Laufs"
            value={gewaehlterScope}
            onChange={(ereignis) => setzeScope(ereignis.target.value)}
          >
            {scopes.map((eintrag) => (
              <option key={eintrag.name} value={eintrag.name}>
                {eintrag.name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap gap-2">
          {(["embed", "cluster", "relations", "link-orphans"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              className="wg-button"
              onClick={() => anstossen(kind, { scope: gewaehlterScope })}
            >
              {kind}
            </button>
          ))}
        </div>
        {starten.isError && (
          <p role="alert" className="text-xs text-red-700">
            {starten.error.message}
          </p>
        )}
        {state.run !== undefined && <Fortschritt runId={state.run} />}
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Lauf-Historie</h2>
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th>Art</th>
              <th>Status</th>
              <th>Start</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(laeufe.data?.items ?? []).map((lauf) => (
              <tr key={lauf.id} className="border-t">
                <td>{lauf.kind}</td>
                <td>{lauf.status}</td>
                <td>{lauf.started_at?.slice(0, 19) ?? "—"}</td>
                <td>
                  <button
                    type="button"
                    className="wg-button"
                    onClick={() => onChange({ run: lauf.id })}
                  >
                    verfolgen
                  </button>
                  {(lauf.status === "queued" || lauf.status === "running") && (
                    <button
                      type="button"
                      className="wg-button"
                      onClick={() => abbrechen.mutate(lauf.id)}
                    >
                      abbrechen
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Modellnutzung</h2>
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th>Task</th>
              <th>Aufrufe</th>
              <th>Token ein</th>
              <th>Token aus</th>
              <th>€ (geschätzt)</th>
            </tr>
          </thead>
          <tbody>
            {(nutzung.data?.items ?? []).map((zeile) => (
              <tr key={`${zeile.task}-${zeile.model}`} className="border-t">
                <td>{zeile.task}</td>
                <td>{zeile.calls}</td>
                <td>{zeile.tokens_in}</td>
                <td>{zeile.tokens_out}</td>
                <td>{zeile.cost_estimate_eur.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="text-sm font-medium">Aufgelöste Routen</h3>
        <ul className="text-xs">
          {(modelle.data?.tasks ?? []).map((route) => (
            <li key={route.task}>
              {route.task}: <code>{route.model_key}</code>
              {route.local && " (lokal)"}
              {/* Nur bei Vertex belegt. Aus 'eu' folgt ein anderer Ort der Verarbeitung als aus
                  'europe-west4' — sichtbar wird der Unterschied allein am Endpunkt. */}
              {route.endpoint && <> über <code>{route.endpoint}</code></>}
              {!route.configured && " — kein Schlüssel hinterlegt"}
            </li>
          ))}
        </ul>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Bestand</h2>
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th>Store</th>
              <th>Konzepte</th>
              <th>Kanten</th>
              <th>Cluster</th>
              <th>lose</th>
            </tr>
          </thead>
          <tbody>
            {(zahlen.data?.stores ?? []).map((eintrag) => (
              <tr key={eintrag.store} className="border-t">
                <td>{eintrag.store}</td>
                <td>{eintrag.concepts}</td>
                <td>{eintrag.edges}</td>
                <td>{eintrag.clusters}</td>
                <td>{eintrag.loose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="text-sm font-semibold">Aufgelöste Konfiguration</h2>
        <p className="text-xs text-slate-500">Secrets sind maskiert (§20.2).</p>
        <pre className="max-h-64 overflow-auto text-xs">
          {JSON.stringify(konfiguration.data ?? {}, null, 2)}
        </pre>
      </section>
    </div>
  );
}

function Fortschritt({ runId }: { runId: string }): JSX.Element {
  const [lauf, setzeLauf] = useState<Run | null>(null);
  const [fehler, setzeFehler] = useState<string | null>(null);

  useEffect(() => {
    setzeLauf(null);
    setzeFehler(null);
    return subscribeToRun(runId, (ereignis) => {
      if (ereignis.kind === "error") {
        setzeFehler(ereignis.detail);
      } else {
        setzeLauf(ereignis.run);
      }
    });
  }, [runId]);

  return (
    <div data-testid="fortschritt" className="rounded border border-slate-200 p-2 text-xs">
      <p>
        Lauf <code>{runId.slice(0, 8)}</code> — {lauf?.status ?? "verbinde …"}
      </p>
      {lauf !== null && (
        <>
          <progress className="w-full" value={lauf.progress} max={1} />
          {Object.keys(lauf.stats).length > 0 && (
            <pre className="max-h-32 overflow-auto">{JSON.stringify(lauf.stats, null, 2)}</pre>
          )}
          {lauf.error !== null && <p className="text-red-700">{lauf.error}</p>}
        </>
      )}
      {fehler !== null && (
        <p role="alert" className="text-red-700">
          {fehler}
        </p>
      )}
    </div>
  );
}
