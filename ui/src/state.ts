/**
 * Der lokale Zustand der Oberfläche — in der URL, nicht im Speicher (§17.1).
 *
 * "Lokaler UI-Zustand über URL-Parameter, damit Ansichten teilbar sind." Das ist keine
 * Bequemlichkeit: Eine Kuration besprechen heißt, jemandem *dieselbe* Ansicht zu zeigen. Ein
 * Zustand in einem React-State wäre nicht verlinkbar, und der Kollege müsste sich zu derselben
 * Stelle durchklicken.
 */

import { useCallback, useEffect, useState } from "react";

/** Die sechs Ansichten aus §17.2. */
export type ViewName =
  | "graph"
  | "browser"
  | "cluster"
  | "kuration"
  | "persoenlich"
  | "betrieb";

/**
 * Die zwei Arten, den Graphen anzusehen (§17.2 Ansicht 1).
 *
 * `karte` zeigt den gefilterten Bestand ohne Ausgangspunkt — der Überblick. `reise` klappt Hop
 * für Hop auf — die Erkundung. Beides ist Ansicht 1 und keine zwei Ansichten: Es ist derselbe
 * Graph, einmal aus der Vogelperspektive und einmal zu Fuß.
 */
export type GraphMode = "karte" | "reise";

/** Der vollständige Zustand einer Ansicht, wie er in der URL steht. */
export interface UiState {
  view: ViewName;
  store: string;
  scope?: string;
  /** Der ausgewählte Knoten — Ausgangspunkt von Graph, Detail und Panel. */
  id?: string;
  cluster?: string;
  q?: string;
  run?: string;
  /** Karte oder Traversierung; ohne Angabe die Karte. */
  mode?: GraphMode;
  type?: string;
  status?: string;
  /** Die gewählten Kantenarten, kommagetrennt — leer heißt "alle". */
  kinds?: string;
  unverified?: boolean;
  orphan?: boolean;
  /** Ob Grabsteine mitgezeigt werden (§17.2, Filterleiste). */
  tombstones?: boolean;
}

const STANDARD: UiState = { view: "graph", store: "shared" };

/**
 * Die Wahrheitswerte des Zustands.
 *
 * Sie brauchen eine eigene Behandlung, weil eine URL nur Zeichenketten kennt: `"false"` ist eine
 * nicht-leere Zeichenkette und damit wahr, sobald man sie unbesehen übernimmt. Geschrieben wird
 * deshalb nur der wahre Fall, und zwar als `1`; alles andere fehlt schlicht.
 */
const FLAGGEN = ["unverified", "orphan", "tombstones"] as const;

function lesen(): UiState {
  const params = new URLSearchParams(window.location.search);
  const view = (params.get("view") ?? STANDARD.view) as ViewName;
  const flaggen = Object.fromEntries(
    FLAGGEN.map((name) => [name, params.get(name) === "1" ? true : undefined]),
  );
  return {
    view,
    store: params.get("store") ?? STANDARD.store,
    scope: params.get("scope") ?? undefined,
    id: params.get("id") ?? undefined,
    cluster: params.get("cluster") ?? undefined,
    q: params.get("q") ?? undefined,
    run: params.get("run") ?? undefined,
    mode: (params.get("mode") as GraphMode | null) ?? undefined,
    type: params.get("type") ?? undefined,
    status: params.get("status") ?? undefined,
    kinds: params.get("kinds") ?? undefined,
    ...flaggen,
  };
}

function schreiben(zustand: UiState): void {
  const params = new URLSearchParams();
  for (const [name, wert] of Object.entries(zustand)) {
    if (wert === undefined || wert === "" || wert === false) {
      continue;
    }
    params.set(name, wert === true ? "1" : String(wert));
  }
  const ziel = `${window.location.pathname}?${params.toString()}`;
  window.history.pushState(zustand, "", ziel);
}

/**
 * Liest und schreibt den Ansichtszustand über die Adressleiste.
 *
 * `popstate` wird mitgehört, damit der Zurück-Knopf des Browsers funktioniert. Ohne ihn wäre die
 * URL zwar teilbar, aber die Navigation innerhalb der Oberfläche verhielte sich anders als jede
 * andere Webseite.
 */
export function useUiState(): [UiState, (aenderung: Partial<UiState>) => void] {
  const [zustand, setzeZustand] = useState<UiState>(lesen);

  useEffect(() => {
    const beimZurueck = (): void => setzeZustand(lesen());
    window.addEventListener("popstate", beimZurueck);
    return () => window.removeEventListener("popstate", beimZurueck);
  }, []);

  const aendern = useCallback((aenderung: Partial<UiState>) => {
    setzeZustand((vorher) => {
      const neu = { ...vorher, ...aenderung };
      schreiben(neu);
      return neu;
    });
  }, []);

  return [zustand, aendern];
}
