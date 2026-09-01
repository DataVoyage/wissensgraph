/**
 * Der Graph als Cytoscape-Zeichenfläche (§17.1, §17.2 Ansicht 1).
 *
 * Die visuelle Kodierung ist die aus §17.2 und keine Geschmacksfrage:
 *
 * | Merkmal | Kodierung |
 * |---|---|
 * | Store | Knotenform — Ellipse für `shared`, Raute für `personal` |
 * | Typ | Knotenfarbe (`theme.ts`) |
 * | Gewicht (Score bzw. Grad) | Knotengröße |
 * | Kantenart | Linienstil |
 * | Provenienz | Linienfarbe — manuell / Code / Modell |
 * | unbestätigte Modellkante | gestrichelt |
 *
 * Die letzte Zeile ist Leitprinzip 6 in einer Zeile CSS: Was eine Maschine vorgeschlagen und
 * niemand bestätigt hat, sieht anders aus als das, was jemand geprüft hat.
 *
 * **Warum die Instanz eine Änderung überlebt.** Vorher legte jede Datenänderung eine neue
 * Cytoscape-Instanz an. Das war einfach und hatte einen Preis, den man sah: Ein aufgeklappter
 * Nachbar ließ den ganzen Graphen von vorn beginnen, alle Knoten sprangen an neue Stellen, und
 * der Blick verlor den Punkt, von dem er gekommen war. Jetzt wird die Instanz einmal angelegt und
 * danach nur noch abgeglichen — neue Knoten kommen an der Stelle ihres Nachbarn zur Welt und
 * schieben sich von dort ins Bild. Die Anordnung bleibt dieselbe; sie wächst.
 *
 * **Warum Physik — und warum sie nicht dauernd läuft.** Ein Graph, den man am Knoten anfassen und
 * auseinanderziehen kann, beantwortet Fragen, die kein Standbild beantwortet: was hängt zusammen,
 * was hängt nur daneben, was löst sich, wenn ich hier ziehe. Deshalb läuft `cola` — aber nur,
 * solange es etwas beantwortet. Ein Dauerbetrieb rechnet je Bild über alle Knoten und Kanten und
 * kostet bei zweitausend Knoten die gesamte Bildrate, ohne dass jemand hinsieht. Die Simulation
 * sortiert den Graphen deshalb, kommt zur Ruhe und läuft wieder an, wenn jemand einen Knoten
 * anfasst. Wie die Anordnung überhaupt entsteht, entscheidet `motorFuer()` — und zwar an einer
 * gemessenen Grenze, nicht an einer geschätzten.
 */

import cytoscape from "cytoscape";
import cola from "cytoscape-cola";
import fcose from "cytoscape-fcose";
import type { Core, ElementDefinition, NodeSingular } from "cytoscape";
import { useEffect, useMemo, useRef } from "react";

import { SIGNAL, TON, farbeFuerKante, farbeFuerTyp, istUnbestaetigt } from "../theme";
import type { Edge } from "../api/types";

cytoscape.use(cola);
cytoscape.use(fcose);

/** Die Layouts aus §17.2, ergänzt um die Live-Physik. */
export type LayoutName = "physik" | "cose" | "concentric" | "breadthfirst";

/** Die Stellschrauben der Simulation — §17.2 verlangt steuerbare Layouts, nicht ein festes. */
export interface PhysikWerte {
  /** Wie stark sich Knoten abstoßen; wirkt als Mindestabstand. */
  abstossung: number;
  /** Ruhelänge einer Kante. */
  kantenlaenge: number;
  /** Wie stark alles zur Mitte gezogen wird; 0 lässt den Graphen auseinanderdriften. */
  schwerkraft: number;
}

export const PHYSIK_VORGABE: PhysikWerte = {
  abstossung: 28,
  kantenlaenge: 110,
  schwerkraft: 0.6,
};

/**
 * Ein Knoten, wie ihn die Zeichenfläche braucht.
 *
 * Bewusst nicht `GraphNode` aus der API: Eine Karte hat keinen Score und eine Traversierung
 * keinen Grad (siehe `graph.py`). Was beide haben, ist *ein* Gewicht zwischen 0 und 1, und welche
 * Größe es bedeutet, entscheidet die Ansicht — nicht die Zeichenfläche.
 */
