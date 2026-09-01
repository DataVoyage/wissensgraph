/**
 * Die Anordnung des Graphen — reine Logik, ohne WebGL und ohne Zeichenfläche.
 *
 * Alles, was sich *berechnen* lässt, steht hier und ist ohne Browser-Fähigkeiten prüfbar:
 * der Abgleich der Daten in das graphology-Modell (unter Erhalt der Positionen), die
 * Übersetzung der Physik-Regler in ForceAtlas2-Einstellungen und die beiden geometrischen
 * Layouts (konzentrisch, hierarchisch). Die Zeichenfläche (`GraphCanvas`) ruft nur noch auf.
 *
 * Die visuelle Kodierung ist die aus §17.2:
 *
 * | Merkmal | Kodierung |
 * |---|---|
 * | Store | Knotenform — Kreis für `shared`, Raute für `personal` |
 * | Typ | Knotenfarbe (`theme.ts`) |
 * | Gewicht (Score bzw. Grad) | Knotengröße |
 * | Kantenart | Linienstärke — strukturell (`member`) kräftig, semantisch fein |
 * | Provenienz | Linienfarbe — manuell / Code / Modell |
 * | unbestätigte Modellkante | voll deckend, alles Bestätigte tritt halbtransparent zurück |
 *
 * Die letzten beiden Zeilen sind gegenüber Cytoscape **geändert**, und zwar bewusst: WebGL
 * kennt keine gestrichelten Linien, und ein eigenes Kantenprogramm dafür wäre mehr Fläche als
 * Aussage. Deckkraft trägt dieselbe Botschaft — was auf einen Menschen wartet, steht vorn;
 * was geprüft ist, tritt zurück (Leitprinzip 6). Spezifikation und `theme.ts` sind auf diese
 * Fassung gehoben.
 */

import type Graph from "graphology";

import type { Edge } from "../api/types";
import { farbeFuerKante, farbeFuerTyp, istUnbestaetigt } from "../theme";

/** Die Layouts aus §17.2: kraftbasiert (die Physik), konzentrisch, hierarchisch. */
export type LayoutName = "physik" | "concentric" | "breadthfirst";

