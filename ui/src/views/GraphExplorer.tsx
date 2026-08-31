/**
 * Ansicht 1 — Graph-Explorer (§17.2).
 *
 * Der Einstieg ist die Kernspace-Übersicht und keine Suche: §18.2 begründet das für den Agenten,
 * und für den Menschen gilt dasselbe — wer eine Sammlung nicht kennt, kann sie nicht durchsuchen.
 *
 * Aufgeklappt wird Hop für Hop über `/graph/neighbors` (§17.2: "kein Vorabladen des
 * Gesamtgraphen"). Der aufgeklappte Ausschnitt wächst dabei, statt ersetzt zu werden — sonst
 * verlöre ein Klick den Weg, über den man gekommen ist.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { get } from "../api/client";
import { useClusters, useConcept, useSearch } from "../api/hooks";
import type { Edge, GraphNode, Traversal } from "../api/types";
import { ConceptPanel } from "../components/ConceptPanel";
import { GraphCanvas, type LayoutName } from "../components/GraphCanvas";
import type { UiState } from "../state";

export interface GraphExplorerProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  /** Die Kantenarten aus der Konfiguration — die Oberfläche kennt keine eigenen (§17.1). */
  edgeKinds: string[];
}

interface Ausschnitt {
  nodes: Map<string, GraphNode>;
  edges: Map<string, Edge>;
}

const LEER: Ausschnitt = { nodes: new Map(), edges: new Map() };

