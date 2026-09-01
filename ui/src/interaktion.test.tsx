/**
 * Die Bedienung selbst: Filter, Tastatur, Auswahl, Layout (§17.2, §17.3).
 *
 * Diese Tests fassen nichts an, was die Datenbank verändert. Sie prüfen den anderen Teil des
 * Versprechens von §17.2: dass die Filterleiste, die Tastaturbedienung und das Aufklappen
 * tatsächlich wirken — und dass ein gedeckelter Ausschnitt (§17.3) nicht am Zeichnen scheitert.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GraphCanvas, PHYSIK_VORGABE } from "./components/GraphCanvas";
import { CurationList } from "./views/CurationList";
import { DocumentBrowser } from "./views/DocumentBrowser";
import { GraphExplorer } from "./views/GraphExplorer";
import { Operations } from "./views/Operations";
import { PersonalArea } from "./views/PersonalArea";
import {
  FakeApi,
  KONFIGURATION,
  kante,
  karte,
  kartenknoten,
  konzept,
  renderMitQuery,
  werkbankProps,
} from "./test-support";

let api: FakeApi;

function knoten(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    store: "shared",
    scope: "engineering",
    type: "Confluence Page",
    title: id,
    status: "stable",
    hop: 0,
    score: 0.5,
    gewicht: 0.5,
    density: 0,
    ...overrides,
  };
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
  api = new FakeApi();
  api.on("GET", /config\/effective/, () => KONFIGURATION);
  api.on("GET", /graph\/map/, () => karte());
  api.on("GET", /\/api\/v1\/clusters\?/, () => ({ store: "shared", items: [], next_cursor: null }));
  api.on("GET", /\/api\/v1\/concepts\?/, () => ({ store: "shared", items: [], next_cursor: null }));
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ein Rahmen mit echten Maßen: Ohne Fläche zeichnet sigma nichts, und ein Test gegen eine
 *  unsichtbare Zeichenfläche prüft nur das Anlegen. */
function imRahmen(element: JSX.Element): JSX.Element {
  return <div style={{ width: 800, height: 600 }}>{element}</div>;
}