/** Die Stellschrauben der Simulation — §17.2 verlangt steuerbare Layouts, nicht ein festes. */
export interface PhysikWerte {
  /** Wie stark sich Knoten abstoßen; wirkt als Abstand. */
  abstossung: number;
  /** Wunschlänge einer Kante — in ForceAtlas2 indirekt über die Skalierung. */
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
 * keinen Grad (siehe `graph.py`). Was beide haben, ist *ein* Gewicht zwischen 0 und 1, und
 * welche Größe es bedeutet, entscheidet die Ansicht — nicht die Zeichenfläche.
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

/** Ab wie vielen Knoten ForceAtlas2 die Barnes-Hut-Näherung benutzt. */
export const BARNES_HUT_AB = 500;

/**
 * Wie lange sich die Simulation sortieren darf, bevor sie stehen bleibt.
 *
 * Der Worker rechnet zwar neben dem UI-Thread — aber ein Graph, der nie zur Ruhe kommt, ist
 * keine Antwort, sondern ein Bildschirmschoner. Große Graphen brauchen länger als kleine.
 */
export function einschwingzeitMs(anzahl: number): number {
  return Math.min(8000, 2500 + anzahl);
}

/**
 * Die Physik-Regler, übersetzt in ForceAtlas2.
 *
 * ForceAtlas2 kennt keine Wunschlänge je Kante; der Gleichgewichtsabstand entsteht aus dem
 * Verhältnis von Abstoßung (`scalingRatio`) und Anziehung. Der Kantenlängen-Regler skaliert
 * deshalb die Abstoßung quadratisch mit — dieselbe Wirkung über einen anderen Hebel. Oberhalb
 * von `BARNES_HUT_AB` rechnet die Näherung: n·log n statt n², und genau das trägt die
 * Zielmarke von 5.000 Knoten mit laufender Physik.
 *
 * **Die Vorgabe ist bewusst weit.** Der Graph beantwortet am Anfang eine strukturelle Frage —
 * *wie viele Gruppen gibt es, was hängt zusammen, was liegt allein* —, und dafür braucht das
 * Auge Zwischenraum. Eine dichte Anordnung sieht nach viel Inhalt aus und zeigt nichts: Bei
 * einigen tausend Knoten verschmelzen die Kanten zu einem Gewebe, und die Gruppen, um die es
 * geht, verschwinden darin. Ins Einzelne geht man danach über Zoom und Filter, nicht dadurch,
 * dass von Anfang an alles eng beieinandersteht.
 *
 * Konkret gegenüber der ersten Fassung: die Abstoßung dreifach, die Schwerkraft (die alles zur
 * Mitte zieht und damit verdichtet) deutlich schwächer, und `outboundAttractionDistribution`
 * aus — es zieht stark verbundene Knoten zusammen und lässt genau die Ballungen entstehen, die
 * hier stören.
 */
export function fa2Einstellungen(
  physik: PhysikWerte,
  anzahl: number,
): Record<string, number | boolean> {
  const laenge = physik.kantenlaenge / PHYSIK_VORGABE.kantenlaenge;
  return {
    barnesHutOptimize: anzahl > BARNES_HUT_AB,
    scalingRatio: Math.max(1.5, (physik.abstossung / 1.2) * laenge * laenge),
    gravity: 0.003 + Math.max(0, Math.min(0.9, physik.schwerkraft)) * 0.03,
    strongGravityMode: false,
    // Größere Graphen werden stärker gedämpft, sonst zappelt das Bild statt zu konvergieren.
    slowDown: 4 + anzahl / 400,
    // Knoten weichen einander aus, ihr Durchmesser zählt mit — aber nur bei kleinen Graphen.
    // Bei einigen tausend Knoten zusammen mit hoher Abstoßung planiert es das Bild zu einem
    // gleichmäßigen Teppich: Jeder Knoten hält zu jedem Abstand, und genau die Gruppen, um die
    // es geht, verschwinden. Nachgesehen, nicht überlegt.
    adjustSizes: anzahl <= BARNES_HUT_AB,
    outboundAttractionDistribution: false,
    edgeWeightInfluence: 0,
  };
}

function knotenAttribute(
  knoten: CanvasNode,
  typen: readonly string[],
): Record<string, unknown> {
  const grabstein = knoten.status === "tombstone";
  const farbe = farbeFuerTyp(knoten.type, typen);
  return {
    label: knoten.title ?? knoten.id,
    store: knoten.store,
    typ: knoten.type,
    status: knoten.status,
    gewicht: knoten.gewicht,
    // Store über die Form (§17.2): der eigene Bestand als Raute, der geteilte als Kreis.
    type: knoten.store === "personal" ? "raute" : "circle",
    // Ein Grabstein bleibt sichtbar, aber blass — er ist Geschichte, kein Inhalt (§7.6).
    color: grabstein ? mitDeckkraft(farbe, 0.35) : farbe,
    // Eine Wurzelkennlinie statt einer geraden: Der Durchmesser wächst spürbar, die Fläche
    // nicht quadratisch — sonst erschlüge ein einziger schwerer Knoten das Bild.
    size: Math.round(5 + Math.sqrt(Math.max(0, Math.min(1, knoten.gewicht))) * 9),
    tombstone: grabstein,
  };
}

/** Deckkraft als Teil der Farbe — WebGL kennt keine gestrichelte Linie (siehe Kopfkommentar). */
function mitDeckkraft(farbe: string, deckkraft: number): string {
  const wert = Math.round(Math.max(0, Math.min(1, deckkraft)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${farbe}${wert}`;
}

function kantenAttribute(kante: Edge): Record<string, unknown> {
  const wartet = istUnbestaetigt(kante);
  return {
    kind: kante.kind,
    unbestaetigt: wartet,
    // Kantenart über die Stärke: `member` trägt die Struktur und ist kräftig, alles
    // Semantische ist fein. Provenienz über die Farbe; Unbestätigtes voll deckend.
    //
    // Die Stärken sind gegenüber der ersten Fassung halbiert. Sie waren für ein Dutzend Kanten
    // gewählt und wurden bei einigen tausend zum Problem: Nebeneinanderlaufende Kanten
    // verschmolzen zu Bändern, und das Bild zeigte Fläche statt Struktur. Eine Kante muss
    // sichtbar sein, wenn man sie sucht — sie muss nicht auffallen, wenn man die Gruppen sucht.
    size: (kante.kind === "member" ? 1.2 : 0.6) + (wartet ? 0.3 : 0),
    color: mitDeckkraft(farbeFuerKante(kante.generated_by), wartet ? 0.9 : 0.28),
  };
}

/**
 * Gleicht die Daten der Ansicht in das graphology-Modell ab — unter Erhalt der Positionen.
 *
 * Das ist die Regel "die Instanz überlebt eine Datenänderung" aus der Cytoscape-Fassung, nur
 * dass sie hier natürlicher liegt: Das Datenmodell ist von der Zeichnung getrennt. Vorhandene
 * Knoten behalten x/y; neue kommen an der Stelle eines schon platzierten Nachbarn zur Welt
 * und schieben sich von dort ins Bild — nicht am Nullpunkt, wo sie erst einen weiten Weg
 * durchs Bild fliegen müssten.
 */
export function spiegeln(
  graph: Graph,
  nodes: CanvasNode[],
  edges: Edge[],
  typen: readonly string[],
): { neu: number } {
  const sollKnoten = new Set(nodes.map((knoten) => knoten.id));
  const sollKanten = new Map(
    edges
      .filter((kante) => sollKnoten.has(kante.from_id) && sollKnoten.has(kante.to_id))
      .map((kante) => [kante.id, kante]),
  );

  for (const id of graph.nodes()) {
    if (!sollKnoten.has(id)) {
      graph.dropNode(id);
    }
  }
  for (const id of graph.edges()) {
    if (!sollKanten.has(id)) {
      graph.dropEdge(id);
    }
  }

  // Neue Knoten kommen mit einer vorläufigen Position zur Welt — sigma verlangt x/y schon
  // beim Anlegen. Nachgerückt (neben einen Nachbarn) wird, sobald die Kanten da sind, damit
  // auch ein Nachbar zählt, der im selben Abgleich gekommen ist.
  const spanne = Math.sqrt(Math.max(1, graph.order + nodes.length)) * 30;
  const neue: string[] = [];
  for (const knoten of nodes) {
    const attribute = knotenAttribute(knoten, typen);
    if (graph.hasNode(knoten.id)) {
      graph.mergeNodeAttributes(knoten.id, attribute);
    } else {
      graph.addNode(knoten.id, { ...attribute, x: zufall(spanne), y: zufall(spanne) });
      neue.push(knoten.id);
    }
  }

  for (const [id, kante] of sollKanten) {
    if (graph.hasEdge(id)) {
      graph.mergeEdgeAttributes(id, kantenAttribute(kante));
    } else {
      graph.addEdgeWithKey(id, kante.from_id, kante.to_id, kantenAttribute(kante));
    }
  }

  const frisch = new Set(neue);
  for (const id of neue) {
    const nachbar = graph.neighbors(id).find((kandidat) => !frisch.has(kandidat));
    if (nachbar !== undefined) {
      graph.setNodeAttribute(id, "x", (graph.getNodeAttribute(nachbar, "x") as number) + zufall(40));
      graph.setNodeAttribute(id, "y", (graph.getNodeAttribute(nachbar, "y") as number) + zufall(40));
    }
  }
  return { neu: neue.length };
}

function zufall(spanne: number): number {
  return (Math.random() - 0.5) * 2 * spanne;
}

export type Positionen = Map<string, { x: number; y: number }>;

/**
 * Ringe nach Gewicht (§17.2 "konzentrisch"): Was schwer wiegt, steht innen.
 *
 * In der Traversierung ist das Gewicht der Score und fällt mit der Hop-Distanz — die Ringe
 * zeigen dann die Entfernung vom Startknoten. In der Karte ist es der Grad: Die vernetzten
 * Knoten stehen im Zentrum, die losen am Rand.
 */
export function konzentrisch(graph: Graph, abstand: number): Positionen {
  const ringe = new Map<number, string[]>();
  for (const id of graph.nodes()) {
    const gewicht = Number(graph.getNodeAttribute(id, "gewicht")) || 0;
    const ring = Math.round((1 - Math.max(0, Math.min(1, gewicht))) * 4);
    const eintraege = ringe.get(ring) ?? [];
    eintraege.push(id);
    ringe.set(ring, eintraege);
  }

  const positionen: Positionen = new Map();
  for (const [ring, eintraege] of ringe) {
    // Der Umfang muss die Knoten fassen: Ein voller innerer Ring wird notfalls weiter.
    const radius =
      ring === 0 && eintraege.length === 1
        ? 0
        : Math.max((ring + 1) * abstand, (eintraege.length * abstand * 0.6) / (2 * Math.PI));
    eintraege.forEach((id, platz) => {
      const winkel = (2 * Math.PI * platz) / eintraege.length;
      positionen.set(id, { x: Math.cos(winkel) * radius, y: Math.sin(winkel) * radius });
    });
  }
  return positionen;
}

/**
 * Ebenen entlang der `member`-Kanten (§17.2 "hierarchisch") — als eigene Rechnung, weil
 * sigma kein hierarchisches Layout mitbringt (Konzept, Abschnitt 3.3).
 *
 * Wurzeln sind Knoten ohne eingehende `member`-Kante; jede Ebene liegt eine Zeile tiefer.
 * Was gar nicht an der Mitglieds-Struktur hängt, steht gesammelt unter dem Baum statt
 * irgendwo dazwischen.
 */
export function hierarchisch(graph: Graph, abstand: number): Positionen {
  const eltern = new Map<string, string[]>();
  for (const kante of graph.edges()) {
    if (graph.getEdgeAttribute(kante, "kind") === "member") {
      const von = graph.source(kante);
      const nach = graph.target(kante);
      const kinder = eltern.get(von) ?? [];
      kinder.push(nach);
      eltern.set(von, kinder);
    }
  }
  const hatEingang = new Set([...eltern.values()].flat());

  const ebene = new Map<string, number>();
  const warteschlange: string[] = [];
  for (const id of graph.nodes()) {
    if (eltern.has(id) && !hatEingang.has(id)) {
      ebene.set(id, 0);
      warteschlange.push(id);
    }
  }
  while (warteschlange.length > 0) {
    const id = warteschlange.shift() as string;
    for (const kind of eltern.get(id) ?? []) {
      if (!ebene.has(kind)) {
        ebene.set(kind, (ebene.get(id) ?? 0) + 1);
        warteschlange.push(kind);
      }
    }
  }

  const tiefste = Math.max(0, ...ebene.values());
  const zeilen = new Map<number, string[]>();
  for (const id of graph.nodes()) {
    const zeile = ebene.get(id) ?? tiefste + 1;
    const eintraege = zeilen.get(zeile) ?? [];
    eintraege.push(id);
    zeilen.set(zeile, eintraege);
  }

  const positionen: Positionen = new Map();
  for (const [zeile, eintraege] of zeilen) {
    eintraege.sort();
    eintraege.forEach((id, platz) => {
      positionen.set(id, {
        x: (platz - (eintraege.length - 1) / 2) * abstand,
        y: zeile * abstand * 1.4,
      });
    });
  }
  return positionen;
}
