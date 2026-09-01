/**
 * Die Anordnungslogik — bibliotheksfrei geprüft, auf graphology-Ebene (Konzept, 3.3).
 *
 * Hier steht, was die Zeichenfläche *rechnet*; ob sie es auch *zeichnet*, beweisen die
 * Playwright-Läufe im echten Browser.
 */

import Graph from "graphology";
import { describe, expect, it } from "vitest";

import type { Edge } from "../api/types";
import {
  einschwingzeitMs,
  fa2Einstellungen,
  hierarchisch,
  konzentrisch,
  PHYSIK_VORGABE,
  spiegeln,
  type CanvasNode,
} from "./anordnung";
import { NodeRautenProgram, shaderTauschMoeglich } from "./rauten";

function knoten(id: string, overrides: Partial<CanvasNode> = {}): CanvasNode {
  return { id, store: "shared", type: "Note", title: id, status: "stable", gewicht: 0.5, ...overrides };
}

function kante(id: string, von: string, nach: string, kind = "references"): Edge {
  return {
    id,
    from_store: "shared",
    from_id: von,
    to_store: "shared",
    to_id: nach,
    kind,
    weight: null,
    confidence: null,
    reasoning: null,
    resolved: true,
    generated_by: null,
    verified_by: null,
    verified_at: null,
    curated: true,
    created_at: "2026-03-01T12:00:00+00:00",
  } as Edge;
}

describe("spiegeln", () => {
  it("erhält die Position vorhandener Knoten über den Abgleich hinweg", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("a")], [], []);
    graph.setNodeAttribute("a", "x", 123);
    graph.setNodeAttribute("a", "y", -7);

    spiegeln(graph, [knoten("a", { gewicht: 1 }), knoten("b")], [], []);

    expect(graph.getNodeAttribute("a", "x")).toBe(123);
    expect(graph.getNodeAttribute("a", "y")).toBe(-7);
    // Die Werte kommen trotzdem an: Das Gewicht steuert die Größe.
    expect(Number(graph.getNodeAttribute("a", "size"))).toBeGreaterThan(
      Number(graph.getNodeAttribute("b", "size")),
    );
  });

  it("lässt einen neuen Knoten neben seinem Nachbarn zur Welt kommen", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("a")], [], []);
    graph.setNodeAttribute("a", "x", 1000);
    graph.setNodeAttribute("a", "y", 1000);

    spiegeln(graph, [knoten("a"), knoten("b")], [kante("k1", "a", "b")], []);

    expect(Math.abs(Number(graph.getNodeAttribute("b", "x")) - 1000)).toBeLessThanOrEqual(40);
    expect(Math.abs(Number(graph.getNodeAttribute("b", "y")) - 1000)).toBeLessThanOrEqual(40);
  });

  it("entfernt, was nicht mehr im Ausschnitt ist — Kanten ohne beide Enden inbegriffen", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("a"), knoten("b")], [kante("k1", "a", "b")], []);

    spiegeln(graph, [knoten("a")], [kante("k1", "a", "b")], []);

    expect(graph.hasNode("b")).toBe(false);
    expect(graph.hasEdge("k1")).toBe(false);
  });

  it("kodiert den Store über die Form: personal ist die Raute (§17.2)", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("a"), knoten("n", { store: "personal" })], [], []);

    expect(graph.getNodeAttribute("a", "type")).toBe("circle");
    expect(graph.getNodeAttribute("n", "type")).toBe("raute");
  });

  it("hebt Unbestätigtes über die Deckkraft hervor — der Ersatz für die Strichelung", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    const wartet = {
      ...kante("k1", "a", "b"),
      generated_by: "gemini:m/relation_extraction@v1",
      curated: false,
    };
    spiegeln(graph, [knoten("a"), knoten("b")], [wartet, kante("k2", "b", "a", "member")], []);

    const unbestaetigt = String(graph.getEdgeAttribute("k1", "color"));
    const bestaetigt = String(graph.getEdgeAttribute("k2", "color"));
    // 8-stellige Hex-Farbe: die letzten zwei Stellen sind die Deckkraft.
    expect(parseInt(unbestaetigt.slice(7), 16)).toBeGreaterThan(parseInt(bestaetigt.slice(7), 16));
    // Kantenart über die Stärke: `member` trägt die Struktur und ist kräftiger als eine
    // semantische Kante. Bewusst relativ geprüft und nicht gegen eine feste Pixelzahl — die
    // absoluten Stärken sind eine Gestaltungsfrage und wurden schon einmal halbiert, weil
    // tausende Kanten sonst zu Bändern verschmelzen. Die *Aussage* ist das Verhältnis.
    expect(Number(graph.getEdgeAttribute("k2", "size"))).toBeGreaterThan(
      Number(graph.getEdgeAttribute("k1", "size")),
    );
  });
});

