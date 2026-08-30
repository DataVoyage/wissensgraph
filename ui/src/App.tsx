/**
 * Wurzelkomponente der SPA.
 *
 * Stufe 0 verlangt eine "leere SPA mit Verbindungsanzeige" (§24). Mehr steht hier bewusst nicht:
 * Die eigentlichen Ansichten aus §17.2 kommen mit Stufe 11, und eine vorgezogene Halbversion
 * wäre nur Ballast.
 */

import { useEffect, useState } from "react";
import { fetchReadyz, type ConnectionState } from "./api";
import { loadConfig } from "./config";

/** Wie oft die Verbindung erneut geprüft wird, solange sie nicht steht (Millisekunden). */
const RETRY_INTERVAL_MS = 3000;

export function App(): JSX.Element {
  const [state, setState] = useState<ConnectionState>({ kind: "pruefe" });
  const { apiBaseUrl } = loadConfig();

  useEffect(() => {
    let abgebrochen = false;

    async function pruefen(): Promise<void> {
      const ergebnis = await fetchReadyz(apiBaseUrl);
      if (!abgebrochen) {
        setState(ergebnis);
      }
    }

    void pruefen();
    // Solange die API nicht bereit ist, weiter versuchen: Beim Hochfahren des Stacks startet die
    // UI unabhängig von der API (§5.5) und soll den Zustand von selbst einholen.
    const timer = window.setInterval(() => void pruefen(), RETRY_INTERVAL_MS);
    return () => {
      abgebrochen = true;
      window.clearInterval(timer);
    };
  }, [apiBaseUrl]);

  return (
    <main>
      <h1>Wissensgraph</h1>
      <ConnectionBanner state={state} apiBaseUrl={apiBaseUrl} />
    </main>
  );
}

function ConnectionBanner({
  state,
  apiBaseUrl,
}: {
  state: ConnectionState;
  apiBaseUrl: string;
}): JSX.Element {
  const ziel = apiBaseUrl || "(gleicher Ursprung)";

  switch (state.kind) {
    case "pruefe":
      return <p role="status">Verbindung zur API wird geprüft …</p>;

    case "verbunden":
      return (
        <section role="status">
          <p>Verbunden mit {ziel}. Beide Stores sind erreichbar.</p>
          <StoreList stores={state.stores} />
        </section>
      );

    case "nicht_bereit":
      return (
        <section role="alert">
          <p>Die API antwortet, ist aber nicht bereit — mindestens ein Store fehlt.</p>
          <StoreList stores={state.stores} />
        </section>
      );

    case "unerreichbar":
      return (
        <section role="alert">
          <p>Keine Verbindung zu {ziel}.</p>
          <p>{state.grund}</p>
        </section>
      );
  }
}

function StoreList({ stores }: { stores: ReadonlyArray<{ store: string; healthy: boolean }> }) {
  return (
    <ul>
      {stores.map((store) => (
        <li key={store.store}>
          {store.store}: {store.healthy ? "erreichbar" : "nicht erreichbar"}
        </li>
      ))}
    </ul>
  );
}