export function GraphExplorer({ state, onChange, edgeKinds }: GraphExplorerProps): JSX.Element {
  const [ausschnitt, setzeAusschnitt] = useState<Ausschnitt>(LEER);
  const [layout, setzeLayout] = useState<LayoutName>("cose");
  const [arten, setzeArten] = useState<Set<string>>(new Set());
  const [nurUnbestaetigt, setzeNurUnbestaetigt] = useState(false);
  const [fehler, setzeFehler] = useState<string | null>(null);

  const cluster = useClusters(state.store, state.scope);
  const detail = useConcept(state.id ?? null, state.store);
  const suchen = useSearch();

  const aufnehmen = useCallback((ergebnis: Traversal) => {
    setzeAusschnitt((vorher) => {
      const nodes = new Map(vorher.nodes);
      const edges = new Map(vorher.edges);
      for (const knoten of ergebnis.nodes) {
        nodes.set(knoten.id, knoten);
      }
      for (const kante of ergebnis.edges) {
        edges.set(kante.id, kante);
      }
      return { nodes, edges };
    });
  }, []);

  const aufklappen = useCallback(
    async (id: string, store: string) => {
      try {
        aufnehmen(
          await get<Traversal>(`/api/v1/graph/neighbors/${encodeURI(id)}`, { store }),
        );
        setzeFehler(null);
      } catch (ausnahme) {
        setzeFehler(ausnahme instanceof Error ? ausnahme.message : String(ausnahme));
      }
    },
    [aufnehmen],
  );

  // Ein Knoten aus der URL wird beim Öffnen aufgeklappt: Ein geteilter Link soll denselben
  // Ausschnitt zeigen und nicht eine leere Fläche mit einem markierten Punkt.
  useEffect(() => {
    if (state.id !== undefined) {
      void aufklappen(state.id, state.store);
    }
  }, [state.id, state.store, aufklappen]);

  const sichtbar = useMemo(() => {
    const kanten = [...ausschnitt.edges.values()].filter((kante) => {
      if (arten.size > 0 && !arten.has(kante.kind)) {
        return false;
      }
      if (!nurUnbestaetigt) {
        return true;
      }
      return kante.generated_by !== null && !kante.curated && kante.verified_at === null;
    });
    return { knoten: [...ausschnitt.nodes.values()], kanten };
  }, [ausschnitt, arten, nurUnbestaetigt]);

  return (
    <div className="grid h-full grid-cols-[220px_1fr_320px] gap-3">
      <div className="wg-panel space-y-3 overflow-y-auto">
        <section>
          <h2 className="text-sm font-semibold">Kernspace-Übersicht</h2>
          <p className="text-xs text-slate-500">Der Einstieg — kein Suchfeld (§17.2).</p>
          <ul className="mt-2 space-y-1 text-sm">
            {(cluster.data?.items ?? []).map((eintrag) => (
              <li key={eintrag.id}>
                <button
                  type="button"
                  className="text-left underline"
                  onClick={() => onChange({ id: eintrag.id })}
                >
                  {eintrag.title ?? eintrag.id}{" "}
                  <span className="text-xs text-slate-500">({eintrag.member_count})</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2 className="text-sm font-semibold">Suche</h2>
          <p className="text-xs text-slate-500">Der Weg, wenn die Übersicht nicht hilft (§12.4).</p>
          <form
            className="mt-1 space-y-1"
            onSubmit={(ereignis) => {
              ereignis.preventDefault();
              const query = String(new FormData(ereignis.currentTarget).get("q") ?? "");
              suchen.mutate(
                { query, store: state.store, granularity: "auto" },
                {
                  onSuccess: (ergebnis) => {
                    aufnehmen({
                      start: [],
                      nodes: ergebnis.hits,
                      edges: [],
                      hops: 0,
                      truncated: false,
                      queries: 0,
                    });
                    onChange({ q: query });
                  },
                },
              );
            }}
          >
            <input className="wg-input" name="q" aria-label="Suchbegriff" defaultValue={state.q} />
            <button type="submit" className="wg-button">
              Suchen
            </button>
          </form>
          {suchen.data && (
            <p className="mt-1 text-xs text-slate-500">
              Modus: <strong>{suchen.data.mode}</strong>
            </p>
          )}
        </section>

        <section>
          <h2 className="text-sm font-semibold">Filter</h2>
          <fieldset className="space-y-1 text-sm">
            <legend className="sr-only">Kantenarten</legend>
            {edgeKinds.map((art) => (
              <label key={art} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={arten.has(art)}
                  onChange={(ereignis) =>
                    setzeArten((vorher) => {
                      const neu = new Set(vorher);
                      if (ereignis.target.checked) {
                        neu.add(art);
                      } else {
                        neu.delete(art);
                      }
                      return neu;
                    })
                  }
                />
                {art}
              </label>
            ))}
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={nurUnbestaetigt}
                onChange={(ereignis) => setzeNurUnbestaetigt(ereignis.target.checked)}
              />
              nur unbestätigte
            </label>
          </fieldset>
        </section>

        <section>
          <h2 className="text-sm font-semibold">Layout</h2>
          <select
            className="wg-input"
            aria-label="Layout"
            value={layout}
            onChange={(ereignis) => setzeLayout(ereignis.target.value as LayoutName)}
          >
            <option value="cose">kraftbasiert</option>
            <option value="concentric">konzentrisch (Hop-Distanz)</option>
            <option value="breadthfirst">hierarchisch (member)</option>
          </select>
        </section>
      </div>

      <div className="wg-panel relative h-full p-0">
        {sichtbar.knoten.length === 0 ? (
          <p className="p-4 text-sm text-slate-500">
            Ein Cluster links auswählen — oder suchen, wenn die Übersicht nicht weiterhilft.
          </p>
        ) : (
          <GraphCanvas
            nodes={sichtbar.knoten}
            edges={sichtbar.kanten}
            selected={state.id}
            layout={layout}
            onSelect={(id, store) => onChange({ id, store })}
            onExpand={(id, store) => void aufklappen(id, store)}
          />
        )}
        {fehler !== null && (
          <p role="alert" className="absolute bottom-2 left-2 text-xs text-red-700">
            {fehler}
          </p>
        )}
      </div>

      {detail.data ? (
        <ConceptPanel
          detail={detail.data}
          onOpen={(id, store) => onChange({ id, store })}
        />
      ) : (
        <div className="wg-panel text-sm text-slate-500">Kein Knoten ausgewählt.</div>
      )}
    </div>
  );
}