describe("Abstand und Kantendichte (nachgesehen, nicht überlegt)", () => {
  it("spreizt weit genug, dass Gruppen auseinandertreten", () => {
    // Die erste Fassung stand bei 7 und zeichnete einen Knäuel: Die Themen klebten aneinander,
    // und die Kanten dazwischen waren ein Gewebe. Der Graph beantwortet aber zuerst eine
    // strukturelle Frage, und die braucht Zwischenraum.
    expect(Number(fa2Einstellungen(PHYSIK_VORGABE, 5000).scalingRatio)).toBeGreaterThan(15);
  });

  it("zieht nur schwach zur Mitte — Schwerkraft verdichtet", () => {
    expect(Number(fa2Einstellungen(PHYSIK_VORGABE, 5000).gravity)).toBeLessThan(0.03);
  });

  it("spreizt dicht vernetzte Bestände stärker als dünne", () => {
    // An echten Daten gelernt: Ein Bestand mit mittlerem Grad 7,4 ballte sich zum Klumpen,
    // während dieselbe Einstellung bei Grad 2,5 gut aussah. In FA2 summiert sich die Anziehung
    // über die Kanten, die Abstoßung über die Knoten — wer die Kanten verdreifacht, zieht
    // denselben Graphen ohne Ausgleich dreifach zusammen.
    const duenn = Number(fa2Einstellungen(PHYSIK_VORGABE, 1000, 1250).scalingRatio);
    const dicht = Number(fa2Einstellungen(PHYSIK_VORGABE, 1000, 3700).scalingRatio);

    expect(dicht).toBeGreaterThan(duenn * 2);
  });

  it("nimmt Naben nur im dichten Bestand die Anziehung", () => {
    // In Gephi "Dissuade Hubs". An echten Daten war das der Hebel, der den Klumpen wirklich
    // löste: Mehr Abstoßung allein spreizte nur den ganzen Ballen, die Gruppen blieben
    // ineinander. Erst geteilt durch den Grad des Ausgangsknotens traten sie auseinander.
    // Im dünnen Bestand ist dieselbe Einstellung schädlich — sie erzeugt dort Ballungen.
    expect(fa2Einstellungen(PHYSIK_VORGABE, 1000, 3700).outboundAttractionDistribution).toBe(true);
    expect(fa2Einstellungen(PHYSIK_VORGABE, 1000, 1000).outboundAttractionDistribution).toBe(false);
  });

  it("deckelt den Dichtefaktor — ein Klumpen wird nicht durch Sprengen besser", () => {
    const extrem = Number(fa2Einstellungen(PHYSIK_VORGABE, 100, 50_000).scalingRatio);
    const normal = Number(fa2Einstellungen(PHYSIK_VORGABE, 100, 100).scalingRatio);

    expect(extrem).toBeLessThanOrEqual(normal * 6);
  });

  it("lässt Knoten nur bei kleinen Graphen einander ausweichen", () => {
    // `adjustSizes` zusammen mit hoher Abstoßung planiert große Graphen zu einem gleichmäßigen
    // Teppich — jeder Knoten hält zu jedem Abstand, und die Gruppen verschwinden.
    expect(fa2Einstellungen(PHYSIK_VORGABE, 100).adjustSizes).toBe(true);
    expect(fa2Einstellungen(PHYSIK_VORGABE, 5000).adjustSizes).toBe(false);
  });

  it("hält Kanten fein genug, dass sie nicht zu Bändern verschmelzen", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [knoten("a"), knoten("b")],
      [kante("m", "a", "b", "member"), kante("r", "b", "a")],
      [],
    );
    // Auch die kräftigste Kantenart bleibt schlank; den Rest besorgt `minEdgeThickness` in
    // der Zeichenfläche, das sigma sonst bei 1,7 Pixeln festhält.
    expect(Number(graph.getEdgeAttribute("m", "size"))).toBeLessThanOrEqual(1.5);
  });
});

describe("hierarchisch", () => {
  it("legt Ebenen entlang der member-Kanten und sammelt Unverbundenes darunter", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [knoten("wurzel"), knoten("kind1"), knoten("kind2"), knoten("enkel"), knoten("lose")],
      [
        kante("m1", "wurzel", "kind1", "member"),
        kante("m2", "wurzel", "kind2", "member"),
        kante("m3", "kind1", "enkel", "member"),
        kante("r1", "kind2", "lose"),
      ],
      [],
    );
    const lage = hierarchisch(graph, 100);

    const y = (id: string): number => (lage.get(id) as { y: number }).y;
    expect(y("wurzel")).toBeLessThan(y("kind1"));
    expect(y("kind1")).toBe(y("kind2"));
    expect(y("kind1")).toBeLessThan(y("enkel"));
    // "lose" hängt nur semantisch — sie steht unter dem Baum, nicht mittendrin.
    expect(y("lose")).toBeGreaterThan(y("enkel"));
  });
});

describe("konzentrisch", () => {
  it("stellt das Schwere nach innen und das Leichte nach außen", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [knoten("zentrum", { gewicht: 1 }), knoten("rand1", { gewicht: 0 }), knoten("rand2", { gewicht: 0.05 })],
      [],
      [],
    );
    const lage = konzentrisch(graph, 80);

    const radius = (id: string): number => {
      const punkt = lage.get(id) as { x: number; y: number };
      return Math.hypot(punkt.x, punkt.y);
    };
    expect(radius("zentrum")).toBeLessThan(radius("rand1"));
    expect(radius("rand2")).toBeGreaterThan(radius("zentrum"));
  });
});

describe("Einschwingzeit und Rautenform", () => {
  it("gibt großen Graphen mehr Zeit, aber nie endlos", () => {
    expect(einschwingzeitMs(100)).toBeLessThan(einschwingzeitMs(5000));
    expect(einschwingzeitMs(100000)).toBe(8000);
  });

  it("der Shader-Tausch für die Raute greift in dieser sigma-Version", () => {
    // Bricht eine neue sigma-Version die Zeile, fällt dieser Test — und nicht stillschweigend
    // die Kodierung "Store über Knotenform" aus §17.2.
    expect(shaderTauschMoeglich()).toBe(true);
    const definition = NodeRautenProgram.prototype.getDefinition.call(
      Object.create(NodeRautenProgram.prototype) as NodeRautenProgram,
    );
    expect(definition.FRAGMENT_SHADER_SOURCE).toContain("abs(v_diffVector.x)");
  });
});
