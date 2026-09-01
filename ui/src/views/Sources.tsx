/**
 * Verwalten → Quellen & Sync (§17.2 Ansicht 6, U5).
 *
 * Je Quelle: Health, letzter Lauf, und der Sync mit seinen beiden Optionen aus §19 —
 * `--full` (den gespeicherten Cursor ignorieren) und `--dry-run` (alles ausführen und am Ende
 * verwerfen). Damit ist `wg sync --source … [--full] [--dry-run]` vollständig in der UI.
 */

import { useState } from "react";

import { useSources, useStartRun } from "../api/hooks";
import { Fortschritt } from "../components/Fortschritt";
import { Fehler, Laden, Leer, Schaltflaeche } from "../components/basis";
import type { UiState } from "../state";

export interface SourcesProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function Sources({ state, onChange }: SourcesProps): JSX.Element {
  const quellen = useSources();
  const starten = useStartRun();
  const [full, setzeFull] = useState(false);
  const [probe, setzeProbe] = useState(false);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        <section className="wg-panel space-y-2" aria-label="Sync-Optionen">
          <h2 className="wg-panel-titel">Quellen &amp; Sync</h2>
          <div className="flex flex-wrap gap-4">
            <label className="wg-check">
              <input
                type="checkbox"
                checked={full}
                onChange={(ereignis) => setzeFull(ereignis.target.checked)}
              />
              Vollabgleich (full) — den gespeicherten Cursor ignorieren
            </label>
            <label className="wg-check">
              <input
                type="checkbox"
                checked={probe}
                onChange={(ereignis) => setzeProbe(ereignis.target.checked)}
              />
              Trockenlauf (dry_run) — alles ausführen, am Ende verwerfen
            </label>
          </div>
          {starten.isError && <Fehler>{starten.error.message}</Fehler>}
        </section>

        <section className="wg-panel space-y-1" aria-label="Quellen">
          {quellen.isPending && <Laden was="Quellen werden geladen" />}
          {quellen.data?.items.length === 0 && (
            <Leer titel="Keine Quelle eingeschaltet.">
              Quellen stehen in <code>sources.yaml</code> (§8.4) — ohne Zugangsdaten läuft der
              Mock-Quellserver.
            </Leer>
          )}
          <ul className="space-y-1 text-sm">
            {(quellen.data?.items ?? []).map((quelle) => (
              <li key={quelle.name} className="flex items-center gap-2 rounded px-1 py-1.5 hover:bg-ton-50">
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${
                    quelle.usable ? "bg-emerald-500" : "bg-signal-500"
                  }`}
                  aria-label={quelle.usable ? "benutzbar" : "nicht benutzbar"}
                />
                <span className="min-w-0 flex-1 truncate font-medium text-ton-800">
                  {quelle.name}
                </span>
                <span className="text-xs tabular-nums text-ton-500">
                  {quelle.last_run
                    ? `${quelle.last_run.status} · ${quelle.last_run.started_at?.slice(0, 16) ?? ""}`
                    : "noch kein Lauf"}
                </span>
                <Schaltflaeche
                  klein
                  disabled={!quelle.usable}
                  beschaeftigt={starten.isPending}
                  onClick={() =>
                    starten.mutate(
                      {
                        kind: "sync",
                        body: { source: quelle.name, full, dry_run: probe } as never,
                      },
                      { onSuccess: (lauf) => onChange({ run: lauf.id }) },
                    )
                  }
                >
                  Sync
                </Schaltflaeche>
              </li>
            ))}
          </ul>
          {state.run !== undefined && <Fortschritt runId={state.run} />}
        </section>
      </div>
    </div>
  );
}
