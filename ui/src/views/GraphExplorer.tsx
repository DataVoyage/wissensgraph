/**
 * Ansicht 1 — die Graph-Zentrale (§17.2).
 *
 * Der Graph kommt in zwei Ausprägungen, und beide sind dieselbe Ansicht:
 *
 * - **Karte** — der gefilterte Bestand ohne Ausgangspunkt, über `/graph/map`. Der Überblick:
 *   "Was liegt hier überhaupt, wenn ich es so einschränke?"
 * - **Reise** — inkrementelles Aufklappen Hop für Hop über `/graph/neighbors`. Die Erkundung:
 *   "Woran hängt *das* hier?"
 *
 * §17.2 verlangt für die Erkundung ausdrücklich "kein Vorabladen des Gesamtgraphen", und daran
 * ändert die Karte nichts: Sie lädt keinen Gesamtgraphen, sondern eine gedeckelte, gefilterte
 * Seite, deren Rest sichtbar als Rest ausgewiesen wird. Der Unterschied zwischen beiden Modi ist
 * nicht die Datenmenge, sondern die Frage — und wer eine Sammlung noch nicht kennt, hat keinen
 * Startknoten, den er nennen könnte.
 *
 * Der aufgeklappte Ausschnitt der Reise *wächst*, statt ersetzt zu werden — sonst verlöre ein
 * Klick den Weg, über den man gekommen ist.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { get } from "../api/client";
import { useClusters, useConcept, useGraphMap, useSearch } from "../api/hooks";
import type { Edge, GraphNode, Traversal } from "../api/types";
import { ConceptPanel } from "../components/ConceptPanel";
import {
  GraphCanvas,
  PHYSIK_VORGABE,
  type CanvasNode,
  type LayoutName,
  type PhysikWerte,
} from "../components/GraphCanvas";
import { GraphControls } from "../components/GraphControls";
import { GraphLegend } from "../components/GraphLegend";
import { Inspektor } from "../components/Inspektor";
import type { EffectiveConfig } from "../api/types";
import type { GraphMode, UiState } from "../state";
import type { WerkbankZustand } from "../werkbank";

export interface GraphExplorerProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  /** Die Fachregeln kommen aus der Konfiguration; die Oberfläche kennt keine eigenen (§17.1). */
  config: EffectiveConfig;
  /** Die Werkbank hält Panelbreite und -zustand — sie gehört der Hülle, nicht dieser Ansicht. */
  werkbank: WerkbankZustand;
  onWerkbank: (aenderung: Partial<WerkbankZustand>) => void;
}

interface Ausschnitt {
  nodes: Map<string, GraphNode>;
  edges: Map<string, Edge>;
}

const LEER: Ausschnitt = { nodes: new Map(), edges: new Map() };

/**
 * Die Karte lädt im Default den **ganzen Bestand**, nicht einen Ausschnitt.
 *
 * Das war anders — 300 Knoten, dann "mehr laden" — und das Ergebnis war an echten Daten
 * beweisbar strukturlos: Die Seite schneidet nach ID, `cluster:` sortiert vor `sapdoc:`, also
 * bestand der erste Ausschnitt aus sämtlichen Clustern und einer Handvoll zufälliger Dokumente.
 * Eine Kante erscheint nur, wenn beide Enden sichtbar sind — von 818 Mitgliedschaften überlebten
 * fast keine, vom Cluster-Geflecht alle. Was die Physik dann zeigte (ein Knäuel aus Clustern,
 * ein Ring beziehungsloser Knoten), war kein Physikfehler, sondern die korrekte Darstellung
 * eines zerrissenen Graphen. Struktur ist eine Eigenschaft des Ganzen; sie übersteht kein
 * zufälliges Vierzehntel.
 *
 * Der Start deckelt trotzdem: 5.000 ist die vermessene Engine-Grenze — so viele Knoten
 * zeichnet sigma mit laufender Physik flüssig. Wächst ein Bestand darüber, holt "mehr laden"
 * den Rest in Verdopplungsschritten (§17.3); das ist dann eine bewusste Entscheidung dessen,
 * der klickt, keine Vorgabe.
 */
