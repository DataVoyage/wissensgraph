/**
 * Die Hülle der Oberfläche — sechs Ansichten, ein Zustand, keine Fachlogik (§17.1, §17.2).
 *
 * Die Fachregeln kommen aus `/api/v1/config/effective` und `/api/v1/models`. Solange die
 * Konfiguration nicht geladen ist, zeigt diese Komponente deshalb *keine* Ansicht — nicht aus
 * Vorsicht, sondern weil sie ohne sie nicht wüsste, welche Scopes, Typen und Kantenarten es
 * überhaupt gibt (§17.1). Eine Vorbelegung wäre genau die eingebaute Fachregel, die dort
 * ausgeschlossen ist.
 */

import { useEffect, useState } from "react";

import { configure } from "./api/client";
import { useConfig } from "./api/hooks";
import { loadConfig } from "./config";
import { useUiState, type ViewName } from "./state";
import { ClusterWorkbench } from "./views/ClusterWorkbench";
import { CurationList } from "./views/CurationList";
import { DocumentBrowser } from "./views/DocumentBrowser";
import { GraphExplorer } from "./views/GraphExplorer";
import { Operations } from "./views/Operations";
import { PersonalArea } from "./views/PersonalArea";

const ANSICHTEN: Array<{ name: ViewName; label: string }> = [
  { name: "graph", label: "Graph" },
  { name: "browser", label: "Dokumente" },
  { name: "cluster", label: "Cluster" },
  { name: "kuration", label: "Kuration" },
  { name: "persoenlich", label: "Persönlich" },
  { name: "betrieb", label: "Betrieb" },
];

/** Schlüssel, unter dem der Bearer-Token in der Sitzung liegt (§17.1). */
const TOKEN_KEY = "wg.token";

export function App(): JSX.Element {
  const laufzeit = loadConfig();
  const [token, setzeToken] = useState<string>(
    () => window.sessionStorage.getItem(TOKEN_KEY) ?? "",
  );

  // Der Client wird vor dem ersten Aufruf eingerichtet — auch beim allerersten Rendern, deshalb
  // direkt und nicht in einem Effekt.
  configure({ baseUrl: laufzeit.apiBaseUrl, token: token || null });
  useEffect(() => {
    configure({ baseUrl: laufzeit.apiBaseUrl, token: token || null });
    if (token) {
      window.sessionStorage.setItem(TOKEN_KEY, token);
    }
  }, [laufzeit.apiBaseUrl, token]);

  const [zustand, aendern] = useUiState();
  const konfiguration = useConfig();

  if (konfiguration.isError) {
    return (
      <main className="p-4">
        <h1 className="text-lg font-semibold">Wissensgraph</h1>
        <p role="alert" className="mt-2 text-sm text-red-700">
          Die Konfiguration ließ sich nicht laden: {konfiguration.error.message}
        </p>
        <label className="mt-2 block text-sm">
          Bearer-Token
          <input
            className="wg-input"
            aria-label="Bearer-Token"
            type="password"
            defaultValue={token}
            onBlur={(ereignis) => setzeToken(ereignis.target.value)}
          />
        </label>
      </main>
    );
  }

  if (konfiguration.data === undefined) {
    return (
      <main className="p-4">
        <p role="status">Konfiguration wird geladen …</p>
      </main>
    );
  }

  const config = konfiguration.data;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-3 border-b px-3">
        <h1 className="text-base font-semibold">Wissensgraph</h1>
        <nav className="flex" aria-label="Ansichten">
          {ANSICHTEN.map((eintrag) => (
            <button
              key={eintrag.name}
              type="button"
              className={`wg-tab ${zustand.view === eintrag.name ? "wg-tab-active" : ""}`}
              aria-current={zustand.view === eintrag.name ? "page" : undefined}
              onClick={() => aendern({ view: eintrag.name })}
            >
              {eintrag.label}
            </button>
          ))}
        </nav>
        <label className="ml-auto text-sm">
          Store
          <select
            className="wg-input ml-1 w-32"
            aria-label="Store"
            value={zustand.store}
            onChange={(ereignis) => aendern({ store: ereignis.target.value, id: undefined })}
          >
            {Object.keys(config.stores).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </header>

      <main className="min-h-0 flex-1 p-3">
        {zustand.view === "graph" && (
          <GraphExplorer
            state={zustand}
            onChange={aendern}
            edgeKinds={[...config.edge_kinds.structural, ...config.edge_kinds.semantic]}
          />
        )}
        {zustand.view === "browser" && (
          <DocumentBrowser state={zustand} onChange={aendern} config={config} />
        )}
        {zustand.view === "cluster" && (
          <ClusterWorkbench state={zustand} onChange={aendern} />
        )}
        {zustand.view === "kuration" && <CurationList state={zustand} />}
        {zustand.view === "persoenlich" && (
          <PersonalArea state={zustand} onChange={aendern} config={config} />
        )}
        {zustand.view === "betrieb" && <Operations state={zustand} onChange={aendern} />}
      </main>
    </div>
  );
}
