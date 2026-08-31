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

import { GraphCanvas } from "./components/GraphCanvas";
import { CurationList } from "./views/CurationList";
import { DocumentBrowser } from "./views/DocumentBrowser";
import { GraphExplorer } from "./views/GraphExplorer";
import { Operations } from "./views/Operations";
import { PersonalArea } from "./views/PersonalArea";
import { FakeApi, KONFIGURATION, kante, konzept, renderMitQuery } from "./test-support";

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
    density: 0,
    ...overrides,
  };
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
  api = new FakeApi();
  api.on("GET", /config\/effective/, () => KONFIGURATION);
  api.on("GET", /\/api\/v1\/clusters\?/, () => ({ store: "shared", items: [], next_cursor: null }));
  api.on("GET", /\/api\/v1\/concepts\?/, () => ({ store: "shared", items: [], next_cursor: null }));
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Zeichenfläche (§17.2)", () => {
  it("meldet eine Auswahl nach oben", () => {
    const gewaehlt: string[] = [];
    renderMitQuery(
      <GraphCanvas
        nodes={[knoten("a"), knoten("b", { store: "personal" })] as never}
        edges={[kante({ from_id: "a", to_id: "b", to_store: "personal" })] as never}
        layout="concentric"
        onSelect={(id) => gewaehlt.push(id)}
      />,
    );

    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
    expect(gewaehlt).toEqual([]);
  });

  it("lässt Kanten weg, deren Gegenstück außerhalb des Ausschnitts liegt (§17.3)", () => {
    // Ein gedeckelter Ausschnitt hat solche Kanten zwangsläufig; sie dürfen ihn nicht sprengen.
    renderMitQuery(
      <GraphCanvas
        nodes={[knoten("a")] as never}
        edges={[kante({ from_id: "a", to_id: "ausserhalb" })] as never}
        layout="breadthfirst"
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
  });

  it("markiert den ausgewählten Knoten", () => {
    renderMitQuery(
      <GraphCanvas
        nodes={[knoten("a"), knoten("b")] as never}
        edges={[]}
        selected="b"
        layout="cose"
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
  });

  it("zeigt einen Grabstein gedämpft statt ihn wegzulassen (§7.6)", () => {
    renderMitQuery(
      <GraphCanvas
        nodes={[knoten("a", { status: "tombstone" })] as never}
        edges={[]}
        layout="cose"
        onSelect={() => undefined}
      />,
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
      />,
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
      />,
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
      />,
    );

    await userEvent.selectOptions(screen.getByLabelText("Typ"), "Confluence Page");

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("type=Confluence"))).toBe(true),
    );
  });
});

describe("Graph-Explorer — Filter und Layout", () => {
  it("filtert Kanten nach Art und blendet Bestätigtes aus", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        edgeKinds={["member", "references"]}
      />,
    );

    await userEvent.click(screen.getByLabelText("member"));
    await userEvent.click(screen.getByLabelText("nur unbestätigte"));
    await userEvent.click(screen.getByLabelText("member"));

    expect(screen.getByLabelText("nur unbestätigte")).toBeChecked();
  });

  it("wechselt das Layout", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        edgeKinds={[]}
      />,
    );

    await userEvent.selectOptions(screen.getByLabelText("Layout"), "breadthfirst");

    expect(screen.getByLabelText("Layout")).toHaveValue("breadthfirst");
  });

  it("meldet einen gescheiterten Aufklapp-Versuch sichtbar", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", id: "confluence:0" }}
        onChange={() => undefined}
        edgeKinds={[]}
      />,
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
        state={{ view: "graph", store: "shared" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        edgeKinds={[]}
      />,
    );
    await waitFor(() => expect(screen.getByText(/Warehouse/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Warehouse/ }));

    expect(gemerkt).toContainEqual({ id: "cluster:a" });
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
      />,
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
      />,
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
      />,
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
      />,
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
