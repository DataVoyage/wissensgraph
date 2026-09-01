/** Die Bedienleiste der Zeichenfläche (§17.2, "Layouts") — Regler, Umschalter, Zähler. */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PHYSIK_VORGABE } from "./GraphCanvas";
import { GraphControls, type GraphControlsProps } from "./GraphControls";

function aufbauen(overrides: Partial<GraphControlsProps> = {}) {
  const props: GraphControlsProps = {
    layout: "physik",
    onLayout: vi.fn(),
    physik: PHYSIK_VORGABE,
    onPhysik: vi.fn(),
    labels: true,
    onLabels: vi.fn(),
    onEinpassen: vi.fn(),
    knoten: 12,
    kanten: 30,
    ...overrides,
  };
  render(<GraphControls {...props} />);
  return props;
}

describe("GraphControls", () => {
  it("meldet einen Layoutwechsel nach oben", async () => {
    const props = aufbauen();
    await userEvent.click(screen.getByRole("button", { name: "hierarchisch" }));
    expect(props.onLayout).toHaveBeenCalledWith("breadthfirst");
  });

  it("öffnet die Regler nur zur Live-Simulation und meldet jede Änderung", async () => {
    const props = aufbauen();
    await userEvent.click(screen.getByRole("button", { name: "Regler" }));
    const regler = screen.getByLabelText("Kantenlänge");
    fireEvent.change(regler, { target: { value: "200" } });
    expect(props.onPhysik).toHaveBeenCalledWith({ ...PHYSIK_VORGABE, kantenlaenge: 200 });
  });

  it("sperrt die Regler außerhalb der Live-Simulation", () => {
    aufbauen({ layout: "concentric" });
    expect(screen.getByRole("button", { name: "Regler" })).toBeDisabled();
  });

  it("schaltet Beschriftungen um und rückt auf Wunsch alles ins Bild", async () => {
    const props = aufbauen();
    await userEvent.click(screen.getByRole("button", { name: "Titel" }));
    await userEvent.click(screen.getByRole("button", { name: "Alles zeigen" }));
    expect(props.onLabels).toHaveBeenCalledWith(false);
    expect(props.onEinpassen).toHaveBeenCalledTimes(1);
  });

  it("weist einen gedeckelten Ausschnitt als solchen aus (§17.3)", () => {
    aufbauen({ gedeckelt: true });
    expect(screen.getByText("(Ausschnitt)")).toBeInTheDocument();
    expect(screen.getByText(/12 Knoten · 30 Kanten/)).toBeInTheDocument();
  });
});
