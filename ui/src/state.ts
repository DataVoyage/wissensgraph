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
}

const STANDARD: UiState = { view: "graph", store: "shared" };

function lesen(): UiState {
  const params = new URLSearchParams(window.location.search);
  const view = (params.get("view") ?? STANDARD.view) as ViewName;
  return {
    view,
    store: params.get("store") ?? STANDARD.store,
    scope: params.get("scope") ?? undefined,
    id: params.get("id") ?? undefined,
    cluster: params.get("cluster") ?? undefined,
    q: params.get("q") ?? undefined,
    run: params.get("run") ?? undefined,
  };
}

function schreiben(zustand: UiState): void {
  const params = new URLSearchParams();
  for (const [name, wert] of Object.entries(zustand)) {
    if (wert !== undefined && wert !== "") {
      params.set(name, String(wert));
    }
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
