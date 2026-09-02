/**
 * Der Graph als sigma-Zeichenfläche (§17.1, §17.2 Ansicht 1).
 *
 * Der Motortausch von Cytoscape auf sigma.js/graphology löst die zwei gemessenen Engpässe an
 * der Wurzel (Konzept, Abschnitt 3): WebGL nimmt dem Zeichnen die Elementzahl ab, und
 * ForceAtlas2 rechnet in einem **Web Worker** — die Simulation konkurriert nie mit Zoom,
 * Auswahl oder Panels um den UI-Thread. Der alte Motorwechsel bei 400 Knoten ist damit
 * ersatzlos entfallen: Es gibt wieder einen Motor, und er läuft auf jeder Größe.
 *
 * Was aus der Cytoscape-Fassung übernommen ist, weil es sich bewährt hat:
 *
 * - **Die Instanz überlebt eine Datenänderung.** `spiegeln()` gleicht ab statt neu zu bauen;
 *   vorhandene Knoten behalten ihre Position, neue kommen neben ihrem Nachbarn zur Welt.
 * - **Die Simulation kommt zur Ruhe.** Sie sortiert, hält an (`einschwingzeitMs`) und läuft
 *   wieder an, wenn jemand einen Knoten anfasst — ein Graph, der ewig zappelt, beantwortet
 *   nichts. Der Unterschied zu früher: Auch im Lauf bleibt die Bedienung flüssig.
 * - **Beschriftungen erscheinen erst, wenn sie lesbar wären** (`labelRenderedSizeThreshold`).
 * - **Auswahl blendet ab statt aus**: Die Nachbarschaft bleibt hell, der Rest tritt zurück —
 *   über die Reducer, ohne die Daten anzufassen.
 *
 * Die visuelle Kodierung steht in `graph/anordnung.ts`, die Rautenform in `graph/rauten.ts`.
 */

import Graph from "graphology";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import Sigma from "sigma";
import { animateNodes } from "sigma/utils";
import { useEffect, useMemo, useRef } from "react";

import type { Edge } from "../api/types";
import {
  einschwingzeitMs,
  fa2Einstellungen,
  hierarchisch,
  konzentrisch,
  spiegeln,
  sterne,
  TYP_CLUSTER,
  type CanvasNode,
  type LayoutName,
  type PhysikWerte,
  type Positionen,
} from "../graph/anordnung";
import { NodeRautenProgram } from "../graph/rauten";
import { SIGNAL, TON } from "../theme";

export {
  PHYSIK_VORGABE,
  type CanvasNode,
  type LayoutName,
  type PhysikWerte,
} from "../graph/anordnung";

export interface GraphCanvasProps {
  nodes: CanvasNode[];
  edges: Edge[];
  selected?: string;
  layout: LayoutName;
  physik: PhysikWerte;
  /** Ob Beschriftungen gezeichnet werden. Bei vielen Knoten ist das Bild ohne sie lesbarer. */
  labels?: boolean;
  /** Zählt hoch, wenn die Ansicht das Bild neu einpassen soll ("Alles zeigen"). */
  einpassen?: number;
  /** Die Konzepttypen dieser Installation in konfigurierter Reihenfolge — sie entscheidet
   *  über die Typfarben (§17.1). */
  typen?: readonly string[];
  /** Auswahl setzen — `null` hebt sie auf (Klick auf die freie Fläche). */
  onSelect: (id: string | null, store: string) => void;
  /** Doppelklick klappt einen weiteren Hop auf (§17.2: "Inkrementelles Aufklappen"). */
  onExpand?: (id: string, store: string) => void;
}

/**
 * Ob die Umgebung Bewegung will. Wer im Betriebssystem "weniger Bewegung" eingestellt hat,
 * meint auch diesen Graphen: Die Simulation rechnet dann einen einzigen Schub und stellt das
 * Ergebnis, statt ihm beim Sortieren zuzusehen.
 */
