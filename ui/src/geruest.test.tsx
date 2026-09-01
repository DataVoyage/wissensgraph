/**
 * Das App-Gerüst (§17.5): Navigationsleiste, Inspektor, Werkbank, Komponentensatz.
 *
 * Die Tests laufen im echten Browser. Das ist hier keine Formalie: Das Ziehen des Inspektors
 * sind echte Zeigerereignisse auf einem echten Layout, und `localStorage` ist der echte Speicher
 * des Browsers — beides hatte in einer DOM-Simulation nur so getan.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BEREICHE, bereichVon } from "./bereiche";
import { Auswahl, Fehler, Feld, Leer, Schaltflaeche } from "./components/basis";
import { Inspektor } from "./components/Inspektor";
import { NavRail } from "./components/NavRail";
import { begrenzteBreite, INSPEKTOR_MAX, INSPEKTOR_MIN, useWerkbank } from "./werkbank";

beforeEach(() => {
  window.localStorage.clear();
});

describe("Arbeitsbereiche (§17.5)", () => {
  it("ordnet jede Ansicht genau einem Bereich zu", () => {
    const alle = BEREICHE.flatMap((bereich) => bereich.ansichten.map((a) => a.name));
    expect(new Set(alle).size).toBe(alle.length);
    expect(bereichVon("graph").name).toBe("erkunden");
    expect(bereichVon("kuration").name).toBe("analysieren");
    expect(bereichVon("betrieb").name).toBe("verwalten");
  });

  it("zeigt die drei Bereiche mit ihren Ansichten", () => {
    render(
      <NavRail
        aktiv="graph"
        breit={true}
        offeneKuration={0}
        onNavigieren={() => undefined}
        onBreite={() => undefined}
      />,
    );
    for (const label of ["Erkunden", "Analysieren", "Verwalten"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Dokumente" })).toBeInTheDocument();
  });

  it("hält den Kurationszähler auch in der schmalen Leiste sichtbar", () => {
    render(
      <NavRail
        aktiv="graph"
        breit={false}
        offeneKuration={7}
        onNavigieren={() => undefined}
        onBreite={() => undefined}
      />,
    );
    // Schmal tragen die Einträge nur Monogramme — der Name bleibt als Beschriftung erhalten.
    expect(screen.getByRole("button", { name: "Kuration" })).toBeInTheDocument();
    expect(screen.getByTitle("7 offene Posten")).toBeInTheDocument();
  });

  it("meldet Navigation und Breitenwechsel nach oben", async () => {
    const navigiert: string[] = [];
    const breiten: boolean[] = [];
    render(
      <NavRail
        aktiv="graph"
        breit={true}
        offeneKuration={0}
        onNavigieren={(view) => navigiert.push(view)}
        onBreite={(breit) => breiten.push(breit)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Betrieb" }));
    await userEvent.click(screen.getByRole("button", { name: "Navigation breit" }));
    expect(navigiert).toEqual(["betrieb"]);
    expect(breiten).toEqual([false]);
  });
});

describe("Inspektor (§17.5)", () => {
  function aufbauen(overrides: Partial<Parameters<typeof Inspektor>[0]> = {}) {
    const breiten: number[] = [];
    const zustaende: boolean[] = [];
    render(
      <Inspektor
        titel="Inspektor"
        breite={340}
        zu={false}
        onBreite={(breite) => breiten.push(breite)}
        onZu={(zu) => zustaende.push(zu)}
        {...overrides}
      >
        <p>Inhalt</p>
      </Inspektor>,
    );
    return { breiten, zustaende };
  }

  it("klappt ein und wieder aus", async () => {
    const { zustaende } = aufbauen();
    await userEvent.click(screen.getByRole("button", { name: "Inspektor einklappen" }));
    expect(zustaende).toEqual([true]);
  });

  it("zeigt eingeklappt nur den Öffner — der Inhalt ist wirklich weg", () => {
    aufbauen({ zu: true });
    expect(screen.queryByText("Inhalt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Inspektor öffnen" })).toBeInTheDocument();
  });

  it("wird durch Ziehen des Griffs breiter — nach links heißt breiter", () => {
    const { breiten } = aufbauen();
    const griff = screen.getByRole("separator", { name: "Inspektorbreite" });
    fireEvent.pointerDown(griff, { clientX: 800, pointerId: 1 });
    fireEvent.pointerMove(griff, { clientX: 740, pointerId: 1 });
    fireEvent.pointerUp(griff, { pointerId: 1 });
    expect(breiten).toEqual([400]);
  });

  it("ignoriert Bewegung ohne angefassten Griff", () => {
    const { breiten } = aufbauen();
    const griff = screen.getByRole("separator", { name: "Inspektorbreite" });
    fireEvent.pointerMove(griff, { clientX: 100, pointerId: 1 });
    expect(breiten).toEqual([]);
  });

  it("lässt sich auch mit der Tastatur ziehen (§17.2 Ansicht 4)", () => {
    const { breiten } = aufbauen();
    const griff = screen.getByRole("separator", { name: "Inspektorbreite" });
    fireEvent.keyDown(griff, { key: "ArrowLeft" });
    fireEvent.keyDown(griff, { key: "ArrowRight" });
    expect(breiten).toEqual([356, 324]);
  });
});

describe("Werkbank", () => {
  it("hält die Breite in den Grenzen — auch eine gemerkte von einem anderen Schirm", () => {
    expect(begrenzteBreite(100)).toBe(INSPEKTOR_MIN);
    expect(begrenzteBreite(9000)).toBe(INSPEKTOR_MAX);
    expect(begrenzteBreite(400)).toBe(400);
    expect(begrenzteBreite(Number.NaN)).toBe(340);
  });

  it("merkt sich Änderungen über localStorage", async () => {
    function Probe(): JSX.Element {
      const [werkbank, aendern] = useWerkbank();
      return (
        <button type="button" onClick={() => aendern({ railBreit: false, inspektorBreite: 9000 })}>
          {werkbank.railBreit ? "breit" : "schmal"}:{werkbank.inspektorBreite}
        </button>
      );
    }
    render(<Probe />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button")).toHaveTextContent(`schmal:${INSPEKTOR_MAX}`);
    const gemerkt = JSON.parse(window.localStorage.getItem("wg.werkbank") ?? "{}");
    expect(gemerkt.railBreit).toBe(false);
    expect(gemerkt.inspektorBreite).toBe(INSPEKTOR_MAX);
  });

  it("übersteht einen kaputten Eintrag im Speicher", () => {
    window.localStorage.setItem("wg.werkbank", "{kein json");
    function Probe(): JSX.Element {
      const [werkbank] = useWerkbank();
      return <p>{werkbank.inspektorBreite}</p>;
    }
    render(<Probe />);
    expect(screen.getByText("340")).toBeInTheDocument();
  });
});

describe("Komponentensatz", () => {
  it("eine beschäftigte Schaltfläche ist gesperrt und sagt das auch", () => {
    const geklickt = vi.fn();
    render(
      <Schaltflaeche art="primaer" beschaeftigt onClick={geklickt}>
        Speichern
      </Schaltflaeche>,
    );
    const knopf = screen.getByRole("button", { name: "Speichern" });
    expect(knopf).toBeDisabled();
    expect(knopf).toHaveAttribute("aria-busy", "true");
  });

  it("eine Schaltfläche ohne type ist 'button' und löst kein Formular aus", async () => {
    const abgeschickt = vi.fn((ereignis: { preventDefault: () => void }) =>
      ereignis.preventDefault(),
    );
    render(
      <form onSubmit={abgeschickt}>
        <Schaltflaeche>Nur ein Knopf</Schaltflaeche>
      </form>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Nur ein Knopf" }));
    expect(abgeschickt).not.toHaveBeenCalled();
  });

  it("ein gesperrtes Feld ist sichtbar gesperrt, nicht nur schreibgeschützt (§17.3)", () => {
    render(<Feld label="Titel" gesperrt value="aus der Quelle" onChange={() => undefined} />);
    const feld = screen.getByLabelText("Titel");
    expect(feld).toHaveAttribute("readonly");
    expect(feld.className).toContain("wg-locked");
  });

  it("die Auswahl trägt Optionen und den leeren Eintrag", () => {
    render(
      <Auswahl
        label="Scope"
        leer="alle"
        optionen={["engineering", { wert: "personal", text: "persönlich" }]}
        value=""
        onChange={() => undefined}
      />,
    );
    const auswahl = screen.getByLabelText<HTMLSelectElement>("Scope");
    expect([...auswahl.options].map((option) => option.textContent)).toEqual([
      "alle",
      "engineering",
      "persönlich",
    ]);
  });

  it("Leerzustand und Fehler sind gestaltete Zustände", () => {
    render(
      <>
        <Leer titel="Nichts da.">Ein Häkchen weniger hilft.</Leer>
        <Fehler>kaputt</Fehler>
      </>,
    );
    expect(screen.getByText("Nichts da.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("kaputt");
  });
});