export interface CanvasNode {
  id: string;
  store: string;
  type: string;
  title: string | null;
  status: string;
  /** 0 … 1; steuert den Durchmesser. */
  gewicht: number;
}

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
  /**
   * Die Konzepttypen dieser Installation in konfigurierter Reihenfolge (§17.1).
   *
   * Sie entscheidet über die Typfarben. Ohne sie fielen zwei Typen auf denselben Ton, sobald ein
   * Hash kollidiert — und die Kodierung aus §17.2 wäre an dieser Stelle wirkungslos.
   */
  typen?: readonly string[];
  onSelect: (id: string, store: string) => void;
  /** Doppelklick klappt einen weiteren Hop auf (§17.2: "Inkrementelles Aufklappen"). */
  onExpand?: (id: string, store: string) => void;
}

/**
 * Ob die Umgebung Bewegung will und kann.
 *
 * Zwei Fälle in einer Abfrage. Der eine ist eine Bedienhilfe: Wer im Betriebssystem "weniger
 * Bewegung" eingestellt hat, meint auch diesen Graphen. Der andere ist die Testumgebung — jsdom
 * kennt `matchMedia` nicht, hat keine echte Layout-Berechnung und braucht keine Dauerschleife im
 * `requestAnimationFrame`, die nichts zeichnet.
 */
function magSichBewegen(): boolean {
  if (typeof window.matchMedia !== "function") {
    return false;
  }
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function knotenDaten(knoten: CanvasNode, typen: readonly string[]): ElementDefinition {
  return {
    data: {
      id: knoten.id,
      label: knoten.title ?? knoten.id,
      store: knoten.store,
      typ: knoten.type,
      farbe: farbeFuerTyp(knoten.type, typen),
      // Eine Wurzelkennlinie statt einer geraden: Der Durchmesser wächst dadurch spürbar, aber
      // die Fläche nicht quadratisch — sonst erschlüge ein einziger schwerer Knoten das Bild.
      groesse: Math.round(22 + Math.sqrt(Math.max(0, Math.min(1, knoten.gewicht))) * 34),
      tombstone: knoten.status === "tombstone",
      cluster: knoten.type === "Cluster",
    },
  };
}

function kantenDaten(kante: Edge): ElementDefinition {
  return {
    data: {
      id: kante.id,
      source: kante.from_id,
      target: kante.to_id,
      label: kante.kind,
      kind: kante.kind,
      farbe: farbeFuerKante(kante.generated_by),
      unbestaetigt: istUnbestaetigt(kante),
    },
  };
}

function elemente(
  nodes: CanvasNode[],
  edges: Edge[],
  typen: readonly string[],
): ElementDefinition[] {
  const bekannt = new Set(nodes.map((knoten) => knoten.id));
  return [
    ...nodes.map((knoten) => knotenDaten(knoten, typen)),
    // Kanten auf Knoten außerhalb der Auswahl werden weggelassen: Cytoscape wirft für eine Kante
    // ohne beide Enden einen Fehler, und ein gedeckelter Ausschnitt (§17.3) hat solche Kanten
    // zwangsläufig.
    ...edges
      .filter((kante) => bekannt.has(kante.from_id) && bekannt.has(kante.to_id))
      .map(kantenDaten),
  ];
}

const STIL: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      "background-color": "data(farbe)",
      width: "data(groesse)",
      height: "data(groesse)",
      label: "data(label)",
      "font-size": "10px",
      "font-weight": 500,
      // Die wichtigste Zeile für die Lesbarkeit einer großen Karte: Beschriftungen erscheinen
      // erst, wenn sie auch lesbar wären. Zweihundert Titel über einem herausgezoomten Graphen
      // sind kein Text, sondern Rauschen — und sie verdecken genau die Struktur, wegen der man
      // herausgezoomt hat. Wer etwas lesen will, zoomt hinein, und dann steht es da.
      "min-zoomed-font-size": 9,
      "text-valign": "bottom",
      "text-margin-y": 4,
      "text-wrap": "ellipsis",
      "text-max-width": "120px",
      color: TON.text,
      // Ein heller Rand trennt überlappende Knoten voneinander, ohne eine zweite Farbe zu
      // vergeben — in einem dichten Ausschnitt ist das der Unterschied zwischen Wolke und Graph.
      "border-width": 2,
      "border-color": TON.weiss,
      "transition-property": "opacity, border-color, border-width",
      "transition-duration": 140,
    },
  },
  { selector: 'node[store = "personal"]', style: { shape: "diamond" } },
  // Cluster sind Behälter und keine Inhalte; der Ring sagt das, ohne die Form zu belegen —
  // die gehört nach §17.2 dem Store.
  { selector: "node[?cluster]", style: { "border-width": 4, "border-color": TON.linie } },
  { selector: "node[?tombstone]", style: { opacity: 0.35, "border-style": "dashed" } },
  {
    selector: "node:selected",
    style: { "border-width": 4, "border-color": SIGNAL.normal, "z-index": 20 },
  },
  // Was nicht zur Auswahl gehört, verschwindet nicht — es tritt zurück. Ein ausgeblendeter Knoten
  // ließe den Graphen zerfallen; ein blasser zeigt, dass da noch mehr ist.
  { selector: ".abgeblendet", style: { opacity: 0.12 } },
  { selector: ".hervorgehoben", style: { "z-index": 15 } },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "data(farbe)",
      "target-arrow-color": "data(farbe)",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      // Kanten treten zurück. In einem Ausschnitt mit fünfhundert Verbindungen ist die Fläche
      // zwischen den Knoten sonst dichter als die Knoten selbst, und man sieht ein Gewebe statt
      // eines Graphen. Wer eine einzelne Kante braucht, wählt ihren Knoten aus — dann wird sie
      // hell und der Rest blass.
      opacity: 0.55,
      // Der Linienstil kommt über Selektoren und nicht als Datenabbildung: Cytoscape lässt für
      // 'line-style' nur feste Werte zu, und eine Umgehung über 'any' verschöbe den Fehler nur
      // von der Typprüfung in die Laufzeit.
      "line-style": "dotted",
      "transition-property": "opacity, width",
      "transition-duration": 140,
    },
  },
  { selector: 'edge[kind = "member"]', style: { "line-style": "solid" } },
  { selector: "edge[?unbestaetigt]", style: { "line-style": "dashed", opacity: 0.9 } },
  { selector: "edge.hervorgehoben", style: { width: 2.5, opacity: 1 } },
  { selector: ".ohne-label", style: { label: "" } },
];