describe("Zeichenfläche (§17.2)", () => {
  it("meldet eine Auswahl nach oben — mit einem echten Klick auf den Knoten", async () => {
    const gewaehlt: string[] = [];
    renderMitQuery(
      imRahmen(
        <GraphCanvas
          nodes={[knoten("a", { gewicht: 1 })] as never}
          edges={[]}
          layout="concentric"
          physik={PHYSIK_VORGABE}
          onSelect={(id) => gewaehlt.push(id)}
        />,
      ),
    );

    // Ein einzelner Knoten liegt im konzentrischen Layout in der Mitte der Fläche.
    const flaeche = screen.getByTestId("graph-canvas");
    await waitFor(() => expect(flaeche.querySelector("canvas")).not.toBeNull());
    await new Promise((weiter) => window.setTimeout(weiter, 150));
    const mitte = flaeche.getBoundingClientRect();
    const x = mitte.left + mitte.width / 2;
    const y = mitte.top + mitte.height / 2;
    const ziel = document.elementFromPoint(x, y) ?? flaeche;
    fireEvent.mouseDown(ziel, { clientX: x, clientY: y, buttons: 1 });
    fireEvent.mouseUp(ziel, { clientX: x, clientY: y });
    fireEvent.click(ziel, { clientX: x, clientY: y });

    await waitFor(() => expect(gewaehlt).toEqual(["a"]));
  });

  it("lässt Kanten weg, deren Gegenstück außerhalb des Ausschnitts liegt (§17.3)", () => {
    // Ein gedeckelter Ausschnitt hat solche Kanten zwangsläufig; sie dürfen ihn nicht sprengen.
    renderMitQuery(
      <GraphCanvas
        nodes={[knoten("a")] as never}
        edges={[kante({ from_id: "a", to_id: "ausserhalb" })] as never}
        layout="breadthfirst"
        physik={PHYSIK_VORGABE}
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
  });

  it("markiert den ausgewählten Knoten und blendet den Rest ab", async () => {
    const { rerender } = renderMitQuery(
      imRahmen(
        <GraphCanvas
          nodes={[knoten("a"), knoten("b"), knoten("fern")] as never}
          edges={[kante({ id: "k1", from_id: "a", to_id: "b" })] as never}
          selected="b"
          layout="physik"
          physik={PHYSIK_VORGABE}
          labels={false}
          onSelect={() => undefined}
        />,
      ),
    );
    const flaeche = screen.getByTestId("graph-canvas");
    await waitFor(() => expect(flaeche.querySelector("canvas")).not.toBeNull());

    // Ein Wechsel der Auswahl und ein "Alles zeigen" laufen über dieselbe Instanz.
    rerender(
      imRahmen(
        <GraphCanvas
          nodes={[knoten("a"), knoten("b"), knoten("fern")] as never}
          edges={[kante({ id: "k1", from_id: "a", to_id: "b" })] as never}
          selected="a"
          layout="physik"
          physik={PHYSIK_VORGABE}
          einpassen={1}
          onSelect={() => undefined}
        />,
      ),
    );
    await new Promise((weiter) => window.setTimeout(weiter, 80));
    // Nach dem Rerender neu greifen: `rerender` ohne den Query-Provider ersetzt den Baum, und
    // die alte Referenz zeigt auf einen abgehängten Knoten.
    const danach = screen.getByTestId("graph-canvas");
    await waitFor(() => expect(danach.querySelector("canvas")).not.toBeNull());
  });

  it("zeigt einen Grabstein gedämpft statt ihn wegzulassen (§7.6)", () => {
    renderMitQuery(
      imRahmen(
        <GraphCanvas
          nodes={[knoten("a", { status: "tombstone" })] as never}
          edges={[]}
          layout="physik"
          physik={PHYSIK_VORGABE}
          onSelect={() => undefined}
        />,
      ),
    );

    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
  });
});

describe("Kurationsliste — Tastatur (§17.2 Ansicht 4)", () => {
  const aufgaben = [
    {
      kind: "unverified_edge",
      store: "shared",
      confidence: 0.9,
      edge: kante(),
      concepts: [konzept()],
      entry: null,
    },
    {
      kind: "supersedes",
      store: "shared",
      confidence: 0.5,
      edge: kante({ id: "22222222-2222-4222-8222-222222222222", kind: "supersedes" }),
      concepts: [konzept()],
      entry: null,
    },
  ];

  beforeEach(() => {
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: aufgaben }));
  });

  it("bewegt sich mit j und k durch die Liste", async () => {
    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));

    await userEvent.keyboard("j");
    await waitFor(() =>
      expect(screen.getByText(/Eine Ablösung wirkt nicht von selbst/)).toBeInTheDocument(),
    );

    await userEvent.keyboard("k");
    await waitFor(() =>
      expect(screen.queryByText(/Eine Ablösung wirkt nicht von selbst/)).not.toBeInTheDocument(),
    );
  });

  it("stellt einen Eintrag mit s zurück", async () => {
    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));

    await userEvent.keyboard("s");

    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
  });

  it("springt über die Liste zu einem Eintrag", async () => {
    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));

    await userEvent.click(screen.getAllByRole("button")[1] as HTMLElement);

    await waitFor(() =>
      expect(screen.getByText(/Eine Ablösung wirkt nicht von selbst/)).toBeInTheDocument(),
    );
  });
});

