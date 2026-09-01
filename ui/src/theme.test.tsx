/**
 * Die visuelle Kodierung als Zusicherung (§17.2).
 *
 * §17.2 legt fest, dass Typ über Farbe und Provenienz über Linienfarbe kodiert werden. Das ist
 * eine Aussage über das Bild, und eine Aussage lässt sich prüfen. Vor allem eine: In dieser
 * Oberfläche ist Rot für "sieh her" reserviert — für die Marke, die Hauptaktion und alles, was
 * auf einen Menschen wartet. Bekäme ein Konzepttyp dieselbe Farbe, verlöre die gestrichelte rote
 * Kante ihre Bedeutung, und Leitprinzip 6 wäre nur noch ein Kommentar.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { motorFuer } from "./components/GraphCanvas";
import { GraphLegend } from "./components/GraphLegend";
import {
  SIGNAL,
  TYPTOENE,
  farbeFuerKante,
  farbeFuerTyp,
  istModellvorschlag,
  istUnbestaetigt,
} from "./theme";
import { kante, renderMitQuery } from "./test-support";

describe("Typfarben", () => {
  it("gibt demselben Typ immer denselben Ton", () => {
    expect(farbeFuerTyp("Confluence Page")).toBe(farbeFuerTyp("Confluence Page"));
  });

  it("bleibt in der festgelegten Reihe und erfindet keine Farbe", () => {
    const gewaehlt = ["Note", "Project", "Jira Issue", "Confluence Page", "Decision"].map((typ) =>
      farbeFuerTyp(typ),
    );

    expect(gewaehlt.every((ton) => TYPTOENE.includes(ton))).toBe(true);
  });

  it("vergibt entlang der Taxonomie unterscheidbare Töne", () => {
    // Der Grund für den zweiten Parameter: Über den Hash allein fielen `Confluence Page` und
    // `Jira Issue` auf denselben Ton — zwei verschiedene Dinge in einer Farbe, und damit war die
    // Kodierung aus §17.2 an genau der Stelle wirkungslos, an der man sie braucht.
    const taxonomie = ["Confluence Page", "Jira Issue", "Note", "Project", "Decision"];
    const toene = taxonomie.map((typ) => farbeFuerTyp(typ, taxonomie));

    expect(new Set(toene).size).toBe(taxonomie.length);
  });

  it("hält die Farbe eines Typs fest, wenn ein Filter andere ausblendet", () => {
    // Die Farbe hängt am Platz in der *vollen* Taxonomie und nicht am gezeigten Ausschnitt.
    const taxonomie = ["Confluence Page", "Jira Issue", "Note"];

    expect(farbeFuerTyp("Note", taxonomie)).toBe(farbeFuerTyp("Note", taxonomie));
    expect(farbeFuerTyp("Note", taxonomie)).not.toBe(farbeFuerTyp("Note", ["Note"]));
  });

  it("fällt für einen unbekannten Typ auf den Hash zurück, statt farblos zu bleiben", () => {
    expect(TYPTOENE).toContain(farbeFuerTyp("Unbekannt", ["Note"]));
  });

  it("vergibt niemals das Signalrot an einen Typ", () => {
    // Die eigentliche Zusicherung dieser Datei.
    const viele = Array.from({ length: 400 }, (_, index) => farbeFuerTyp(`Typ ${index}`));

    expect(viele).not.toContain(SIGNAL.normal);
    expect(viele).not.toContain(SIGNAL.dunkel);
  });

  it("hebt Cluster aus der Reihe heraus — sie sind Behälter, keine Inhalte", () => {
    expect(TYPTOENE).not.toContain(farbeFuerTyp("Cluster"));
  });
});

describe("Kantenfarben", () => {
  it("unterscheidet Hand, Code und Modell", () => {
    const [hand, code, modell] = [
      farbeFuerKante(null),
      farbeFuerKante("code:sync"),
      farbeFuerKante("gemini:m/relation_extraction@v1"),
    ];

    expect(new Set([hand, code, modell]).size).toBe(3);
  });

  it("färbt allein den Modellvorschlag rot — er ist der, der wartet", () => {
    expect(farbeFuerKante("gemini:m/relation_extraction@v1")).toBe(SIGNAL.normal);
    expect(farbeFuerKante(null)).not.toBe(SIGNAL.normal);
    expect(farbeFuerKante("code:sync")).not.toBe(SIGNAL.normal);
  });

  it("hält eine bestätigte Modellkante nicht mehr für offen (Leitprinzip 6)", () => {
    expect(istUnbestaetigt(kante() as never)).toBe(true);
    expect(istUnbestaetigt({ ...kante(), verified_at: "2026-03-01T12:00:00+00:00" } as never)).toBe(
      false,
    );
    expect(istUnbestaetigt({ ...kante(), curated: true } as never)).toBe(false);
    expect(istUnbestaetigt({ ...kante(), generated_by: null } as never)).toBe(false);
  });
});

describe("Legende", () => {
  it("nennt nur die Typen, die im Ausschnitt vorkommen", () => {
    renderMitQuery(<GraphLegend typen={["Cluster", "Note"]} />);

    expect(screen.getByText("Cluster")).toBeInTheDocument();
    expect(screen.getByText("Note")).toBeInTheDocument();
    expect(screen.queryByText("Confluence Page")).not.toBeInTheDocument();
  });

  it("erklärt Form, Herkunft und Größe — sonst wäre die Kodierung nur Zierde", () => {
    renderMitQuery(<GraphLegend typen={[]} />);

    expect(screen.getByText("persönlich")).toBeInTheDocument();
    expect(screen.getByText("Modellvorschlag")).toBeInTheDocument();
    expect(screen.getByText(/Größe = Gewicht/)).toBeInTheDocument();
  });
});

describe("Modellvorschlag gegen Ableitung", () => {
  it("hält eine Clustering-Kante für unbestätigt, aber nicht für einen Vorschlag", () => {
    // Der Unterschied entscheidet, wo Rot stehen darf. Ein großes Cluster brächte sonst zwanzig
    // rote Zeilen ins Detailpanel — und danach hieße Rot dort nichts mehr.
    const ausCode = { ...kante(), generated_by: "code:clustering" };

    expect(istUnbestaetigt(ausCode as never)).toBe(true);
    expect(istModellvorschlag(ausCode as never)).toBe(false);
  });

  it("hält eine Modellrelation für beides", () => {
    expect(istModellvorschlag(kante() as never)).toBe(true);
  });

  it("hält eine bestätigte Modellrelation für keines von beidem", () => {
    const bestaetigt = { ...kante(), verified_at: "2026-03-01T12:00:00+00:00" };

    expect(istModellvorschlag(bestaetigt as never)).toBe(false);
  });
});

describe("Layout-Motor (gemessen, nicht geschätzt)", () => {
  it("simuliert kleine Graphen und rechnet große", () => {
    // Die Grenze ist keine Vorsicht, sondern eine Messung: `cola` läuft bei 300 Knoten mit
    // 22 Bildern je Sekunde, bei 2000 mit einem — und das Mausrad antwortete dort erst nach
    // 7,9 Sekunden. `fcose` schafft dieselben 2000 Knoten mit 140.
    expect(motorFuer(50)).toBe("cola");
    expect(motorFuer(400)).toBe("cola");
    expect(motorFuer(401)).toBe("fcose");
    expect(motorFuer(2000)).toBe("fcose");
  });

  it("bleibt beim Ziehen bei der Simulation — nur sie verformt den Graphen", () => {
    expect(motorFuer(2000, true)).toBe("cola");
  });
});