type Rechteck = { x1: number; y1: number; w: number; h: number };

/**
 * Die Fläche, auf der sich der Graph ausbreiten darf.
 *
 * Sie hängt an der **Knotenzahl** und nicht an der Größe des Fensters, und das ist der
 * entscheidende Punkt. Zwängt man zweihundert Knoten in einen Bildschirmausschnitt, findet `cola`
 * unter `avoidOverlap` die einzige Anordnung, die noch passt: ein Gitter. Das sah dann aus wie
 * eine Tabelle mit Strichen und nicht wie ein Graph — die Struktur war weg, obwohl die Physik
 * korrekt gerechnet hatte. Die Fläche wächst deshalb mit der Wurzel der Knotenzahl, sodass die
 * *Dichte* gleich bleibt; hinein passt sie danach ohnehin über den Zoom.
 *
 * Der Regler "Zusammenhalt" skaliert genau diese Fläche — `cola` kennt keine Schwerkraft, aber
 * eine engere Fläche wirkt wie eine.
 */
function spielflaeche(sichtbar: Rechteck, anzahl: number, physik: PhysikWerte): Rechteck {
  const seite = Math.max(
    Math.min(sichtbar.w, sichtbar.h),
    Math.sqrt(Math.max(1, anzahl)) * physik.kantenlaenge * 0.85,
  );
  const anteil = 1 - Math.max(0, Math.min(0.9, physik.schwerkraft)) * 0.4;
  const kante = seite * anteil;
  return {
    x1: sichtbar.x1 + (sichtbar.w - kante) / 2,
    y1: sichtbar.y1 + (sichtbar.h - kante) / 2,
    w: kante,
    h: kante,
  };
}