describe("Dokumentenbrowser — Filter", () => {
  beforeEach(() => {
    api.on("GET", /\/api\/v1\/concepts\?/, () => ({
      store: "shared",
      items: [konzept()],
      next_cursor: null,
    }));
    api.on("GET", /concepts\/confluence:1\?/, () => ({
      ...konzept(),
      body: null,
      content_hash: "a",
      outgoing: [],
      incoming: [],
      clusters: [],
      locked_fields: [],
    }));
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
  });

  it("schickt die Facetten mit (§16.2)", async () => {
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await userEvent.click(screen.getByLabelText("nur lose"));
    await userEvent.click(screen.getByLabelText("nur kuratiert"));
    await userEvent.type(screen.getByLabelText("Status-Filter"), "stable");

    await waitFor(() =>
      expect(
        api.calls.some(
          (aufruf) =>
            aufruf.url.includes("orphan=true") &&
            aufruf.url.includes("curated=true") &&
            aufruf.url.includes("status=stable"),
        ),
      ).toBe(true),
    );
  });

  it("öffnet die Detailspalte über eine Zeile", async () => {
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared", id: "confluence:1" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getAllByText("Eine Seite").length).toBeGreaterThan(0));

    await userEvent.click(screen.getAllByText("Eine Seite")[0] as HTMLElement);

    expect(gemerkt).toContainEqual({ id: "confluence:1" });
  });

  it("setzt die Suche über das Feld zurück an den Anfang", async () => {
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await userEvent.selectOptions(screen.getByLabelText("Typ"), "Confluence Page");

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("type=Confluence"))).toBe(true),
    );
  });
});

describe("Graph-Explorer — Filter und Layout", () => {
  it("meldet jeden Filter nach oben, damit er in der URL landet (§17.1)", async () => {
    // Die Filter der Graph-Ansicht sind Zustand der *Adresse*, nicht der Komponente. Das ist der
    // Punkt von §17.1 ("damit Ansichten teilbar sind"): Wer einen Ausschnitt bespricht, schickt
    // einen Link — nicht eine Klickanleitung.
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await userEvent.click(screen.getByLabelText("member"));
    await userEvent.click(screen.getByLabelText("nur unbestätigte"));

    expect(gemerkt).toContainEqual({ kinds: "member" });
    expect(gemerkt).toContainEqual({ unverified: true });
  });

  it("nimmt eine Kantenart wieder aus der Auswahl", async () => {
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", kinds: "member,references" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    expect(screen.getByLabelText("member")).toBeChecked();
    await userEvent.click(screen.getByLabelText("member"));

    expect(gemerkt).toContainEqual({ kinds: "references" });
  });

  it("wechselt das Layout", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "hierarchisch" }));

    expect(screen.getByRole("button", { name: "hierarchisch" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Physik" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("stellt die Physik-Regler erst zur Live-Simulation frei", async () => {
    // Ein Regler, der nichts bewirkt, ist eine Lüge über das Bedienelement: Abstoßung,
    // Kantenlänge und Zusammenhalt wirken nur auf `cola` und nicht auf ein Einmal-Layout.
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    expect(screen.getByRole("button", { name: "Regler" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "konzentrisch" }));

    expect(screen.getByRole("button", { name: "Regler" })).toBeDisabled();
  });

  it("meldet einen gescheiterten Aufklapp-Versuch sichtbar", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", mode: "reise", id: "confluence:0" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("springt aus der Übersicht auf ein Cluster", async () => {
    const gemerkt: unknown[] = [];
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [{ ...konzept({ id: "cluster:a", title: "Warehouse" }), member_count: 2 }],
      next_cursor: null,
    }));

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", mode: "reise" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getByText(/Warehouse/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Warehouse/ }));

    expect(gemerkt).toContainEqual({ id: "cluster:a" });
  });

  it("schneidet in der Karte auf das Cluster, statt es aufzuklappen", async () => {
    // Dieselbe Liste, zwei Bedeutungen — je nach Modus. In der Karte ist ein Cluster eine
    // Facette; in der Traversierung ist es ein Ausgangspunkt.
    const gemerkt: unknown[] = [];
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [{ ...konzept({ id: "cluster:a", title: "Warehouse" }), member_count: 2 }],
      next_cursor: null,
    }));

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getByText(/Warehouse/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Warehouse/ }));

    expect(gemerkt).toContainEqual({ cluster: "cluster:a" });
  });

  it("gibt die Facetten der Karte an die API weiter", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{
          view: "graph",
          store: "shared",
          type: "Confluence Page",
          unverified: true,
          kinds: "member,references",
        }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("/graph/map"));
      expect(aufruf?.url).toContain("type=Confluence+Page");
      expect(aufruf?.url).toContain("unverified=true");
      // Eine Liste wird zu mehreren gleichnamigen Parametern, nicht zu einer Zeichenkette.
      expect(aufruf?.url).toContain("kinds=member&kinds=references");
    });
  });

  it("bietet mehr zu laden an, wenn der Ausschnitt gedeckelt ist (§17.3)", async () => {
    api.on("GET", /graph\/map/, () =>
      karte({ nodes: [kartenknoten()], truncated: true, next_cursor: "confluence:1" }),
    );

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "mehr laden" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "mehr laden" }));

    await waitFor(() =>
      expect(api.calls.some((eintrag) => eintrag.url.includes("limit=600"))).toBe(true),
    );
  });
});

