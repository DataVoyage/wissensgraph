/**
 * Die Hülle und die Regeln, die sie sichtbar machen muss (§17.1 bis §17.4).
 *
 * Der Schwerpunkt liegt bewusst nicht auf dem Aussehen, sondern auf drei Aussagen, die §17
 * ausdrücklich verlangt: Die Oberfläche enthält keine Fachlogik, ein gesperrtes Feld ist
 * *sichtbar* gesperrt, und eine Verschiebung zwischen Clustern besteht aus zwei Schritten.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ClusterWorkbench } from "./views/ClusterWorkbench";
import { ConceptPanel } from "./components/ConceptPanel";
import { CurationList } from "./views/CurationList";
import { FakeApi, KONFIGURATION, kante, konzept, renderMitQuery } from "./test-support";

let api: FakeApi;

beforeEach(() => {
  window.history.pushState({}, "", "/");
  window.sessionStorage.setItem("wg.token", "test");
  api = new FakeApi();
  api.on("GET", /config\/effective/, () => KONFIGURATION);
  api.on("GET", /\/api\/v1\/clusters(\?|$)/, () => ({
    store: "shared",
    items: [],
    next_cursor: null,
  }));
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Hülle", () => {
  it("zeigt erst eine Ansicht, wenn die Konfiguration da ist", async () => {
    renderMitQuery(<App />);

    expect(screen.getByRole("status")).toHaveTextContent("Konfiguration");
    await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());
  });

  it("bietet alle sechs Ansichten aus §17.2 an", async () => {
    renderMitQuery(<App />);

    await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());
    for (const label of ["Graph", "Dokumente", "Cluster", "Kuration", "Persönlich", "Betrieb"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("schreibt den Ansichtszustand in die URL, damit er teilbar ist", async () => {
    renderMitQuery(<App />);
    await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Dokumente" }));

    expect(window.location.search).toContain("view=browser");
  });

  it.each([
    ["Dokumente", "browser"],
    ["Cluster", "cluster"],
    ["Kuration", "kuration"],
    ["Persönlich", "persoenlich"],
    ["Betrieb", "betrieb"],
  ])("zeigt die Ansicht %s ohne Fehler", async (label, view) => {
    api.on("GET", /\/api\/v1\/concepts\?/, () => ({ store: "shared", items: [], next_cursor: null }));
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/models$/, () => ({ tasks: [], policies: {}, budget: {} }));
    api.on("GET", /models\/usage/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/runs\?/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [] }));
    api.on("GET", /\/api\/v1\/stats/, () => ({ stores: [] }));

    renderMitQuery(<App />);
    await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: label }));

    expect(window.location.search).toContain(`view=${view}`);
  });

  it("wechselt den Store und verwirft dabei die Auswahl", async () => {
    renderMitQuery(<App />);
    await waitFor(() => expect(screen.getByLabelText("Store")).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText("Store"), "personal");

    expect(window.location.search).toContain("store=personal");
    expect(window.location.search).not.toContain("id=");
  });

  it("nimmt die Store-Liste aus der Konfiguration und nicht aus dem Code", async () => {
    renderMitQuery(<App />);

    await waitFor(() => expect(screen.getByLabelText("Store")).toBeInTheDocument());
    const auswahl = screen.getByLabelText("Store") as HTMLSelectElement;
    expect([...auswahl.options].map((option) => option.value)).toEqual(["shared", "personal"]);
  });

  it("meldet einen Fehler beim Laden der Konfiguration und fragt nach dem Token", async () => {
    vi.unstubAllGlobals();
    const leer = new FakeApi();
    leer.install();
    renderMitQuery(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByLabelText("Bearer-Token")).toBeInTheDocument();
  });
});

describe("Gesperrte Felder (§17.3)", () => {
  it("zeigt eine Sperre an, statt sie nur durchzusetzen", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    const detail = {
      ...konzept({ source_name: "confluence-eng" }),
      body: "Text aus der Quelle",
      content_hash: "abc",
      outgoing: [],
      incoming: [],
      clusters: [],
      locked_fields: ["title", "description", "body", "resource"],
    };

    renderMitQuery(
      <ConceptPanel detail={detail as never} onOpen={() => undefined} />,
    );

    expect(screen.getByTestId("gesperrt-Beschreibung")).toBeInTheDocument();
    expect(screen.getByTestId("gesperrt-Fließtext")).toBeInTheDocument();
  });

  it("lässt Status auch an einem gespiegelten Konzept zu (§17.4)", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    const detail = {
      ...konzept({ source_name: "confluence-eng" }),
      body: null,
      content_hash: "abc",
      outgoing: [],
      incoming: [],
      clusters: [],
      locked_fields: ["title", "description", "body", "resource"],
    };

    renderMitQuery(<ConceptPanel detail={detail as never} onOpen={() => undefined} />);

    expect(screen.getByLabelText("Status")).toBeEnabled();
  });

  it("markiert eine unbestätigte Modellkante und bietet beide Aktionen an", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    const detail = {
      ...konzept(),
      body: null,
      content_hash: "abc",
      outgoing: [kante()],
      incoming: [],
      clusters: [],
      locked_fields: [],
    };

    renderMitQuery(<ConceptPanel detail={detail as never} onOpen={() => undefined} />);

    expect(screen.getByText("unbestätigt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "✓" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "✕" })).toBeInTheDocument();
  });
});

describe("Kurationsliste (§17.2 Ansicht 4)", () => {
  const aufgabe = {
    kind: "unverified_edge",
    store: "shared",
    confidence: 0.8,
    edge: kante(),
    concepts: [konzept(), konzept({ id: "confluence:2", title: "Zweite Seite" })],
    entry: null,
  };

  it("zeigt beide Konzepte und die Begründung des Modells", async () => {
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [aufgabe] }));

    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);

    await waitFor(() => expect(screen.getByText("Eine Seite")).toBeInTheDocument());
    expect(screen.getByText("Zweite Seite")).toBeInTheDocument();
    expect(screen.getByText(/dieselbe Ladestrecke/)).toBeInTheDocument();
  });

  it("bestätigt über die Tastatur", async () => {
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [aufgabe] }));
    api.on("POST", /edges\/.*\/verify/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);
    await waitFor(() => expect(screen.getByText("Eine Seite")).toBeInTheDocument());
    await userEvent.keyboard("{Enter}");

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("/verify"))).toBe(true),
    );
  });

  it("verwirft über die Tastatur — und das ist etwas anderes als Löschen", async () => {
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [aufgabe] }));
    api.on("POST", /edges\/.*\/reject/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);
    await waitFor(() => expect(screen.getByText("Eine Seite")).toBeInTheDocument());
    await userEvent.keyboard("x");

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("/reject"))).toBe(true),
    );
    expect(api.calls.some((aufruf) => aufruf.method === "DELETE")).toBe(false);
  });

  it("meldet eine leere Warteschlange als Zustand, nicht als Fehler", async () => {
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [] }));

    renderMitQuery(<CurationList state={{ view: "kuration", store: "shared" }} />);

    await waitFor(() => expect(screen.getByText(/Nichts offen/)).toBeInTheDocument());
  });
});

describe("Cluster-Arbeitsplatz (§17.2 Ansicht 3)", () => {
  const cluster = {
    ...konzept({ id: "cluster:a", title: "Warehouse", type: "Cluster" }),
    member_count: 1,
    centroid_age_seconds: 60,
  };
  const detail = {
    ...konzept({ id: "cluster:a", title: "Warehouse", type: "Cluster" }),
    members: [konzept()],
    related: [],
    centroid_age_seconds: 60,
    manual_title: false,
  };

  beforeEach(() => {
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [cluster, { ...cluster, id: "cluster:b", title: "Incident" }],
      next_cursor: null,
    }));
    api.on("GET", /clusters\/cluster:a\?/, () => detail);
  });

  it("verschieben ist Entfernen **und** Hinzufügen — zwei Aufrufe (§13.4)", async () => {
    api.on("DELETE", /clusters\/cluster:a\/members\//, () => ({ entry: {} }));
    api.on("POST", /clusters\/cluster:b\/members/, () => ({ items: [] }));

    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mitglied-confluence:1")).toBeInTheDocument());

    // Das Drag-and-Drop von jsdom trägt nur die Nutzlast; die Wirkung prüft der Ablauf danach.
    const daten = new Map<string, string>();
    const transfer = {
      setData: (typ: string, wert: string) => daten.set(typ, wert),
      getData: (typ: string) => daten.get(typ) ?? "",
    };
    const quelle = screen.getByTestId("mitglied-confluence:1");
    const ziel = screen.getByTestId("cluster-cluster:b");
    quelle.dispatchEvent(
      Object.assign(new Event("dragstart", { bubbles: true }), { dataTransfer: transfer }),
    );
    ziel.dispatchEvent(
      Object.assign(new Event("drop", { bubbles: true }), { dataTransfer: transfer }),
    );

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.method === "DELETE")).toBe(true),
    );
    await waitFor(() =>
      expect(
        api.calls.some(
          (aufruf) => aufruf.method === "POST" && aufruf.url.includes("cluster:b/members"),
        ),
      ).toBe(true),
    );
  });

  it("zeigt an, wenn ein Titel von Hand gesetzt wurde", async () => {
    api.on("GET", /clusters\/cluster:a\?/, () => ({ ...detail, manual_title: true }));

    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/keine automatische Neubetitelung/)).toBeInTheDocument(),
    );
  });

  it("bietet das Ausgliedern erst mit Auswahl und Titel an", async () => {
    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mitglied-confluence:1")).toBeInTheDocument());

    const knopf = screen.getByRole("button", { name: /ausgliedern/i });
    expect(knopf).toBeDisabled();

    await userEvent.click(screen.getByLabelText("Auswahl confluence:1"));
    await userEvent.type(screen.getByLabelText("Neuer Titel"), "Neu");

    expect(knopf).toBeEnabled();
  });
});