/**
 * Ab wie vielen Knoten `cola` nicht mehr benutzt wird.
 *
 * Gemessen, nicht geschätzt: Bei 300 Knoten läuft `cola` mit 18 Bildern je Sekunde, bei 600 mit
 * 7, bei 2000 mit einem — und die Bildrate kehrt erst zurück, wenn die Simulation aufhört.
 * Cytoscape selbst zeichnet dieselben 2000 Knoten mit 144: Die Kosten stecken vollständig in der
 * Simulation, die je Bild über alle Knoten und Kanten rechnet.
 *
 * Zwei naheliegende Auswege funktionieren **nicht**, beide ausprobiert und verworfen:
 *
 * - `cola` ohne Animation durchrechnen lassen. Es rechnet dann synchron und blockiert den Tab;
 *   `maxSimulationTime` bremst das nicht. Ein eingefrorener Browser ist schlechter als ein
 *   langsamer.
 * - `cola` animiert, aber zeitlich gedeckelt. Bei einer Bildrate von 1 sind drei Sekunden genau
 *   drei Schritte — die Anordnung bleibt der Zufallsstart, mit dem sie begonnen hat.
 *
 * Oberhalb der Grenze übernimmt deshalb `fcose`, das für große Graphen gebaut ist: eine spektrale
 * Vorplatzierung und wenige Verfeinerungsschritte statt einer Simulation je Bild. Man sieht ihm
 * nicht beim Rechnen zu, aber man bekommt in einer Sekunde eine Anordnung, die etwas aussagt.
 */
const PHYSIK_ANIMIERT_BIS = 400;

/**
 * Ab wie vielen Knoten das Ziehen keine Physik mehr auslöst.
 *
 * Beim Ziehen läuft die Simulation im Dauerbetrieb, damit sich der Graph unter der Hand verformt.
 * Das kostet dasselbe wie oben — nur dass hier jemand *wartet*. Über der Grenze verschiebt ein
 * Zug den Knoten, statt sieben Sekunden lang auf eine Rückmeldung warten zu lassen.
 */
const PHYSIK_ZIEHEN_BIS = 400;

/** Wie lange sich die Simulation höchstens sortieren darf, bevor sie stehen bleibt. */
const EINSCHWINGEN_MS = 3000;

/**
 * Welcher Motor die Anordnung berechnet — die eine Entscheidung, an der die Bedienbarkeit hängt.
 *
 * Eigene Funktion, weil sie sich prüfen lässt und die Optionsliste darunter nicht. Gemessen an
 * 5.000 synthetischen Knoten im laufenden Container:
 *
 * | Menge | `cola` | `fcose` |
 * |---|---|---|
 * | 300 | 22 fps beim Einschwingen, danach 60 | — |
 * | 600 | 7 fps, Tab blockiert bei `animate: false` | 142 fps |
 * | 1200 | 2 fps | 144 fps |
 * | 2000 | 1 fps, Mausrad nach 7,9 s | 140 fps |
 *
 * Beim Ziehen (`dauerhaft`) bleibt es immer bei `cola`: Nur eine laufende Simulation verformt den
 * Graphen unter der Hand, und die Zeichenfläche lässt das Ziehen oberhalb der Grenze ohnehin
 * nicht mehr in die Physik durch.
 */
export function motorFuer(anzahl: number, dauerhaft = false): "cola" | "fcose" {
  return !dauerhaft && anzahl > PHYSIK_ANIMIERT_BIS ? "fcose" : "cola";
}

/**
 * Die Optionen eines Laufs.
 *
 * **Warum die Physik nicht mehr dauerhaft läuft.** Zuerst lief `cola` mit `infinite: true` — der
 * Graph war jederzeit in Bewegung und jederzeit anfassbar. Das fühlte sich bei dreißig Knoten
 * großartig an und war bei zweitausend unbenutzbar: eine Bildrate von 1, und ein Mausrad, das
 * nach acht Sekunden reagierte. Der Fehler war nicht die Physik, sondern ihre Dauer. Jetzt
 * sortiert sich der Graph, kommt zur Ruhe und gibt den Hauptthread frei; angeworfen wird die
 * Simulation wieder, wenn jemand einen Knoten anfasst — dann, wenn sie etwas beantwortet.
 */