const KARTE_SCHRITT = 5000;
const KARTE_MAX = 20000;

export function GraphExplorer({
  state,
  onChange,
  config,
  werkbank,
  onWerkbank,
}: GraphExplorerProps): JSX.Element {
  const modus: GraphMode = state.mode ?? "karte";
  const kantenarten = useMemo(
    () => [...config.edge_kinds.structural, ...config.edge_kinds.semantic],
    [config],
  );
  const alleTypen = useMemo(
    () => config.concept_types.map((eintrag) => eintrag.name),
    [config],
  );
  const gewaehlteArten = useMemo(
    () => (state.kinds ? state.kinds.split(",").filter(Boolean) : []),
    [state.kinds],
  );

  const [ausschnitt, setzeAusschnitt] = useState<Ausschnitt>(LEER);
  const [layout, setzeLayout] = useState<LayoutName>("physik");
  const [physik, setzePhysik] = useState<PhysikWerte>(PHYSIK_VORGABE);
  const [labels, setzeLabels] = useState(true);
  const [einpassen, setzeEinpassen] = useState(0);
  const [grenze, setzeGrenze] = useState(KARTE_SCHRITT);
  const [fehler, setzeFehler] = useState<string | null>(null);

  const cluster = useClusters(state.store, state.scope);
  const detail = useConcept(state.id ?? null, state.store);
  const suchen = useSearch();

  const karte = useGraphMap(
    {
      store: state.store,
      scope: state.scope,
      type: state.type,
      status: state.status,
      q: state.q,
      cluster_id: state.cluster,
      orphan: state.orphan,
      unverified: state.unverified,
      include_tombstones: state.tombstones,
      kinds: gewaehlteArten.length > 0 ? gewaehlteArten : undefined,
      limit: grenze,
    },
    modus === "karte",
  );

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
        aufnehmen(await get<Traversal>(`/api/v1/graph/neighbors/${encodeURI(id)}`, { store }));
        setzeFehler(null);
      } catch (ausnahme) {
        setzeFehler(ausnahme instanceof Error ? ausnahme.message : String(ausnahme));
      }
    },
    [aufnehmen],
  );

  /**
   * Ein Knoten aus der URL wird **beim Einstieg** aufgeklappt — und nur dann.
   *
   * Ein geteilter Link soll denselben Ausschnitt zeigen und nicht eine leere Fläche mit einem
   * markierten Punkt. Vorher hing das Aufklappen aber an jeder Änderung von `state.id`, und
   * damit an jeder Auswahl: Ein einfacher Klick auf einen Knoten holte einen Hop nach, obwohl
   * die Steuerspalte "Doppelklick auf einen Knoten" verspricht und §17.2 die Erkundung
   * ausdrücklich als Doppelklick beschreibt. Die drei Gesten sind jetzt drei verschiedene
   * Dinge: Klick wählt aus und hebt die Nachbarschaft hervor, Doppelklick klappt auf, Klick
   * ins Leere hebt die Auswahl auf.
   */
  const eingestiegen = useRef(false);
  useEffect(() => {
    if (modus !== "reise") {
      // Beim Wechsel in die Karte zurücksetzen, damit ein späterer Einstieg wieder greift.
      eingestiegen.current = false;
      return;
    }
    if (eingestiegen.current) {
      return;
    }
    eingestiegen.current = true;
    if (state.id !== undefined) {
      void aufklappen(state.id, state.store);
    }
  }, [modus, state.id, state.store, aufklappen]);

  /**
   * Was gezeichnet wird — je Modus aus einer anderen Quelle, aber in derselben Form.
   *
   * Das Gewicht ist der einzige Unterschied, der bis zur Zeichenfläche durchschlägt: In der Reise
   * ist es der Score der Traversierung (§12.3), in der Karte der Grad im Ausschnitt. Beides auf
   * 0…1 normiert, weil die Fläche nur "wie groß" versteht und nicht "wie wichtig warum".
   */
  const bild = useMemo((): { knoten: CanvasNode[]; kanten: Edge[]; gedeckelt: boolean } => {
    if (modus === "karte") {
      const daten = karte.data;
      if (daten === undefined) {
        return { knoten: [], kanten: [], gedeckelt: false };
      }
      const hoechster = Math.max(1, ...daten.nodes.map((knoten) => knoten.degree));
      return {
        knoten: daten.nodes.map((knoten) => ({
          id: knoten.id,
          store: knoten.store,
          type: knoten.type,
          title: knoten.title,
          status: knoten.status,
          gewicht: knoten.degree / hoechster,
        })),
        kanten: daten.edges,
        gedeckelt: daten.truncated,
      };
    }
    const kanten = [...ausschnitt.edges.values()].filter(
      (kante) => gewaehlteArten.length === 0 || gewaehlteArten.includes(kante.kind),
    );
    return {
      knoten: [...ausschnitt.nodes.values()]
        .filter((knoten) => state.tombstones === true || knoten.status !== "tombstone")
        .map((knoten) => ({
          id: knoten.id,
          store: knoten.store,
          type: knoten.type,
          title: knoten.title,
          status: knoten.status,
          gewicht: knoten.score,
        })),
      kanten,
      gedeckelt: false,
    };
  }, [modus, karte.data, ausschnitt, gewaehlteArten, state.tombstones]);

  const typenImBild = useMemo(
    () => [...new Set(bild.knoten.map((knoten) => knoten.type))].sort(),
    [bild.knoten],
  );

  const umschalten = (art: string): void => {
    const neu = gewaehlteArten.includes(art)
      ? gewaehlteArten.filter((eintrag) => eintrag !== art)
      : [...gewaehlteArten, art];
    onChange({ kinds: neu.join(",") });
  };

  const suchfeld = (formular: HTMLFormElement): void => {
    const query = String(new FormData(formular).get("q") ?? "");
    if (modus === "karte") {
      // In der Karte ist die Suche eine Facette wie jede andere: Sie schneidet den Bestand,
      // statt Treffer in einen bestehenden Ausschnitt zu streuen.
      onChange({ q: query || undefined });
      return;
    }
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
          onChange({ q: query || undefined });
        },
      },
    );
  };

  return (
    <div className="flex h-full gap-3">
      {/* -- Steuerspalte ---------------------------------------------------- */}
      <div className="wg-panel flex w-[248px] shrink-0 flex-col gap-4 overflow-y-auto">
        <section>
          <h2 className="wg-panel-titel">Ansicht</h2>
          <div className="wg-segment w-full" role="group" aria-label="Graph-Modus">
            <button
              type="button"
              className="flex-1"
              aria-pressed={modus === "karte"}
              onClick={() => onChange({ mode: "karte" })}
            >
              Karte
            </button>
            <button
              type="button"
              className="flex-1"
              aria-pressed={modus === "reise"}
              onClick={() => onChange({ mode: "reise" })}
            >
              Traversierung
            </button>
          </div>
          <p className="wg-hinweis mt-1.5">
            {modus === "karte"
              ? "Der gefilterte Bestand auf einen Blick."
              : "Klick zeigt Details, Doppelklick klappt einen Hop auf."}
          </p>
        </section>

        <section>
          <h2 className="wg-panel-titel">Suche</h2>
          <form
            className="flex gap-1.5"
            onSubmit={(ereignis) => {
              ereignis.preventDefault();
              suchfeld(ereignis.currentTarget);
            }}
          >
            <input
              className="wg-input"
              name="q"
              aria-label="Suchbegriff"
              placeholder="Begriff …"
              defaultValue={state.q}
            />
            <button type="submit" className="wg-button wg-button-primaer shrink-0">
              Los
            </button>
          </form>
          {modus === "reise" && suchen.data && (
            <p className="wg-hinweis mt-1">
              Modus: <strong className="text-ton-700">{suchen.data.mode}</strong>
            </p>
          )}
        </section>

        <section>
          <h2 className="wg-panel-titel">Kernspace-Übersicht</h2>
          <p className="wg-hinweis mb-1.5">
            {modus === "karte"
              ? "Ein Cluster schneidet die Karte auf seine Mitglieder."
              : "Der Einstieg — kein Suchfeld (§17.2)."}
          </p>
          <ul className="-mx-1 max-h-56 space-y-0.5 overflow-y-auto">
            {(cluster.data?.items ?? []).map((eintrag) => {
              const aktiv = modus === "karte" ? state.cluster === eintrag.id : state.id === eintrag.id;
              return (
                <li key={eintrag.id}>
                  <button
                    type="button"
                    className={`wg-eintrag flex items-baseline justify-between gap-2 ${
                      aktiv ? "wg-eintrag-aktiv" : ""
                    }`}
                    onClick={() => {
                      if (modus === "karte") {
                        onChange({ cluster: aktiv ? undefined : eintrag.id });
                        return;
                      }
                      // In der Reise ist die Übersicht der Einstieg (§17.2) — hier ist das
                      // Aufklappen gewollt und wird deshalb ausdrücklich ausgelöst, seit die
                      // Auswahl allein keinen Hop mehr nachholt.
                      onChange({ id: eintrag.id });
                      void aufklappen(eintrag.id, state.store);
                    }}
                  >
                    <span className="truncate">{eintrag.title ?? eintrag.id}</span>
                    <span className="shrink-0 text-2xs tabular-nums opacity-60">
                      {eintrag.member_count}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        <section>
          <h2 className="wg-panel-titel">Filter</h2>
          <div className="space-y-2">
            <label className="block">
              <span className="wg-label">Scope</span>
              <select
                className="wg-input"
                aria-label="Scope"
                value={state.scope ?? ""}
                onChange={(ereignis) => onChange({ scope: ereignis.target.value || undefined })}
              >
                <option value="">alle</option>
                {config.scopes
                  .filter((eintrag) => eintrag.store === state.store)
                  .map((eintrag) => (
                    <option key={eintrag.name} value={eintrag.name}>
                      {eintrag.name}
                    </option>
                  ))}
              </select>
            </label>

            <label className="block">
              <span className="wg-label">Typ</span>
              <select
                className="wg-input"
                aria-label="Typ"
                value={state.type ?? ""}
                onChange={(ereignis) => onChange({ type: ereignis.target.value || undefined })}
              >
                <option value="">alle</option>
                {config.concept_types
                  .filter((eintrag) => eintrag.stores.includes(state.store))
                  .map((eintrag) => (
                    <option key={eintrag.name} value={eintrag.name}>
                      {eintrag.name}
                    </option>
                  ))}
              </select>
            </label>

            <fieldset>
              <legend className="wg-label">Kantenarten</legend>
              <div className="-mx-1 max-h-40 overflow-y-auto">
                {kantenarten.map((art) => (
                  <label key={art} className="wg-check">
                    <input
                      type="checkbox"
                      checked={gewaehlteArten.includes(art)}
                      onChange={() => umschalten(art)}
                    />
                    <span className="truncate font-mono text-xs">{art}</span>
                  </label>
                ))}
              </div>
              <p className="wg-hinweis">Nichts angehakt heißt: alle.</p>
            </fieldset>

            <fieldset className="-mx-1">
              <legend className="wg-label mx-1">Nur zeigen</legend>
              <label className="wg-check">
                <input
                  type="checkbox"
                  checked={state.unverified === true}
                  onChange={(ereignis) =>
                    onChange({ unverified: ereignis.target.checked || undefined })
                  }
                />
                nur unbestätigte
              </label>
              <label className="wg-check">
                <input
                  type="checkbox"
                  checked={state.orphan === true}
                  onChange={(ereignis) => onChange({ orphan: ereignis.target.checked || undefined })}
                />
                nur lose
              </label>
              <label className="wg-check">
                <input
                  type="checkbox"
                  checked={state.tombstones === true}
                  onChange={(ereignis) =>
                    onChange({ tombstones: ereignis.target.checked || undefined })
                  }
                />
                Grabsteine zeigen
              </label>
            </fieldset>
          </div>
        </section>

        <GraphLegend typen={typenImBild} alleTypen={alleTypen} />
      </div>

      {/* -- Zeichenfläche ---------------------------------------------------- */}
      <div className="wg-panel-blank relative h-full min-w-0 flex-1">
        <GraphControls
          layout={layout}
          onLayout={setzeLayout}
          physik={physik}
          onPhysik={setzePhysik}
          labels={labels}
          onLabels={setzeLabels}
          onEinpassen={() => setzeEinpassen((vorher) => vorher + 1)}
          knoten={bild.knoten.length}
          kanten={bild.kanten.length}
          gedeckelt={bild.gedeckelt}
        />

        {bild.knoten.length === 0 ? (
          <div className="wg-leer">
            {karte.isFetching ? (
              <p role="status" className="text-sm text-ton-500">
                Der Ausschnitt wird geladen …
              </p>
            ) : (
              <>
                <p className="text-sm font-medium text-ton-700">Nichts zu zeichnen.</p>
                <p className="wg-hinweis max-w-xs">
                  {modus === "karte"
                    ? "Kein Konzept passt auf diese Filter. Ein Häkchen weniger, und der Bestand kommt zurück."
                    : "Ein Cluster links auswählen — oder suchen, wenn die Übersicht nicht weiterhilft."}
                </p>
              </>
            )}
          </div>
        ) : (
          <GraphCanvas
            nodes={bild.knoten}
            edges={bild.kanten}
            selected={state.id}
            layout={layout}
            physik={physik}
            labels={labels}
            einpassen={einpassen}
            typen={alleTypen}
            onSelect={(id, store) => onChange(id === null ? { id: undefined } : { id, store })}
            onExpand={(id, store) => void aufklappen(id, store)}
          />
        )}

        {/* §17.3: "Große Nachbarschaften werden gedeckelt und mit 'mehr laden' erweitert." */}
        {bild.gedeckelt && grenze < KARTE_MAX && (
          <button
            type="button"
            className="wg-button absolute bottom-3 left-1/2 z-10 -translate-x-1/2 shadow-schwebend"
            onClick={() => setzeGrenze((vorher) => Math.min(KARTE_MAX, vorher * 2))}
          >
            mehr laden
          </button>
        )}

        {(fehler !== null || karte.isError) && (
          <p role="alert" className="wg-fehler absolute bottom-3 left-3 z-10">
            {fehler ?? karte.error?.message}
          </p>
        )}
      </div>

      {/* -- Inspektor: das Selektierte, einklappbar und ziehbar (§17.5) ------- */}
      <Inspektor
        titel="Inspektor"
        breite={werkbank.inspektorBreite}
        zu={werkbank.inspektorZu}
        onBreite={(inspektorBreite) => onWerkbank({ inspektorBreite })}
        onZu={(inspektorZu) => onWerkbank({ inspektorZu })}
      >
        {detail.data ? (
          <ConceptPanel
            detail={detail.data}
            onOpen={(id, store) => {
              // "Öffnen" heißt hingehen: In der Reise soll der Knoten danach auch auf der
              // Fläche stehen und nicht nur im Inspektor — ein Verweis, dem man folgt, ist
              // dieselbe Bewegung wie ein Doppelklick, nur von der anderen Seite.
              onChange({ id, store });
              if (modus === "reise") {
                void aufklappen(id, store);
              }
            }}
          />
        ) : (
          <div className="wg-leer">
            <p className="text-sm font-medium text-ton-700">Kein Knoten ausgewählt.</p>
            <p className="wg-hinweis max-w-[15rem]">
              Ein Klick in den Graphen hebt die Nachbarschaft hervor und zeigt hier Felder,
              Provenienz und Journal.
            </p>
          </div>
        )}
      </Inspektor>
    </div>
  );
}
