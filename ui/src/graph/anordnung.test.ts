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
  gruppenZuordnung,
  hierarchisch,
  kantenGewicht,
  konzentrisch,
  PHYSIK_VORGABE,
  spiegeln,
  sterne,
  type CanvasNode,
  type Positionen,
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
    // Die Werte kommen trotzdem an: Das Gewicht steuert die Zeichengröße. (`size` trägt den
    // Layout-Radius für ForceAtlas2 und ist davon unabhängig — siehe `knotenAttribute`.)
    expect(Number(graph.getNodeAttribute("a", "zeichenGroesse"))).toBeGreaterThan(
      Number(graph.getNodeAttribute("b", "zeichenGroesse")),
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

  it("macht das Cluster zum größten Knoten seines Sterns — es ist der Anker", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [
        knoten("cluster:1", { type: "Cluster", gewicht: 0.2 }),
        knoten("doc", { gewicht: 1 }),
      ],
      [],
      [],
    );

    // Auch ein leichtes Cluster überragt das schwerste Dokument: Die Struktur liest sich an
    // den Ankern ab, nicht an den Mitgliedern.
    expect(Number(graph.getNodeAttribute("cluster:1", "size"))).toBeGreaterThan(
      Number(graph.getNodeAttribute("doc", "size")),
    );
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
    // strukturelle Frage, und die braucht Zwischenraum. Die Zahl ist seither gesunken, weil
    // LinLog in einer anderen Größenordnung rechnet — die Aussage ist, dass sie deutlich über
    // dem Sockel liegt und mit der Dichte mitwächst.
    expect(Number(fa2Einstellungen(PHYSIK_VORGABE, 5000).scalingRatio)).toBeGreaterThan(8);
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

  it("lässt Dissuade Hubs aus — es invertierte die Sterne", () => {
    // Der Schalter teilt die Anziehung durch den Grad des Ausgangsknotens, und die
    // Ausgangsknoten der member-Kanten sind genau die Cluster: Ein Anker mit zwanzig
    // Mitgliedern band jedes nur noch mit einem Zwanzigstel. Im echten Browser standen die
    // Anker als Ring außen, die Mitglieder ballten sich haltlos in der Mitte. Die Arbeit
    // gegen die Naben leisten die Kantengewichte und LinLog.
    expect(fa2Einstellungen(PHYSIK_VORGABE, 1000, 3700).outboundAttractionDistribution).toBe(false);
    expect(fa2Einstellungen(PHYSIK_VORGABE, 1000, 3700).linLogMode).toBe(true);
  });

  it("deckelt den Dichtefaktor — ein Klumpen wird nicht durch Sprengen besser", () => {
    const extrem = Number(fa2Einstellungen(PHYSIK_VORGABE, 100, 50_000).scalingRatio);
    const normal = Number(fa2Einstellungen(PHYSIK_VORGABE, 100, 100).scalingRatio);

    expect(extrem).toBeLessThanOrEqual(normal * 6);
  });

  it("verzichtet in der freien Simulation auf die Anticollision", () => {
    // Im echten Browser nachgesehen: Bei zweitausend Knoten planiert der hundertfache
    // Kollisionsstoß das Bild zu einem gleichmäßigen Gitter aus Punkten — jeder Knoten hält
    // zu jedem Abstand, und die Gruppen verschwinden. Gebraucht wird sie eine Ebene höher,
    // zwischen den Zentren der Sternenkarte, wo es um Hunderte statt Tausende geht.
    expect(fa2Einstellungen(PHYSIK_VORGABE, 100).adjustSizes).toBe(false);
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

describe("Kantengewicht — was zieht wie stark", () => {
  function mit(kind: string, weight: number | null): Edge {
    return { ...kante("k", "a", "b", kind), weight } as Edge;
  }

  it("bindet ein Mitglied deutlich fester an seine Gruppe als jede semantische Kante", () => {
    // Der Kern der Sache: Vorher zog jede Kante gleich stark, und deshalb hatte der Graph
    // keine Gruppen, sondern einen Mittelpunkt. Ein Cluster soll der Schwerpunkt seiner
    // Umgebung sein — ein einzelner Querverweis darf ein Mitglied nicht herausreißen.
    expect(kantenGewicht(mit("member", 0.88))).toBeGreaterThan(
      kantenGewicht(mit("references", 0.95)) * 2,
    );
  });

  it("zieht Ähnliches stärker zusammen als entfernt Verwandtes", () => {
    // Damit Nähe im Bild inhaltliche Nähe bedeutet — der eigentliche Zweck der Übung.
    expect(kantenGewicht(mit("references", 0.95))).toBeGreaterThan(
      kantenGewicht(mit("references", 0.6)) * 3,
    );
  });

  it("lässt auch die schwächste Kante noch etwas ziehen", () => {
    // Ohne Sockel zerfiele der Graph in Gruppen ganz ohne Bezug zueinander.
    expect(kantenGewicht(mit("references", 0))).toBeGreaterThan(0);
  });

  it("nimmt eine Kante ohne Ähnlichkeitswert als mittelmäßig an, nicht als bedeutungslos", () => {
    const ohne = kantenGewicht(mit("references", null));
    expect(ohne).toBeGreaterThan(kantenGewicht(mit("references", 0.3)));
    expect(ohne).toBeLessThan(kantenGewicht(mit("references", 0.9)));
  });

  it("hält verwandte Cluster als lockere Feder, weit unter einer Mitgliedschaft", () => {
    // Der Bestand ist kein Baum: Die Cluster sind auch untereinander verbunden (jedes mit
    // seinen drei nächsten, Ähnlichkeit im Mittel 0,87). Zögen diese Kanten wie semantische
    // mit voller Ähnlichkeit, pressten sie alle Sterne zu einem Ballen zusammen.
    const verwandt = kantenGewicht(mit("related", 0.87));

    expect(verwandt).toBeLessThan(kantenGewicht(mit("member", 0.87)) / 5);
    expect(verwandt).toBeLessThan(kantenGewicht(mit("references", 0.87)));
    expect(verwandt).toBeGreaterThan(0);
  });

  it("dämpft eine Kante innerhalb einer Gruppe, aber nicht eine zwischen zweien", () => {
    // Der Grund, warum jede Gruppe vorher zu einem Punkt zusammenfiel: Ein Cluster fasst
    // zusammen, was sich ähnlich ist — also haben seine Mitglieder auch untereinander Kanten,
    // und zwanzig davon ziehen den Ring in die Mitte. Zwischen zwei Gruppen ist dieselbe Kante
    // die eigentliche Aussage und zieht voll.
    const innerhalb = kantenGewicht(mit("references", 0.9), true);
    const dazwischen = kantenGewicht(mit("references", 0.9), false);

    expect(innerhalb).toBeLessThan(dazwischen / 3);
  });

  it("erkennt die Zugehörigkeit an den member-Kanten des Ausschnitts", () => {
    const zuordnung = gruppenZuordnung([
      kante("m1", "cluster:1", "a", "member"),
      kante("m2", "cluster:1", "b", "member"),
      kante("m3", "cluster:2", "c", "member"),
      kante("r1", "a", "c"),
    ]);

    expect(zuordnung.get("a")).toBe("cluster:1");
    expect(zuordnung.get("c")).toBe("cluster:2");
    // Ein Cluster ist selbst kein Mitglied — es steht auf keiner rechten Kantenseite.
    expect(zuordnung.has("cluster:1")).toBe(false);
  });

  it("dämpft im Modell nur die Kante innerhalb einer Gruppe", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [knoten("c1"), knoten("c2"), knoten("a"), knoten("b"), knoten("d")],
      [
        kante("m1", "c1", "a", "member"),
        kante("m2", "c1", "b", "member"),
        kante("m3", "c2", "d", "member"),
        { ...kante("innen", "a", "b"), weight: 0.9 } as Edge,
        { ...kante("bruecke", "b", "d"), weight: 0.9 } as Edge,
      ],
      [],
    );

    expect(Number(graph.getEdgeAttribute("innen", "weight"))).toBeLessThan(
      Number(graph.getEdgeAttribute("bruecke", "weight")),
    );
  });

  it("gibt das Gewicht an die Simulation weiter", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("a"), knoten("b")], [kante("m", "a", "b", "member")], []);

    // Über der stärksten semantischen Kante, aber deutlich unter eins: In FA2 ist ein
    // Kantengewicht zugleich Masse, und hohe Gewichte machen unverbundene Knoten relativ
    // masselos — sie fallen dann als Klumpen in die Mitte.
    expect(Number(graph.getEdgeAttribute("m", "weight"))).toBeGreaterThan(0.3);
    expect(Number(graph.getEdgeAttribute("m", "weight"))).toBeLessThan(1);
    // Ohne diese Einstellung läse ForceAtlas2 das Gewicht gar nicht erst.
    expect(fa2Einstellungen(PHYSIK_VORGABE, 100).edgeWeightInfluence).toBe(1);
  });
});