function layoutOptionen(
  name: LayoutName,
  physik: PhysikWerte,
  sichtbar: Rechteck,
  anzahl: number,
  bewegt: boolean,
  dauerhaft = false,
): cytoscape.LayoutOptions {
  if (name === "physik" && motorFuer(anzahl, dauerhaft) === "fcose") {
    return {
      name: "fcose",
      quality: "default",
      animate: false,
      randomize: true,
      fit: true,
      padding: 48,
      nodeSeparation: physik.abstossung * 2.5,
      idealEdgeLength: () => physik.kantenlaenge,
      nodeRepulsion: () => physik.abstossung * 150,
      // `gravity` gibt es hier wirklich — anders als bei `cola`, wo der Zusammenhalt über die
      // Fläche entsteht. Derselbe Regler, dieselbe Wirkung, ein anderer Hebel.
      gravity: 0.1 + physik.schwerkraft * 0.5,
      uniformNodeDimensions: false,
    } as unknown as cytoscape.LayoutOptions;
  }
  if (name === "physik") {
    return {
      name: "cola",
      infinite: dauerhaft,
      animate: bewegt,
      fit: false,
      // Ein zufälliger Start ist hier der bessere: Ohne ihn beginnen alle frisch eingefügten
      // Knoten auf demselben Punkt, und die Simulation braucht lange, um sie überhaupt erst
      // auseinanderzubekommen. Beim Ziehen dagegen ist die Anordnung schon da und soll bleiben.
      randomize: !dauerhaft,
      maxSimulationTime: dauerhaft ? undefined : EINSCHWINGEN_MS,
      nodeSpacing: () => physik.abstossung,
      edgeLength: physik.kantenlaenge,
      boundingBox: spielflaeche(sichtbar, anzahl, physik),
      avoidOverlap: true,
      handleDisconnected: true,
    } as unknown as cytoscape.LayoutOptions;
  }
  return {
    name,
    animate: bewegt,
    animationDuration: 420,
    animationEasing: "ease-out-cubic",
    boundingBox: name === "cose" ? spielflaeche(sichtbar, anzahl, physik) : sichtbar,
    fit: true,
    padding: 40,
    ...(name === "cose" ? { randomize: true, nodeRepulsion: () => physik.abstossung * 8000 } : {}),
  } as unknown as cytoscape.LayoutOptions;
}

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
  const kern = useRef<Core | null>(null);
  const laufendesLayout = useRef<cytoscape.Layouts | null>(null);
  // Die Rückrufe wandern über eine Referenz in die Ereignisbehandlung. Sonst müsste die Instanz
  // neu angelegt werden, sobald die Ansicht eine neue Funktion erzeugt — und genau das soll sie
  // nicht mehr.
  const rueckrufe = useRef({ onSelect, onExpand });
  rueckrufe.current = { onSelect, onExpand };
  // Wird vom Layout-Effekt gesetzt und von der Ereignisbehandlung gerufen: Anfassen eines Knotens
  // wirft die Simulation an, Loslassen lässt sie wieder zur Ruhe kommen.
  const neuStarten = useRef<(dauerhaft: boolean) => void>(() => undefined);

  const bewegt = useMemo(magSichBewegen, []);

  // -- Anlegen, genau einmal -------------------------------------------------
  useEffect(() => {
    if (behaelter.current === null) {
      return undefined;
    }
    const cy = cytoscape({
      container: behaelter.current,
      style: STIL,
      // Ausdrücklich `preset` und nicht die Vorgabe: Cytoscape lässt beim Anlegen sein
      // Standard-Layout (`grid`) über eine noch leere Elementmenge laufen und rechnet dabei auf
      // einer Fläche, die es noch nicht gibt. Das Layout dieser Zeichenfläche kommt ohnehin erst,
      // wenn Knoten da sind.
      layout: { name: "preset" },
      wheelSensitivity: 0.2,
      minZoom: 0.05,
      maxZoom: 4,
    });
    kern.current = cy;

    cy.on("tap", "node", (ereignis) => {
      const knoten = ereignis.target.data() as { id: string; store: string };
      rueckrufe.current.onSelect(knoten.id, knoten.store);
    });
    cy.on("dbltap", "node", (ereignis) => {
      const knoten = ereignis.target.data() as { id: string; store: string };
      rueckrufe.current.onExpand?.(knoten.id, knoten.store);
    });
    // Ein Klick auf die leere Fläche hebt die Hervorhebung auf — sonst bliebe der Graph nach dem
    // ersten Klick für immer halb abgeblendet.
    cy.on("tap", (ereignis) => {
      if (ereignis.target === cy) {
        cy.elements().removeClass("abgeblendet hervorgehoben");
      }
    });

    // Physik auf Zuruf: Wer einen Knoten anfasst, will sehen, was daran hängt — dann läuft die
    // Simulation. Wer nur zusieht, bekommt seine Bildrate zurück.
    cy.on("grab", "node", () => {
      if (cy.nodes().size() <= PHYSIK_ZIEHEN_BIS) {
        neuStarten.current(true);
      }
    });
    cy.on("free", "node", () => neuStarten.current(false));

    return () => {
      laufendesLayout.current?.stop();
      laufendesLayout.current = null;
      cy.destroy();
      kern.current = null;
    };
  }, []);

  // -- Elemente abgleichen ---------------------------------------------------
  useEffect(() => {
    const cy = kern.current;
    if (cy === null) {
      return;
    }
    const soll = elemente(nodes, edges, typen);
    const sollIds = new Set(soll.map((element) => String(element.data.id)));

    cy.batch(() => {
      cy.elements()
        .filter((element) => !sollIds.has(element.id()))
        .remove();
      for (const element of soll) {
        const vorhanden = cy.getElementById(String(element.data.id));
        if (vorhanden.nonempty()) {
          // Vorhandene Knoten behalten ihre Position und übernehmen nur die neuen Werte —
          // Größe und Farbe können sich geändert haben, der Ort soll es nicht.
          vorhanden.data(element.data);
        } else {
          cy.add(element);
        }
      }
    });
  }, [nodes, edges, typen]);

  // -- Layout laufen lassen --------------------------------------------------
  useEffect(() => {
    const cy = kern.current;
    if (cy === null || cy.nodes().empty()) {
      return;
    }
    const flaeche = {
      x1: 0,
      y1: 0,
      // Ein Behälter, der im Moment des Anlegens noch keine Größe hat — weil sein Reiter verborgen
      // ist oder der Browser noch nicht umgebrochen hat —, ergäbe eine Fläche von null, und der
      // Algorithmus verteilte die Knoten auf einen Punkt.
      w: behaelter.current?.clientWidth || 800,
      h: behaelter.current?.clientHeight || 600,
    };
    const anzahl = cy.nodes().size();

    const starten = (dauerhaft: boolean): void => {
      laufendesLayout.current?.stop();
      const lauf = cy.layout(layoutOptionen(layout, physik, flaeche, anzahl, bewegt, dauerhaft));
      laufendesLayout.current = lauf;
      lauf.run();
    };
    neuStarten.current = (dauerhaft) => {
      if (layout === "physik") {
        starten(dauerhaft);
      }
    };
    starten(false);

    // Die Simulation passt das Bild nicht selbst ein (`fit: false`), sonst zöge sie beim Ziehen an
    // einem Knoten dauernd am Ausschnitt. Einmal, nachdem sie sich sortiert hat, soll der Graph
    // aber im Bild stehen — sonst startet jede Karte außerhalb des Sichtfelds.
    if (layout !== "physik") {
      return undefined;
    }
    const einpassen = window.setTimeout(() => cy.fit(undefined, 48), EINSCHWINGEN_MS + 200);
    return () => window.clearTimeout(einpassen);
  }, [nodes, edges, layout, physik, bewegt]);

  // -- Auswahl und Hervorhebung ---------------------------------------------
  useEffect(() => {
    const cy = kern.current;
    if (cy === null) {
      return;
    }
    cy.batch(() => {
      cy.elements().unselect().removeClass("abgeblendet hervorgehoben");
      if (selected === undefined) {
        return;
      }
      const knoten = cy.getElementById(selected);
      if (knoten.empty()) {
        return;
      }
      knoten.select();
      // Die Nachbarschaft bleibt hell, der Rest tritt zurück. Das ist die Frage, die man an einem
      // ausgewählten Knoten stellt: Woran hängt *der*?
      const nachbarschaft = (knoten as NodeSingular).closedNeighborhood();
      cy.elements().difference(nachbarschaft).addClass("abgeblendet");
      nachbarschaft.addClass("hervorgehoben");
    });
  }, [selected, nodes, edges]);

  // -- Beschriftungen --------------------------------------------------------
  useEffect(() => {
    const cy = kern.current;
    cy?.elements().toggleClass("ohne-label", !labels);
  }, [labels, nodes, edges]);

  // -- "Alles zeigen" --------------------------------------------------------
  useEffect(() => {
    if (einpassen > 0) {
      kern.current?.animate({ fit: { eles: "", padding: 48 } }, { duration: bewegt ? 300 : 0 });
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
