/**
 * Der Erkunden-Bereich (U3): globale Suche, gerendertes Lesen, Bearbeiten eigener Notizen.
 *
 * Die Anwender-Sitzung "Was haben wir zu X?" läuft über die Kopfzeile: `/` fokussiert, die
 * Treffer öffnen wahlweise das Dokument oder die Traversierung — ohne Reiterwissen (§17.5).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobaleSuche } from "./components/GlobaleSuche";
import { Markdown } from "./components/Markdown";
import { ConceptPanel } from "./components/ConceptPanel";
import { FakeApi, konzept, renderMitQuery } from "./test-support";

let api: FakeApi;

beforeEach(() => {
  window.history.pushState({}, "", "/");
  api = new FakeApi();
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Markdown-Renderer", () => {
  it("rendert Überschriften, Listen, Code und Betonung", () => {
    render(
      <Markdown
        text={
          "# Titel\n\nEin **fetter** Satz mit `code`.\n\n- eins\n- zwei\n\n1. erstens\n\n```\nrohtext\n```"
        }
      />,
    );
    expect(screen.getByRole("heading", { name: "Titel" })).toBeInTheDocument();
    expect(screen.getByText("fetter")).toBeInTheDocument();
    expect(screen.getByText("code")).toBeInTheDocument();
    expect(screen.getByText("eins")).toBeInTheDocument();
    expect(screen.getByText("erstens")).toBeInTheDocument();
    expect(screen.getByText("rohtext")).toBeInTheDocument();
  });

  it("macht http-Links anklickbar und neutralisiert alles andere", () => {
    render(
      <Markdown text={"[gut](https://example.org/seite) und [böse](javascript:alert(1))"} />,
    );
    const link = screen.getByRole("link", { name: "gut" });
    expect(link).toHaveAttribute("href", "https://example.org/seite");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    // Der javascript:-Link ist kein Link geworden — sein Wortlaut steht als Text da.
    expect(screen.queryByRole("link", { name: "böse" })).not.toBeInTheDocument();
  });

  it("lässt ein script-Tag Text sein — es gibt kein innerHTML", () => {
    const { container } = render(<Markdown text={'<script>alert("x")</script>'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>/)).toBeInTheDocument();
  });

  it("rendert ein Zitat als Zitat", () => {
    const { container } = render(<Markdown text={"> so war das"} />);
    expect(container.querySelector("blockquote")).toHaveTextContent("so war das");
  });
});

describe("Globale Suche (§17.5)", () => {
  const treffer = [
    {
      id: "cluster:a",
      store: "shared",
      scope: "engineering",
      type: "Cluster",
      title: "Warehouse",
      status: "stable",
      hop: 0,
      score: 0.9,
      density: 0,
    },
    {
      id: "confluence:1",
      store: "shared",
      scope: "engineering",
      type: "Confluence Page",
      title: "Faktentabellen laden",
      status: "stable",
      hop: 0,
      score: 0.7,
      density: 0,
    },
  ];

  function aufbauen() {
    api.on("POST", /graph\/search/, () => ({
      store: "shared",
      query: "warehouse",
      mode: "two_stage",
      hits: treffer,
    }));
    const geoeffnet: Array<{ id: string; store: string; wohin: string }> = [];
    renderMitQuery(
      <GlobaleSuche store="shared" typen={["Cluster"]} onOeffnen={(ziel) => geoeffnet.push(ziel)} />,
    );
    return geoeffnet;
  }

  it("sucht auf Enter und öffnet den Treffer zum Lesen", async () => {
    const geoeffnet = aufbauen();
    await userEvent.type(screen.getByRole("searchbox"), "warehouse{Enter}");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /Warehouse/ }));
    expect(geoeffnet).toEqual([{ id: "cluster:a", store: "shared", wohin: "lesen" }]);
  });

  it("öffnet über den Graph-Knopf die Traversierung", async () => {
    const geoeffnet = aufbauen();
    await userEvent.type(screen.getByRole("searchbox"), "warehouse{Enter}");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    const zeile = screen.getByRole("button", { name: /Faktentabellen/ }).closest("div");
    await userEvent.click(zeile?.querySelector('[title^="Im Graphen"]') as Element);
    expect(geoeffnet).toEqual([{ id: "confluence:1", store: "shared", wohin: "graph" }]);
  });

  it("wählt mit Pfeilen und öffnet mit Enter", async () => {
    const geoeffnet = aufbauen();
    const feld = screen.getByRole("searchbox");
    await userEvent.type(feld, "warehouse{Enter}");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await userEvent.type(feld, "{ArrowDown}{Enter}");
    expect(geoeffnet).toEqual([{ id: "confluence:1", store: "shared", wohin: "lesen" }]);
  });

  it("fokussiert das Feld über die Taste /", async () => {
    aufbauen();
    await userEvent.keyboard("/");
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });

  it("schließt die Liste mit Escape", async () => {
    aufbauen();
    await userEvent.type(screen.getByRole("searchbox"), "warehouse{Enter}");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});

describe("Lesen und Bearbeiten im Inspektor", () => {
  function detail(overrides: Record<string, unknown> = {}) {
    return {
      ...konzept(),
      body: "# Abschnitt\n\nInhalt mit **Gewicht**.",
      content_hash: "a",
      outgoing: [],
      incoming: [],
      clusters: [],
      locked_fields: [],
      ...overrides,
    };
  }

  beforeEach(() => {
    api.on("GET", /history/, () => ({ items: [] }));
  });

  it("zeigt den Fließtext gerendert, nicht roh", () => {
    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);
    expect(screen.getByRole("heading", { name: "Abschnitt" })).toBeInTheDocument();
    expect(screen.getByText("Gewicht")).toBeInTheDocument();
    expect(screen.queryByText(/# Abschnitt/)).not.toBeInTheDocument();
  });

  it("verlinkt die Quelle, wenn es eine gibt", () => {
    renderMitQuery(
      <ConceptPanel
        detail={detail({ resource: "https://confluence.example/x" }) as never}
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByRole("link", { name: /zur Quelle/ })).toHaveAttribute(
      "href",
      "https://confluence.example/x",
    );
  });

  it("bietet Bearbeiten nur im persönlichen Store an und schickt ein PATCH", async () => {
    api.on("PATCH", /concepts\//, () => ({ entry: {}, concept: null, edge: null }));
    renderMitQuery(
      <ConceptPanel
        detail={detail({ store: "personal", id: "note:1" }) as never}
        onOpen={() => undefined}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Fließtext bearbeiten" }));
    const entwurf = screen.getByLabelText("Fließtext (Markdown)");
    await userEvent.clear(entwurf);
    await userEvent.type(entwurf, "Neuer Text");
    await userEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.method === "PATCH");
      expect(aufruf?.body).toEqual({ body: "Neuer Text" });
    });
  });

  it("bietet an gespiegelten Inhalten kein Bearbeiten an", () => {
    renderMitQuery(
      <ConceptPanel
        detail={detail({ locked_fields: ["description", "body"] }) as never}
        onOpen={() => undefined}
      />,
    );
    expect(screen.queryByRole("button", { name: /bearbeiten/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("gesperrt-Fließtext")).toBeInTheDocument();
  });
});
