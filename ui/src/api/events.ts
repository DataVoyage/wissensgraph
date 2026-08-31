/**
 * Der Live-Fortschritt eines Laufs über Server-Sent Events (§16.3, §17.3).
 *
 * `EventSource` und kein Polling: §17.3 verlangt "Läufe blockieren die UI nie", und ein Strom,
 * den der Server schließt, sobald der Lauf endet, sagt der Oberfläche von selbst, wann sie
 * aufhören darf zu warten.
 *
 * `EventSource` kann keine eigenen Header setzen — bei `auth_mode: token` wandert der Token
 * deshalb als Query-Parameter mit. Das ist bewusst die schwächere Variante und nur für diesen
 * einen, ausschließlich lesenden Endpunkt: Ein Token in einer URL landet in Zugriffslogs.
 */

import { options } from "./client";
import type { Run } from "./types";

/** Was der Aufrufer über einen Lauf erfährt, während er läuft. */
export type RunEvent =
  | { kind: "progress"; run: Run }
  | { kind: "done"; run: Run }
  | { kind: "error"; detail: string };

/**
 * Abonniert den Fortschritt eines Laufs.
 *
 * @returns Eine Funktion, die das Abonnement beendet. Sie muss beim Aufräumen der Komponente
 *   aufgerufen werden: Ein offener Strom hält eine Verbindung, auch wenn niemand mehr hinsieht.
 */
export function subscribeToRun(runId: string, beim: (ereignis: RunEvent) => void): () => void {
  const { baseUrl } = options();
  const quelle = new EventSource(`${baseUrl}/api/v1/runs/${runId}/events`);

  const lesen = (kind: "progress" | "done") => (ereignis: MessageEvent<string>) => {
    beim({ kind, run: JSON.parse(ereignis.data) as Run });
    if (kind === "done") {
      quelle.close();
    }
  };

  quelle.addEventListener("progress", lesen("progress") as EventListener);
  quelle.addEventListener("done", lesen("done") as EventListener);
  quelle.addEventListener("error", ((ereignis: MessageEvent<string>) => {
    // Zwei verschiedene Dinge heißen hier "error": ein Ereignis des Servers mit Nutzlast und
    // ein Verbindungsabbruch ohne. Nur das erste ist eine Aussage über den Lauf.
    const detail = ereignis.data
      ? (JSON.parse(ereignis.data) as { detail?: string }).detail
      : "Verbindung zum Fortschrittsstrom verloren.";
    beim({ kind: "error", detail: detail ?? "Unbekannter Fehler." });
    quelle.close();
  }) as EventListener);

  return () => quelle.close();
}
