/**
 * Der Live-Fortschritt eines Laufs (§16.3) — aus der Betriebsansicht herausgelöst, weil ihn
 * seit U4 auch die Automatisierung braucht.
 *
 * Er hängt an `EventSource` und nicht an einem Abfrageintervall: §17.3 verlangt "Läufe
 * blockieren die UI nie", und ein Strom, den der Server schließt, sagt der Oberfläche von
 * selbst, wann sie aufhören darf zu warten.
 */

import { useEffect, useState } from "react";

import { subscribeToRun } from "../api/events";
import type { Run } from "../api/types";

export interface FortschrittProps {
  runId: string;
  /** Wird gerufen, sobald der Lauf endgültig ist — mit seinem letzten Stand samt `stats`. */
  onFertig?: (lauf: Run) => void;
}

const ENDGUELTIG = new Set(["succeeded", "failed", "cancelled"]);

export function Fortschritt({ runId, onFertig }: FortschrittProps): JSX.Element {
  const [lauf, setzeLauf] = useState<Run | null>(null);
  const [fehler, setzeFehler] = useState<string | null>(null);

  useEffect(() => {
    setzeLauf(null);
    setzeFehler(null);
    let gemeldet = false;
    return subscribeToRun(runId, (ereignis) => {
      if (ereignis.kind === "error") {
        setzeFehler(ereignis.detail);
        return;
      }
      setzeLauf(ereignis.run);
      if (!gemeldet && ENDGUELTIG.has(ereignis.run.status)) {
        gemeldet = true;
        onFertig?.(ereignis.run);
      }
    });
    // `onFertig` bewusst nicht in den Abhängigkeiten: Eine neue Funktionsidentität je Render
    // würde den Strom je Render neu aufbauen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return (
    <div
      data-testid="fortschritt"
      className="animate-einblenden rounded-lg border border-ton-200 bg-ton-50 p-3 text-xs"
    >
      <p className="flex items-center gap-2">
        <span className="wg-chip">{runId.slice(0, 8)}</span>
        <span className="font-medium text-ton-800">{lauf?.status ?? "verbinde …"}</span>
        <span className="ml-auto tabular-nums text-ton-500">
          {lauf === null ? "" : `${Math.round(lauf.progress * 100)} %`}
        </span>
      </p>
      {lauf !== null && (
        <>
          {/* Ein eigener Balken statt `<progress>`: Der Fortschritt ist die eine Stelle, an der
              diese Oberfläche wartet — und ein Element, dessen Aussehen jeder Browser selbst
              bestimmt, passt dort am wenigsten. Das `<progress>` bleibt unsichtbar bestehen,
              damit Screenreader weiterhin einen Fortschritt vorfinden. */}
          <span className="mt-2 block h-1.5 overflow-hidden rounded bg-ton-200">
            <span
              className="block h-full rounded bg-signal-500 transition-all duration-ruhig"
              style={{ width: `${Math.round(lauf.progress * 100)}%` }}
            />
          </span>
          <progress className="sr-only" value={lauf.progress} max={1} />
          {Object.keys(lauf.stats).length > 0 && (
            <pre className="max-h-32 overflow-auto">{JSON.stringify(lauf.stats, null, 2)}</pre>
          )}
          {lauf.error !== null && <p className="wg-fehler mt-2">{lauf.error}</p>}
        </>
      )}
      {fehler !== null && (
        <p role="alert" className="wg-fehler mt-2">
          {fehler}
        </p>
      )}
    </div>
  );
}