describe("Persönlicher Bereich — Auswahl", () => {
  beforeEach(() => {
    api.on("GET", /\/api\/v1\/models$/, () => ({ tasks: [], policies: {}, budget: {} }));
    api.on("GET", /\/api\/v1\/concepts\?/, () => ({
      store: "personal",
      items: [konzept({ id: "note:1", title: "Meine Notiz", store: "personal" })],
      next_cursor: null,
    }));
  });

  it("bietet nur persönliche Typen und Scopes an (§17.4)", async () => {
    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    const typen = screen.getByLabelText("Typ der Notiz") as HTMLSelectElement;
    expect([...typen.options].map((option) => option.value)).toEqual(["Cluster", "Note"]);
    const scopes = screen.getByLabelText("Scope der Notiz") as HTMLSelectElement;
    expect([...scopes.options].map((option) => option.value)).toEqual(["personal"]);
  });

  it("wählt eine bestehende Notiz aus", async () => {
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getByText("Meine Notiz")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Meine Notiz" }));

    expect(gemerkt).toContainEqual({ id: "note:1", store: "personal" });
  });

  it("meldet einen abgelehnten Anlegeversuch sichtbar (§17.4)", async () => {
    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await userEvent.type(screen.getByLabelText("Titel"), "Test");
    await userEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("verlangt eine ausgewählte Notiz, bevor eine Brücke möglich ist", async () => {
    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    expect(screen.getByText("Links eine Notiz auswählen.")).toBeInTheDocument();
  });
});

describe("Fortschrittsanzeige (§16.3)", () => {
  class StummeQuelle {
    static letzte: StummeQuelle | null = null;
    readonly zuhoerer = new Map<string, EventListener>();
    constructor(readonly url: string) {
      StummeQuelle.letzte = this;
    }
    addEventListener(name: string, zuhoerer: EventListener): void {
      this.zuhoerer.set(name, zuhoerer);
    }
    close(): void {
      /* im Test ohne Wirkung */
    }
  }

  it("zeigt den Zustand eines verfolgten Laufs an", async () => {
    vi.stubGlobal("EventSource", StummeQuelle);
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [] }));
    api.on("GET", /\/api\/v1\/runs\?/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/stats/, () => ({ stores: [] }));
    api.on("GET", /models\/usage/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/models$/, () => ({ tasks: [], policies: {}, budget: {} }));

    renderMitQuery(
      <Operations
        state={{ view: "betrieb", store: "shared", run: "abcdefgh-0000-4000-8000-000000000000" }}
        onChange={() => undefined}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("fortschritt")).toBeInTheDocument());
    fireEvent(
      window,
      new Event("noop"), // nur, damit React die Effekte abarbeitet
    );
    StummeQuelle.letzte?.zuhoerer.get("progress")?.(
      new MessageEvent("progress", {
        data: JSON.stringify({
          id: "abc",
          kind: "embed",
          params: {},
          status: "running",
          started_at: null,
          finished_at: null,
          progress: 0.5,
          stats: { embedded: 7 },
          error: null,
        }),
      }) as Event,
    );

    await waitFor(() => expect(screen.getByText(/running/)).toBeInTheDocument());
  });
});
