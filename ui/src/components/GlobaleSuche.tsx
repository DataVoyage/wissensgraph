/**
 * Die globale Suche in der Kopfzeile (§17.5) — der Einstieg des Anwenders.
 *
 * "Was haben wir zu X?" braucht kein Wissen über Reiter: `/` fokussiert das Feld von überall,
 * die Suche läuft zweistufig über `/graph/search` (§12.4 — erst Themen, dann Dokumente, mit
 * Rückfall auf Volltext), und jeder Treffer bietet die beiden Antworten an, die man auf ein
 * Suchergebnis geben kann: **lesen** (Dokumente-Ansicht) oder **im Graphen ansehen**
 * (Traversierung vom Treffer aus).
 *
 * Die Trefferliste ist Tastatur-erst: Pfeile wählen, Enter öffnet, Escape schließt (§17.2
 * Ansicht 4 gilt überall, nicht nur in der Kurationsliste).
 */

import { useEffect, useRef, useState } from "react";

import { useSearch } from "../api/hooks";
import type { GraphNode } from "../api/types";
import { farbeFuerTyp } from "../theme";

export interface GlobaleSucheProps {
  store: string;
  /** Die Typreihenfolge der Installation — für die Farbmarke am Treffer. */
  typen: readonly string[];
  onOeffnen: (ziel: { id: string; store: string; wohin: "lesen" | "graph" }) => void;
}

export function GlobaleSuche({ store, typen, onOeffnen }: GlobaleSucheProps): JSX.Element {
  const suchen = useSearch();
  const feld = useRef<HTMLInputElement | null>(null);
  const [offen, setzeOffen] = useState(false);
  const [aktiv, setzeAktiv] = useState(0);

  // `/` fokussiert die Suche von überall — außer wenn gerade getippt wird.
  useEffect(() => {
    const beimTippen = (ereignis: KeyboardEvent): void => {
      const ziel = ereignis.target as HTMLElement | null;
      const tippt =
        ziel !== null && (ziel.tagName === "INPUT" || ziel.tagName === "TEXTAREA" || ziel.isContentEditable);
      if (ereignis.key === "/" && !tippt) {
        ereignis.preventDefault();
        feld.current?.focus();
        feld.current?.select();
      }
    };
    window.addEventListener("keydown", beimTippen);
    return () => window.removeEventListener("keydown", beimTippen);
  }, []);

  const treffer: GraphNode[] = offen ? (suchen.data?.hits ?? []) : [];

  const oeffnen = (eintrag: GraphNode, wohin: "lesen" | "graph"): void => {
    setzeOffen(false);
    onOeffnen({ id: eintrag.id, store: eintrag.store, wohin });
  };

  const abschicken = (): void => {
    const query = feld.current?.value.trim() ?? "";
    if (query === "") {
      return;
    }
    setzeAktiv(0);
    setzeOffen(true);
    suchen.mutate({ query, store, granularity: "auto" });
  };

  return (
    <div className="relative w-full max-w-md">
      <input
        ref={feld}
        type="search"
        role="searchbox"
        aria-label="Globale Suche"
        placeholder='Suchen …  ( / )'
        className="wg-input py-1"
        onKeyDown={(ereignis) => {
          if (ereignis.key === "Enter") {
            if (offen && treffer.length > 0) {
              oeffnen(treffer[Math.min(aktiv, treffer.length - 1)] as GraphNode, "lesen");
            } else {
              abschicken();
            }
          } else if (ereignis.key === "Escape") {
            setzeOffen(false);
          } else if (ereignis.key === "ArrowDown" && treffer.length > 0) {
            ereignis.preventDefault();
            setzeAktiv((vorher) => Math.min(treffer.length - 1, vorher + 1));
          } else if (ereignis.key === "ArrowUp" && treffer.length > 0) {
            ereignis.preventDefault();
            setzeAktiv((vorher) => Math.max(0, vorher - 1));
          } else if (offen) {
            // Weitertippen heißt: neue Frage — die alte Liste soll nicht darunter kleben.
            setzeOffen(false);
          }
        }}
      />

      {offen && (
        <div className="absolute inset-x-0 top-full z-30 mt-1 max-h-96 overflow-y-auto rounded-lg border border-ton-200 bg-ton-0 p-1 shadow-schwebend">
          {suchen.isPending && (
            <p role="status" className="px-2 py-1.5 text-xs text-ton-500">
              Es wird gesucht …
            </p>
          )}
          {suchen.isError && (
            <p role="alert" className="wg-fehler m-1">
              {suchen.error.message}
            </p>
          )}
          {suchen.isSuccess && treffer.length === 0 && (
            <p className="px-2 py-1.5 text-xs text-ton-500">
              Nichts gefunden — auch nicht im Volltext.
            </p>
          )}
          {treffer.length > 0 && (
            <>
              <p className="px-2 pb-1 pt-1.5 text-2xs uppercase tracking-wider text-ton-400">
                {suchen.data?.mode === "lexical" ? "Volltext-Rückfall (§12.4)" : "zweistufig (§12.4)"}
                — Enter öffnet, Pfeile wählen
              </p>
              <ul role="listbox" aria-label="Suchtreffer">
                {treffer.map((eintrag, platz) => (
                  <li key={eintrag.id} role="option" aria-selected={platz === aktiv}>
                    <div
                      className={`flex items-center gap-2 rounded px-2 py-1.5 ${
                        platz === aktiv ? "bg-ton-100" : "hover:bg-ton-50"
                      }`}
                    >
                      <span
                        aria-hidden="true"
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: farbeFuerTyp(eintrag.type, typen) }}
                      />
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate text-left text-sm text-ton-800"
                        onClick={() => oeffnen(eintrag, "lesen")}
                      >
                        {eintrag.title ?? eintrag.id}
                        <span className="ml-1.5 text-2xs text-ton-400">{eintrag.type}</span>
                      </button>
                      <button
                        type="button"
                        className="wg-button wg-button-klein wg-button-still shrink-0"
                        title="Im Graphen ansehen — Traversierung von hier aus"
                        onClick={() => oeffnen(eintrag, "graph")}
                      >
                        Graph
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