describe("Sternenkarte", () => {
  /** Ein Ersatz für `forceatlas2.assign` — legt die Zentren auf eine Reihe, deterministisch. */
  function fa2Attrappe(graph: Graph): void {
    graph.nodes().sort().forEach((id, platz) => {
      graph.setNodeAttribute(id, "x", platz * 100);
      graph.setNodeAttribute(id, "y", 0);
    });
  }

  function bestand(): Graph {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(
      graph,
      [
        knoten("cluster:a", { type: "Cluster" }),
        knoten("cluster:b", { type: "Cluster" }),
        knoten("a1"),
        knoten("a2"),
        knoten("a3"),
        knoten("b1"),
        knoten("allein"),
      ],
      [
        kante("m1", "cluster:a", "a1", "member"),
        kante("m2", "cluster:a", "a2", "member"),
        kante("m3", "cluster:a", "a3", "member"),
        kante("m4", "cluster:b", "b1", "member"),
        kante("r1", "a1", "b1"),
      ],
      [],
    );
    return graph;
  }

  const radius = (lagen: Positionen, von: string, nach: string): number => {
    const a = lagen.get(von) as { x: number; y: number };
    const b = lagen.get(nach) as { x: number; y: number };
    return Math.hypot(a.x - b.x, a.y - b.y);
  };

  it("stellt jedes Mitglied auf einen Ring um sein eigenes Zentrum", () => {
    const lagen = sterne(bestand(), PHYSIK_VORGABE, fa2Attrappe);

    const abstaende = ["a1", "a2", "a3"].map((id) => radius(lagen, "cluster:a", id));
    // Alle drei gleich weit vom Anker — und keiner an derselben Stelle wie ein anderer.
    expect(Math.max(...abstaende) - Math.min(...abstaende)).toBeLessThan(0.001);
    expect(radius(lagen, "a1", "a2")).toBeGreaterThan(1);
  });

  it("hält ein Mitglied bei seinem Zentrum, auch wenn es woandershin verweist", () => {
    // Der Punkt, an dem die freie Simulation scheiterte: `a1` verweist auf `b1` im anderen
    // Stern. Für die *Darstellung* zählt allein die Zugehörigkeit — ein Verweis ist eine
    // Aussage über Inhalte, keine über Orte. Sichtbar bleibt er als Linie.
    const lagen = sterne(bestand(), PHYSIK_VORGABE, fa2Attrappe);

    expect(radius(lagen, "cluster:a", "a1")).toBeLessThan(radius(lagen, "cluster:b", "a1"));
  });

  it("stellt Unverbundenes außerhalb aller Sterne", () => {
    const lagen = sterne(bestand(), PHYSIK_VORGABE, fa2Attrappe);
    const weite = (id: string): number => {
      const punkt = lagen.get(id) as { x: number; y: number };
      return Math.hypot(punkt.x, punkt.y);
    };

    // §15.1 sichtbar gemacht: Was an nichts hängt, steht am Rand und nicht im Gedränge.
    expect(weite("allein")).toBeGreaterThan(Math.max(weite("a1"), weite("b1"), weite("cluster:a")));
  });

  it("gibt einem großen Stern einen größeren Ring als einem kleinen", () => {
    const lagen = sterne(bestand(), PHYSIK_VORGABE, fa2Attrappe);

    expect(radius(lagen, "cluster:a", "a1")).toBeGreaterThan(radius(lagen, "cluster:b", "b1"));
  });

  it("kommt ohne jedes Zentrum zurecht", () => {
    const graph = new Graph({ multi: true, type: "directed" });
    spiegeln(graph, [knoten("x"), knoten("y")], [], []);

    const lagen = sterne(graph, PHYSIK_VORGABE, fa2Attrappe);

    expect(lagen.size).toBe(2);
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
    expect(einschwingzeitMs(100000)).toBe(25_000);
    // Der Vollbestand (§17.2: die Karte lädt im Default alles) muss real fertig sortieren:
    // Mit dem alten Deckel stoppte die Simulation bei 2.210 Knoten nach 4,7 s mitten in der
    // Bewegung, und jedes Bild war das Standbild eines halb entwirrten Knäuels.
    expect(einschwingzeitMs(2210)).toBeGreaterThan(15_000);
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