function magSichBewegen(): boolean {
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Wie lange die Simulation nach dem Loslassen eines Knotens noch nachsortiert. */
const NACHLAUF_MS = 1500;

export function GraphCanvas({
  nodes,
  edges,
  selected,
  layout,
  physik,
  labels = true,
  einpassen = 0,
  typen = [],
  onSelect,
  onExpand,
}: GraphCanvasProps): JSX.Element {
  const behaelter = useRef<HTMLDivElement | null>(null);
  const kern = useRef<Sigma | null>(null);
  const modell = useRef<Graph | null>(null);
  const simulation = useRef<FA2Layout | null>(null);
  const anhalten = useRef<number | null>(null);
  const gezogen = useRef<string | null>(null);
  /**
   * Ob die aktuelle Anordnung *gerechnet* ist (Sternenkarte, konzentrisch, hierarchisch) statt
   * erlaufen. Dann darf keine freie Simulation anlaufen: Sie kennt die Regeln nicht, nach denen
   * die Anordnung entstanden ist, und würde sie nicht fortschreiben, sondern ersetzen.
   */
  const gerechnet = useRef(false);
  // Rückrufe und Auswahl wandern über Referenzen in die Ereignisbehandlung und die Reducer —
  // sonst müsste die Instanz neu angelegt werden, sobald die Ansicht eine neue Funktion
  // erzeugt, und genau das soll sie nicht.
  const zustand = useRef({ onSelect, onExpand, selected, labels });
  zustand.current = { onSelect, onExpand, selected, labels };

  const bewegt = useMemo(magSichBewegen, []);

  // -- Anlegen, genau einmal -------------------------------------------------
  useEffect(() => {
    if (behaelter.current === null) {
      return undefined;
    }
    const graph = new Graph({ multi: true, type: "directed" });
    modell.current = graph;

    const sigma = new Sigma(graph, behaelter.current, {
      // Ein Behälter kann im Moment des Anlegens 0×0 messen (verborgener Reiter); sigma soll
      // dann warten statt werfen.
      allowInvalidContainer: true,
      nodeProgramClasses: { raute: NodeRautenProgram },
      // Die wichtigste Einstellung für die Lesbarkeit einer großen Karte: Beschriftungen
      // erscheinen erst, wenn sie auch lesbar wären. Zweihundert Titel über einem
      // herausgezoomten Graphen sind kein Text, sondern Rauschen.
      labelRenderedSizeThreshold: 7,
      labelSize: 11,
      labelWeight: "500",
      labelColor: { color: TON.text },
      zoomingRatio: 1.4,
      // Die zweitwichtigste Einstellung, und eine, die man nur durch Hinsehen findet: sigma
      // zeichnet Kanten von Haus aus nie dünner als 1,7 Pixel (`minEdgeThickness`). Beim
      // Herauszoomen rücken die Knoten zusammen, die Kanten aber nicht — bei einigen tausend
      // verschmelzen sie zu Bändern, und das Bild zeigt Fläche statt Struktur. Mit 0,6 dürfen
      // sie wirklich fein werden; wer eine einzelne Kante braucht, zoomt hinein oder wählt
      // ihren Knoten aus, dann tritt sie ohnehin hervor.
      minEdgeThickness: 0.6,
      // Mehr Luft zum Rand. Der Graph beantwortet zuerst eine strukturelle Frage, und die
      // braucht Zwischenraum — auch nach außen.
      stagePadding: 60,
      // Auswahl über Reducer: Die Nachbarschaft bleibt hell, der Rest tritt zurück, ohne dass
      // die Daten angefasst werden. `hidden`/Löschen ließe den Graphen zerfallen; blass zeigt,
      // dass da noch mehr ist.
      nodeReducer: (id, daten) => {
        const { selected: wahl, labels: mitLabel } = zustand.current;
        // `size` im Modell ist der Layout-Radius für ForceAtlas2 (Kollision in
        // Layout-Koordinaten); gezeichnet wird die Pixelgröße daneben. Siehe `anordnung.ts`.
        const angepasst: Record<string, unknown> = { ...daten, size: daten.zeichenGroesse };
        if (!mitLabel) {
          angepasst.label = null;
        }
        if (wahl === undefined || modell.current === null) {
          return angepasst;
        }
        if (id === wahl) {
          angepasst.highlighted = true;
          angepasst.zIndex = 2;
        } else if (!modell.current.hasNode(wahl) || !istNachbar(modell.current, wahl, id)) {
          angepasst.color = `${String(daten.color ?? TON.linie).slice(0, 7)}22`;
          angepasst.label = null;
          angepasst.zIndex = 0;
        } else {
          angepasst.zIndex = 1;
        }
        return angepasst;
      },
      edgeReducer: (id, daten) => {
        const { selected: wahl } = zustand.current;
        if (wahl === undefined || modell.current === null || !modell.current.hasEdge(id)) {
          return daten;
        }
        const beruehrt =
          modell.current.source(id) === wahl || modell.current.target(id) === wahl;
        if (beruehrt) {
          return { ...daten, color: SIGNAL.dunkel, size: Number(daten.size ?? 1) + 0.8, zIndex: 1 };
        }
        return { ...daten, color: `${String(daten.color ?? TON.linie).slice(0, 7)}14` };
      },
      zIndex: true,
    });
    kern.current = sigma;

    // Ein Grabstein bleibt sichtbar, aber blass — er ist Geschichte, kein Inhalt (§7.6).
    // Über die Knotenattribute selbst, damit der Reducer nicht jeden Status kennen muss.

    sigma.on("clickNode", ({ node }) => {
      const store = String(graph.getNodeAttribute(node, "store"));
      zustand.current.onSelect(node, store);
    });
    // Klick auf die freie Fläche hebt die Auswahl auf. Ohne das gab es keinen Weg zurück:
    // Die Auswahl blendet alles ab, was nicht Nachbarschaft ist, und wer einmal etwas
    // angeklickt hatte, blieb in dieser Verengung gefangen — er konnte nur noch zu einem
    // anderen Knoten wechseln, nie zur ganzen Karte zurück.
    sigma.on("clickStage", () => {
      zustand.current.onSelect(null, "");
    });
    // Der Doppelklick gehört den Knoten. Sigma zoomt von Haus aus auch auf einen Doppelklick
    // ins Leere, und das ist hier eine Falle: Wer einen Knoten aufklappen will und ihn knapp
    // verfehlt — die Punkte sind zwei bis neun Pixel groß —, springt stattdessen tief in den
    // Graphen hinein und findet ohne "Alles zeigen" nicht zurück. Zoomen kann man über das
    // Mausrad, und das bleibt unberührt.
    sigma.on("doubleClickStage", (ereignis) => {
      ereignis.preventSigmaDefault();
    });
    sigma.on("doubleClickNode", (ereignis) => {
      ereignis.preventSigmaDefault();
      const store = String(graph.getNodeAttribute(ereignis.node, "store"));
      zustand.current.onExpand?.(ereignis.node, store);
    });

    // -- Ziehen: Physik auf Zuruf ------------------------------------------
    // Wer einen Knoten anfasst, will sehen, was daran hängt — die Simulation läuft mit und
    // verformt den Graphen unter der Hand. Der Worker macht das auch bei tausenden Knoten
    // bezahlbar; die alte 400er-Grenze fürs Ziehen ist entfallen.
    //
    // **Nicht in der Sternenkarte.** Dort ist die Anordnung gerechnet und nicht erlaufen, und
    // die freie Simulation würde sie zerstören, statt sie fortzuschreiben: Sie kennt die
    // Zugehörigkeiten nicht und zieht den Graphen nach denselben Massenregeln zusammen, deretwegen
    // es die Sternenkarte überhaupt gibt. Weil `downNode` schon beim Mausdruck feuert, genügte
    // ein einzelner Klick — der Graph fiel bei jeder Berührung ein Stück weiter in sich zusammen.
    // Der angefasste Knoten folgt trotzdem der Maus; nur die anderen bleiben, wo sie hingehören.
    sigma.on("downNode", (ereignis) => {
      ereignis.preventSigmaDefault();
      gezogen.current = ereignis.node;
      graph.setNodeAttribute(ereignis.node, "highlighted", true);
      if (!gerechnet.current) {
        simulationAnwerfen();
      }
    });
    sigma.getMouseCaptor().on("mousemovebody", (ereignis) => {
      const id = gezogen.current;
      if (id === null) {
        return;
      }
      const punkt = sigma.viewportToGraph(ereignis);
      graph.setNodeAttribute(id, "x", punkt.x);
      graph.setNodeAttribute(id, "y", punkt.y);
      // Während des Zugs soll die Kamera stehen bleiben.
      ereignis.preventSigmaDefault();
      ereignis.original.preventDefault();
    });
    const loslassen = (): void => {
      if (gezogen.current !== null) {
        graph.removeNodeAttribute(gezogen.current, "highlighted");
        gezogen.current = null;
        if (!gerechnet.current) {
          simulationAusklingen();
        }
      }
    };
    sigma.getMouseCaptor().on("mouseup", loslassen);

    return () => {
      if (anhalten.current !== null) {
        window.clearTimeout(anhalten.current);
      }
      simulation.current?.kill();
      simulation.current = null;
      sigma.kill();
      kern.current = null;
      modell.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Startet die Simulation (neu), passend zu Reglern und Größe. */
  function simulationAnwerfen(): void {
    const graph = modell.current;
    if (graph === null || graph.order === 0) {
      return;
    }
    if (anhalten.current !== null) {
      window.clearTimeout(anhalten.current);
      anhalten.current = null;
    }
    simulation.current?.kill();
    const lauf = new FA2Layout(graph, {
      settings: fa2Einstellungen(physik, graph.order, graph.size),
    });
    simulation.current = lauf;
    lauf.start();
  }

  /** Lässt die Simulation kurz nachsortieren und hält sie dann an. */
  function simulationAusklingen(dauer = NACHLAUF_MS): void {
    if (anhalten.current !== null) {
      window.clearTimeout(anhalten.current);
    }
    anhalten.current = window.setTimeout(() => {
      simulation.current?.stop();
      anhalten.current = null;
    }, dauer);
  }

  // -- Daten abgleichen und Anordnung rechnen --------------------------------
  useEffect(() => {
    const graph = modell.current;
    if (graph === null) {
      return;
    }
    spiegeln(graph, nodes, edges, typen);
    if (graph.order === 0) {
      return;
    }

    if (layout === "physik") {
      // Gibt es Cluster im Bild, zeichnet die Sternenkarte: Zentren kraftbasiert, Mitglieder
      // als Ring darum, Unverbundenes außen. Der Grund steht ausführlich in `anordnung.ts` —
      // kurz: In ForceAtlas2 ist die Masse eines Knotens die Summe seiner Kantengewichte, und
      // damit wird ein Cluster durch seine eigenen Mitglieder nach außen gedrängt, während die
      // kantenlosen Dokumente in die Mitte fallen. Der Stern kommt dort verkehrt herum heraus.
      //
      // Ohne Cluster — eine frisch synchronisierte Sammlung, ein enger Filter — bleibt es bei
      // der freien Simulation: Ohne Zentren gibt es keine Sterne zu zeichnen.
      void import("graphology-layout-forceatlas2").then((fa2) => {
        const zentren = graph.filterNodes((_id, daten) => daten.typ === TYP_CLUSTER);
        gerechnet.current = zentren.length > 0;
        if (zentren.length === 0) {
          if (bewegt) {
            simulationAnwerfen();
            simulationAusklingen(einschwingzeitMs(graph.order));
          } else {
            fa2.default.assign(graph, {
              iterations: 80,
              settings: fa2Einstellungen(physik, graph.order, graph.size),
            });
          }
          return;
        }
        simulation.current?.stop();
        const lagen = sterne(graph, physik, (ziel, optionen) =>
          fa2.default.assign(ziel, optionen as never),
        );
        stellen(graph, lagen);
      });
      return;
    }

    simulation.current?.stop();
    gerechnet.current = true;
    const abstand = physik.kantenlaenge * 0.9;
    stellen(graph, layout === "concentric" ? konzentrisch(graph, abstand) : hierarchisch(graph, abstand));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, typen, layout, physik, bewegt]);

  /** Setzt gerechnete Positionen — gleitend, wenn das jemand sehen will (§17.2). */
  function stellen(graph: Graph, positionen: Positionen): void {
    if (bewegt) {
      const ziel: Record<string, { x: number; y: number }> = {};
      for (const [id, punkt] of positionen) {
        ziel[id] = punkt;
      }
      animateNodes(graph, ziel, { duration: 420 });
      return;
    }
    for (const [id, punkt] of positionen) {
      graph.setNodeAttribute(id, "x", punkt.x);
      graph.setNodeAttribute(id, "y", punkt.y);
    }
  }

  // -- Auswahl, Beschriftungen -----------------------------------------------
  useEffect(() => {
    // Reducer lesen aus der Referenz; hier genügt ein Neuzeichnen.
    kern.current?.refresh();
  }, [selected, labels, nodes, edges]);

  // -- "Alles zeigen" --------------------------------------------------------
  useEffect(() => {
    if (einpassen > 0) {
      void kern.current?.getCamera().animatedReset({ duration: bewegt ? 300 : 0 });
    }
  }, [einpassen, bewegt]);

  return (
    <div
      ref={behaelter}
      data-testid="graph-canvas"
      aria-label="Graph"
      className="h-full w-full bg-ton-50"
    />
  );
}

function istNachbar(graph: Graph, wahl: string, id: string): boolean {
  return graph.hasNode(id) && (graph.areNeighbors(wahl, id) || false);
}
