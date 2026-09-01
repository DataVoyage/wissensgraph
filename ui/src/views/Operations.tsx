/**
 * Ansicht 6 — Betriebsansicht (§17.2).
 *
 * Quellen mit Zustand, Läufe anstoßen und verfolgen, Modellnutzung, aufgelöste Konfiguration.
 *
 * Der Live-Fortschritt hängt an `EventSource` und nicht an einem Abfrageintervall: §17.3 verlangt
 * "Läufe blockieren die UI nie", und ein Strom, den der Server schließt, sagt der Oberfläche von
 * selbst, wann sie aufhören darf zu warten.
 */

import { useState } from "react";

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
import { Fortschritt } from "../components/Fortschritt";
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
        <h2 className="wg-panel-titel">Quellen</h2>
        <ul className="space-y-1 text-sm">
          {(quellen.data?.items ?? []).map((quelle) => (
            <li key={quelle.name} className="flex items-center gap-2">
              <span
                className={`h-2 w-2 rounded-full ${
                  quelle.usable ? "bg-emerald-500" : "bg-signal-500"
                }`}
                aria-label={quelle.usable ? "benutzbar" : "nicht benutzbar"}
              />
              <span className="flex-1">{quelle.name}</span>
              <span className="text-xs text-ton-500">
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
            <li className="text-xs text-ton-500">Keine Quelle eingeschaltet.</li>
          )}
        </ul>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="wg-panel-titel">Läufe anstoßen</h2>
        <label className="block">
          <span className="wg-label">Scope</span>
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
              className="wg-button font-mono"
              onClick={() => anstossen(kind, { scope: gewaehlterScope })}
            >
              {kind}
            </button>
          ))}
        </div>
        {starten.isError && (
          <p role="alert" className="wg-fehler">
            {starten.error.message}
          </p>
        )}
        {state.run !== undefined && <Fortschritt runId={state.run} />}
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="wg-panel-titel">Lauf-Historie</h2>
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
              <tr key={lauf.id}>
                <td className="font-medium text-ton-800">{lauf.kind}</td>
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
                    <button
                      type="button"
                      className="wg-button wg-button-klein wg-button-still"
                      onClick={() => onChange({ run: lauf.id })}
                    >
                      verfolgen
                    </button>
                    {(lauf.status === "queued" || lauf.status === "running") && (
                      <button
                        type="button"
                        className="wg-button wg-button-klein wg-button-gefahr"
                        onClick={() => abbrechen.mutate(lauf.id)}
                      >
                        abbrechen
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="wg-panel-titel">Modellnutzung</h2>
        <table className="wg-tabelle text-xs">
          <thead>
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
              <tr key={`${zeile.task}-${zeile.model}`}>
                <td>{zeile.task}</td>
                <td className="text-right tabular-nums">{zeile.calls}</td>
                <td className="text-right tabular-nums">{zeile.tokens_in}</td>
                <td className="text-right tabular-nums">{zeile.tokens_out}</td>
                <td className="text-right tabular-nums">{zeile.cost_estimate_eur.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="wg-panel-titel mt-3">Aufgelöste Routen</h3>
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
        <h2 className="wg-panel-titel">Bestand</h2>
        <table className="wg-tabelle text-xs">
          <thead>
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
              <tr key={eintrag.store}>
                <td>{eintrag.store}</td>
                <td className="text-right tabular-nums">{eintrag.concepts}</td>
                <td className="text-right tabular-nums">{eintrag.edges}</td>
                <td className="text-right tabular-nums">{eintrag.clusters}</td>
                <td className="text-right tabular-nums">{eintrag.loose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="wg-panel space-y-2">
        <h2 className="wg-panel-titel">Aufgelöste Konfiguration</h2>
        <p className="wg-hinweis">Secrets sind maskiert (§20.2).</p>
        <pre className="max-h-64 overflow-auto rounded bg-ton-900 p-3 font-mono text-2xs leading-relaxed text-ton-100">
          {JSON.stringify(konfiguration.data ?? {}, null, 2)}
        </pre>
      </section>
    </div>
  );
}
