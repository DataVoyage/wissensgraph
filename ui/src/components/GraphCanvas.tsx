/**
 * Der Graph als Cytoscape-Zeichenfläche (§17.1, §17.2 Ansicht 1).
 *
 * Die visuelle Kodierung ist die aus §17.2 und keine Geschmacksfrage:
 *
 * | Merkmal | Kodierung |
 * |---|---|
 * | Store | Knotenform — Ellipse für `shared`, Raute für `personal` |
 * | Typ | Knotenfarbe |
 * | Score | Knotengröße |
 * | Kantenart | Linienstil |
 * | Provenienz | Linienfarbe — manuell / Code / Modell |
 * | unbestätigte Modellkante | gestrichelt |
 *
 * Die letzte Zeile ist Leitprinzip 6 in einer Zeile CSS: Was eine Maschine vorgeschlagen und
 * niemand bestätigt hat, sieht anders aus als das, was jemand geprüft hat.
 */

import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";
import { useEffect, useRef } from "react";

import type { Edge, GraphNode } from "../api/types";

/** Die Layouts aus §17.2: kraftbasiert, konzentrisch um den Start, hierarchisch entlang `member`. */
export type LayoutName = "cose" | "concentric" | "breadthfirst";

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: Edge[];
  selected?: string;
  layout: LayoutName;
  onSelect: (id: string, store: string) => void;
  /** Doppelklick klappt einen weiteren Hop auf (§17.2: "Inkrementelles Aufklappen"). */
  onExpand?: (id: string, store: string) => void;
}

/** Eine stabile Farbe je Konzepttyp — dieselbe Zeichenkette ergibt immer denselben Farbton. */
function farbeFuerTyp(typ: string): string {
  let hash = 0;
  for (const zeichen of typ) {
    hash = (hash * 31 + zeichen.charCodeAt(0)) % 360;
  }
  return `hsl(${hash}, 55%, 55%)`;
}

/** Die Provenienz einer Kante als Farbe (§17.2). */
function farbeFuerKante(edge: Edge): string {
  if (edge.generated_by === null) {
    return "#0f766e";
  }
  return edge.generated_by.startsWith("code:") ? "#475569" : "#a16207";
}

function elemente(nodes: GraphNode[], edges: Edge[]): ElementDefinition[] {
  const bekannt = new Set(nodes.map((knoten) => knoten.id));
  return [
    ...nodes.map((knoten) => ({
      data: {
        id: knoten.id,
        label: knoten.title ?? knoten.id,
        store: knoten.store,
        typ: knoten.type,
        farbe: farbeFuerTyp(knoten.type),
        groesse: 24 + Math.round(knoten.score * 28),
        tombstone: knoten.status === "tombstone",
      },
    })),
    // Kanten auf Knoten außerhalb der Auswahl werden weggelassen: Cytoscape wirft für eine Kante
    // ohne beide Enden einen Fehler, und ein gedeckelter Ausschnitt (§17.3) hat solche Kanten
    // zwangsläufig.
    ...edges
      .filter((kante) => bekannt.has(kante.from_id) && bekannt.has(kante.to_id))
      .map((kante) => ({
        data: {
          id: kante.id,
          source: kante.from_id,
          target: kante.to_id,
          label: kante.kind,
          kind: kante.kind,
          farbe: farbeFuerKante(kante),
          unbestaetigt: kante.generated_by !== null && !kante.curated && kante.verified_at === null,
        },
      })),
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
      "font-size": "9px",
      "text-valign": "bottom",
      "text-wrap": "ellipsis",
      "text-max-width": "110px",
      color: "#0f172a",
    },
  },
  { selector: 'node[store = "personal"]', style: { shape: "diamond" } },
  { selector: "node[?tombstone]", style: { opacity: 0.4, "border-style": "dashed" } },
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#0f172a" } },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "data(farbe)",
      "target-arrow-color": "data(farbe)",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      // Der Linienstil kommt über Selektoren und nicht als Datenabbildung: Cytoscape lässt für
      // 'line-style' nur feste Werte zu, und eine Umgehung über 'any' verschöbe den Fehler nur
      // von der Typprüfung in die Laufzeit.
      "line-style": "dotted",
    },
  },
  { selector: 'edge[kind = "member"]', style: { "line-style": "solid" } },
  { selector: "edge[?unbestaetigt]", style: { "line-style": "dashed", opacity: 0.7 } },
];

export function GraphCanvas({
  nodes,
  edges,
  selected,
  layout,
  onSelect,
  onExpand,
}: GraphCanvasProps): JSX.Element {
  const behaelter = useRef<HTMLDivElement | null>(null);
  const kern = useRef<Core | null>(null);

  useEffect(() => {
    if (behaelter.current === null) {
      return undefined;
    }
    // Die Layout-Fläche wird ausdrücklich gesetzt und nicht gemessen: Ein Behälter, der im
    // Moment des Anlegens noch keine Größe hat — weil sein Reiter verborgen ist oder der Browser
    // noch nicht umgebrochen hat —, ergäbe eine Fläche von null, und der Algorithmus verteilte
    // die Knoten auf einen Punkt.
    const flaeche = {
      x1: 0,
      y1: 0,
      w: behaelter.current.clientWidth || 800,
      h: behaelter.current.clientHeight || 600,
    };
    const cy = cytoscape({
      container: behaelter.current,
      elements: elemente(nodes, edges),
      style: STIL,
      layout: { name: layout, animate: false, boundingBox: flaeche },
      wheelSensitivity: 0.2,
    });
    kern.current = cy;

    cy.on("tap", "node", (ereignis) => {
      const knoten = ereignis.target.data() as { id: string; store: string };
      onSelect(knoten.id, knoten.store);
    });
    cy.on("dbltap", "node", (ereignis) => {
      const knoten = ereignis.target.data() as { id: string; store: string };
      onExpand?.(knoten.id, knoten.store);
    });

    return () => {
      cy.destroy();
      kern.current = null;
    };
  }, [nodes, edges, layout, onSelect, onExpand]);

  useEffect(() => {
    const cy = kern.current;
    if (cy === null) {
      return;
    }
    cy.elements().unselect();
    if (selected !== undefined) {
      cy.getElementById(selected).select();
    }
  }, [selected]);

  return (
    <div
      ref={behaelter}
      data-testid="graph-canvas"
      aria-label="Graph"
      className="h-full w-full bg-slate-50"
    />
  );
}
