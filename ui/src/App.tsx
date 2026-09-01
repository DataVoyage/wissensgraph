/**
 * Die Hülle der Oberfläche — drei Arbeitsbereiche, ein Zustand, keine Fachlogik (§17.1, §17.5).
 *
 * Die Fachregeln kommen aus `/api/v1/config/effective`. Solange die Konfiguration nicht geladen
 * ist, zeigt diese Komponente deshalb *keine* Ansicht — nicht aus Vorsicht, sondern weil sie ohne
 * sie nicht wüsste, welche Scopes, Typen und Kantenarten es überhaupt gibt (§17.1). Eine
 * Vorbelegung wäre genau die eingebaute Fachregel, die dort ausgeschlossen ist.
 *
 * Das Gerüst (§17.5): Navigationsleiste links mit den drei Bereichen, Kopfzeile mit
 * Bereichstitel und Store-Wahl, Hauptfläche. Der Store steht in der Kopfzeile und nicht in einer
 * Ansicht, weil er über allen liegt: Er entscheidet, *welcher Bestand* gemeint ist. Und weil
 * §17.2 (Ansicht 5) eine "deutliche visuelle Trennung" verlangt, ist der persönliche Store nicht
 * nur ausgewählt, sondern angeschrieben — mit dem Satz, auf den es ankommt: Diese Daten
 * verlassen den Rechner nicht (Leitprinzip 2).
 */

import { useEffect, useState } from "react";

import { configure } from "./api/client";
import { useConfig, useQueue } from "./api/hooks";
import { bereichVon } from "./bereiche";
import { loadConfig } from "./config";
import { GlobaleSuche } from "./components/GlobaleSuche";
import { NavRail } from "./components/NavRail";
import { useUiState } from "./state";
import { useWerkbank } from "./werkbank";
import { Automation } from "./views/Automation";
import { ClusterWorkbench } from "./views/ClusterWorkbench";
import { Diagnose } from "./views/Diagnose";
import { Models } from "./views/Models";
import { Quality } from "./views/Quality";
import { RunsView } from "./views/RunsView";
import { Sources } from "./views/Sources";
import { CurationList } from "./views/CurationList";
import { DocumentBrowser } from "./views/DocumentBrowser";
import { GraphExplorer } from "./views/GraphExplorer";
import { PersonalArea } from "./views/PersonalArea";

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
  const [werkbank, werkbankAendern] = useWerkbank();
  const konfiguration = useConfig();
  // Die offenen Posten stehen an der Navigation, nicht erst in der Ansicht. Eine Warteschlange,
  // die man erst sieht, wenn man hinsieht, wächst unbemerkt (§17.2 Ansicht 4).
  const warteschlange = useQueue(zustand.store);

  if (konfiguration.isError) {
    return (
      <main className="mx-auto mt-24 w-full max-w-md">
        <div className="wg-panel">
          <h1 className="text-lg font-semibold text-ton-900">Wissensgraph</h1>
          <p role="alert" className="wg-fehler mt-2">
            Die Konfiguration ließ sich nicht laden: {konfiguration.error.message}
          </p>
          <label className="mt-3 block">
            <span className="wg-label">Bearer-Token</span>
            <input
              className="wg-input"
              aria-label="Bearer-Token"
              type="password"
              defaultValue={token}
              onBlur={(ereignis) => setzeToken(ereignis.target.value)}
            />
          </label>
        </div>
      </main>
    );
  }

  if (konfiguration.data === undefined) {
    return (
      <main className="flex h-screen items-center justify-center">
        <p role="status" className="text-sm text-ton-500">
          Konfiguration wird geladen …
        </p>
      </main>
    );
  }

  const config = konfiguration.data;
  const persoenlich = zustand.store === "personal";
  const offen = warteschlange.data?.items.length ?? 0;
  const bereich = bereichVon(zustand.view);

  return (
    <div className="flex h-screen">
      <NavRail
        aktiv={zustand.view}
        breit={werkbank.railBreit}
        offeneKuration={offen}
        onNavigieren={(view) => aendern({ view })}
        onBreite={(railBreit) => werkbankAendern({ railBreit })}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className={`flex items-center gap-4 border-b bg-ton-0 px-4 shadow-karte ${
            persoenlich ? "border-b-2 border-ton-700" : "border-ton-200"
          }`}
        >
          <h1 className="flex shrink-0 items-center gap-2 py-2.5 text-base font-semibold tracking-tight text-ton-900">
            <span aria-hidden="true" className="h-4 w-1.5 rounded-sm bg-signal-500" />
            Wissensgraph
          </h1>
          <p className="shrink-0 text-sm text-ton-400">
            {bereich.label}
            <span className="mx-1.5 text-ton-300">/</span>
            <span className="font-medium text-ton-700">
              {bereich.ansichten.find((eintrag) => eintrag.name === zustand.view)?.label}
            </span>
          </p>

          {/* Der Einstieg des Anwenders: "Was haben wir zu X?" — von überall, ohne Reiterwissen. */}
          <div className="mx-2 hidden min-w-0 flex-1 justify-center md:flex">
            <GlobaleSuche
              store={zustand.store}
              typen={config.concept_types.map((eintrag) => eintrag.name)}
              onOeffnen={({ id, store, wohin }) =>
                aendern(
                  wohin === "graph"
                    ? { view: "graph", mode: "reise", id, store }
                    : { view: "browser", id, store },
                )
              }
            />
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            {persoenlich && (
              <span className="hidden rounded border border-ton-300 bg-ton-100 px-2 py-1 text-2xs font-medium text-ton-600 lg:inline">
                verlässt diesen Rechner nicht
              </span>
            )}
            <label className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-ton-500">
              Store
              <select
                className="wg-input w-32 py-1 text-sm normal-case tracking-normal"
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
          </div>
        </header>

        <main className="min-h-0 flex-1 p-3">
          {zustand.view === "graph" && (
            <GraphExplorer
              state={zustand}
              onChange={aendern}
              config={config}
              werkbank={werkbank}
              onWerkbank={werkbankAendern}
            />
          )}
          {zustand.view === "browser" && (
            <DocumentBrowser
              state={zustand}
              onChange={aendern}
              config={config}
              werkbank={werkbank}
              onWerkbank={werkbankAendern}
            />
          )}
          {zustand.view === "cluster" && <ClusterWorkbench state={zustand} onChange={aendern} />}
          {zustand.view === "kuration" && <CurationList state={zustand} />}
          {zustand.view === "automatisierung" && (
            <Automation state={zustand} onChange={aendern} />
          )}
          {zustand.view === "qualitaet" && <Quality state={zustand} onChange={aendern} />}
          {zustand.view === "persoenlich" && (
            <PersonalArea
              state={zustand}
              onChange={aendern}
              config={config}
              werkbank={werkbank}
              onWerkbank={werkbankAendern}
            />
          )}
          {zustand.view === "quellen" && <Sources state={zustand} onChange={aendern} />}
          {zustand.view === "laeufe" && <RunsView state={zustand} onChange={aendern} />}
          {zustand.view === "modelle" && <Models state={zustand} onChange={aendern} />}
          {zustand.view === "diagnose" && <Diagnose state={zustand} onChange={aendern} />}
        </main>
      </div>
    </div>
  );
}
