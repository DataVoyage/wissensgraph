/**
 * Die übrigen vier Ansichten und der Fortschrittsstrom (§17.2, §16.3).
 *
 * Geprüft wird jeweils die Aussage, die §17 an dieser Ansicht festmacht — nicht ihr Aufbau:
 * dass der persönliche Bereich als solcher gekennzeichnet ist, dass eine Brücke im persönlichen
 * Store entsteht, dass ein Lauf den Bildschirm nicht blockiert und dass der Modus einer Suche
 * sichtbar wird.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { subscribeToRun } from "./api/events";
import { DocumentBrowser } from "./views/DocumentBrowser";
import { GraphExplorer } from "./views/GraphExplorer";
import { Diagnose } from "./views/Diagnose";
import { RunsView } from "./views/RunsView";
import { Sources } from "./views/Sources";
import { PersonalArea } from "./views/PersonalArea";
import { FakeApi, KONFIGURATION, karte, konzept, renderMitQuery, werkbankProps } from "./test-support";

let api: FakeApi;

const LEERE_SEITE = { store: "shared", items: [], next_cursor: null };

beforeEach(() => {
  window.history.pushState({}, "", "/");
  api = new FakeApi();
  api.on("GET", /config\/effective/, () => KONFIGURATION);
  api.on("GET", /graph\/map/, () => karte());
  api.on("GET", /\/api\/v1\/clusters\?/, () => LEERE_SEITE);
  api.on("GET", /\/api\/v1\/concepts\?/, () => LEERE_SEITE);
  api.on("GET", /\/api\/v1\/models$/, () => ({ tasks: [], policies: {}, budget: {} }));
  api.on("GET", /models\/usage/, () => ({ store: "shared", items: [] }));
  api.on("GET", /\/api\/v1\/runs\?/, () => ({ store: "shared", items: [] }));
  api.on("GET", /\/api\/v1\/sources/, () => ({ items: [] }));
  api.on("GET", /\/api\/v1\/stats/, () => ({ stores: [] }));
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Dokumentenbrowser (§17.2 Ansicht 2)", () => {
  it("bietet nur Typen an, die es in diesem Store gibt (§7.2)", async () => {
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    const auswahl = screen.getByLabelText("Typ") as HTMLSelectElement;
    expect([...auswahl.options].map((option) => option.value)).toEqual(["", "Cluster", "Note"]);
  });

  it("blättert cursor-basiert weiter", async () => {
    api.on("GET", /\/api\/v1\/concepts\?/, () => ({
      store: "shared",
      items: [konzept()],
      next_cursor: "confluence:1",
    }));

    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getByText("Eine Seite")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Weiter" }));

    await waitFor(() =>
      expect(
        api.calls.some((aufruf) => aufruf.url.includes("cursor=confluence%3A1")),
      ).toBe(true),
    );
  });

  it("meldet einen leeren Treffer als Zustand", async () => {
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() => expect(screen.getByText("Kein Treffer.")).toBeInTheDocument());
  });

  it("gibt einen Filterwechsel nach oben, damit er in der URL landet", async () => {
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <DocumentBrowser
        state={{ view: "browser", store: "shared" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await userEvent.selectOptions(screen.getByLabelText("Scope"), "engineering");

    expect(gemerkt).toContainEqual({ scope: "engineering" });
  });
});

describe("Graph-Explorer (§17.2 Ansicht 1)", () => {
  it("startet mit der Kernspace-Übersicht und nicht mit einem Suchfeld allein", async () => {
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [{ ...konzept({ id: "cluster:a", title: "Warehouse" }), member_count: 4 }],
      next_cursor: null,
    }));

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() => expect(screen.getByText(/Warehouse/)).toBeInTheDocument());
    expect(screen.getByText("Kernspace-Übersicht")).toBeInTheDocument();
  });

  it("nennt den Modus einer Suche (§12.4)", async () => {
    api.on("POST", /graph\/search/, () => ({
      store: "shared",
      query: "Warehouse",
      mode: "hybrid",
      hits: [],
    }));

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", mode: "reise" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await userEvent.type(screen.getByLabelText("Suchbegriff"), "Warehouse");
    await userEvent.click(screen.getByRole("button", { name: "Los" }));

    await waitFor(() => expect(screen.getByText("hybrid")).toBeInTheDocument());
  });

  it("klappt einen Knoten aus der URL sofort auf", async () => {
    api.on("GET", /graph\/neighbors/, () => ({
      start: [["shared", "confluence:1"]],
      nodes: [
        {
          id: "confluence:1",
          store: "shared",
          scope: "engineering",
          type: "Confluence Page",
          title: "Eine Seite",
          status: "stable",
          hop: 0,
          score: 1,
          density: 0,
        },
      ],
      edges: [],
      hops: 1,
      truncated: false,
      queries: 1,
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

    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared", mode: "reise", id: "confluence:1" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() => expect(screen.getByTestId("graph-canvas")).toBeInTheDocument());
  });

  it("bietet die Kantenarten aus der Konfiguration als Filter an (§17.1)", async () => {
    renderMitQuery(
      <GraphExplorer
        state={{ view: "graph", store: "shared" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    expect(screen.getByLabelText("member")).toBeInTheDocument();
    expect(screen.getByLabelText("depends_on")).toBeInTheDocument();
  });
});

describe("Persönlicher Bereich (§17.2 Ansicht 5)", () => {
  it("kennzeichnet den Bereich und sagt, ob Embeddings lokal möglich sind (§11.5)", async () => {
    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/keine Embeddings/)).toBeInTheDocument(),
    );
    expect(screen.getByText("Persönlicher Bereich")).toBeInTheDocument();
  });

  it("meldet einen lokalen Anbieter, wenn es einen gibt", async () => {
    api.on("GET", /\/api\/v1\/models$/, () => ({
      tasks: [
        {
          task: "embedding",
          provider: "ollama",
          model: "nomic",
          model_key: "ollama:nomic",
          local: true,
          dim: 512,
          temperature: null,
          configured: true,
          fallbacks: [],
          generated_by: "ollama:nomic/embedding@v1",
        },
      ],
      policies: {},
      budget: {},
    }));

    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/lokalen Anbieter/)).toBeInTheDocument(),
    );
  });

  it("legt eine Notiz an und übernimmt sie in den Zustand", async () => {
    const gemerkt: unknown[] = [];
    api.on("POST", /\/api\/v1\/concepts$/, () => ({
      entry: { id: 1 },
      concept: konzept({ id: "note:neu", store: "personal" }),
      edge: null,
      detail: null,
    }));

    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await userEvent.type(screen.getByLabelText("Titel"), "Meine Notiz");
    await userEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() =>
      expect(gemerkt).toContainEqual({ id: "note:neu", store: "personal" }),
    );
  });

  it("setzt eine Brücke im persönlichen Store (§12.1)", async () => {
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [{ ...konzept({ id: "cluster:a", title: "Warehouse" }), member_count: 3 }],
      next_cursor: null,
    }));
    api.on("POST", /\/api\/v1\/edges$/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(
      <PersonalArea
        state={{ view: "persoenlich", store: "personal", id: "note:1" }}
        onChange={() => undefined}
        config={KONFIGURATION as never}
      {...werkbankProps()} />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "verlinken" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "verlinken" }));

    await waitFor(() => {
      const aufruf = api.calls.find(
        (eintrag) => eintrag.method === "POST" && eintrag.url.endsWith("/api/v1/edges"),
      );
      expect(aufruf?.body).toMatchObject({
        store: "personal",
        from_id: "note:1",
        to_id: "cluster:a",
        to_store: "shared",
      });
    });
  });
});

describe("Verwalten (§17.2 Ansicht 6, U5)", () => {
  const quelle = {
    name: "confluence-eng",
    adapter: "confluence",
    enabled: true,
    id_prefix: "confluence",
    scope: "engineering",
    usable: true,
    health: { state: "healthy", detail: "" },
    capabilities: {},
    last_run: null,
  };

  it("zeigt Quellen mit Zustand und dem letzten Lauf", async () => {
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [quelle] }));

    renderMitQuery(
      <Sources state={{ view: "quellen", store: "shared" }} onChange={() => undefined} />,
    );

    await waitFor(() => expect(screen.getByText("confluence-eng")).toBeInTheDocument());
    expect(screen.getByText("noch kein Lauf")).toBeInTheDocument();
  });

  it("startet einen Sync mit den Optionen aus §19 und merkt sich die Lauf-ID", async () => {
    const gemerkt: unknown[] = [];
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [quelle] }));
    api.on("POST", /runs\/sync/, () => ({
      id: "22222222-2222-4222-8222-222222222222",
      kind: "sync",
      params: {},
      status: "queued",
      started_at: null,
      finished_at: null,
      progress: 0,
      stats: {},
      error: null,
    }));

    renderMitQuery(
      <Sources
        state={{ view: "quellen", store: "shared" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Sync" })).toBeEnabled());
    await userEvent.click(screen.getByLabelText(/Vollabgleich/));
    await userEvent.click(screen.getByLabelText(/Trockenlauf/));
    await userEvent.click(screen.getByRole("button", { name: "Sync" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("/runs/sync"));
      expect(aufruf?.body).toEqual({ source: "confluence-eng", full: true, dry_run: true });
    });
    expect(gemerkt).toContainEqual({ run: "22222222-2222-4222-8222-222222222222" });
  });

  it("zeigt die aufgelöste Konfiguration mit maskierten Secrets (§20.2)", async () => {
    renderMitQuery(
      <Diagnose state={{ view: "diagnose", store: "shared" }} onChange={() => undefined} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/Secrets sind maskiert/)).toBeInTheDocument(),
    );
  });

  it("führt die Diagnose nur auf Anstoß aus und zeigt die Ampel", async () => {
    api.on("GET", /\/api\/v1\/doctor/, () => ({
      healthy: false,
      checks: [
        { name: "stores", status: "ok", detail: "beide erreichbar", context: {} },
        { name: "provider", status: "fail", detail: "kein Schlüssel", context: {} },
      ],
    }));

    renderMitQuery(
      <Diagnose state={{ view: "diagnose", store: "shared" }} onChange={() => undefined} />,
    );
    // Kein Aufruf beim Rendern: Die Prüfungen verbinden sich wirklich mit den Stores.
    expect(api.calls.some((aufruf) => aufruf.url.includes("/doctor"))).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Diagnose ausführen" }));

    await waitFor(() =>
      expect(screen.getByText(/Mindestens eine Prüfung ist fehlgeschlagen/)).toBeInTheDocument(),
    );
    expect(screen.getByText("kein Schlüssel")).toBeInTheDocument();
    expect(screen.getByLabelText("fail")).toBeInTheDocument();
  });

  it("bricht einen wartenden Lauf ab und weist Probeläufe aus", async () => {
    api.on("GET", /\/api\/v1\/runs\?/, () => ({
      store: "shared",
      items: [
        {
          id: "33333333-3333-4333-8333-333333333333",
          kind: "embed",
          params: { dry_run: true },
          status: "queued",
          started_at: null,
          finished_at: null,
          progress: 0,
          stats: {},
          error: null,
        },
      ],
    }));
    api.on("POST", /runs\/.*\/cancel/, () => ({ id: "x", status: "cancelled" }));

    renderMitQuery(
      <RunsView state={{ view: "laeufe", store: "shared" }} onChange={() => undefined} />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "abbrechen" })).toBeInTheDocument());
    expect(screen.getByText("Probelauf")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "abbrechen" }));

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("/cancel"))).toBe(true),
    );
  });
});

describe("Fortschrittsstrom (§16.3)", () => {
  class FakeEventSource {
    static letzte: FakeEventSource | null = null;
    readonly zuhoerer = new Map<string, EventListener>();
    geschlossen = false;

    constructor(readonly url: string) {
      FakeEventSource.letzte = this;
    }

    addEventListener(name: string, zuhoerer: EventListener): void {
      this.zuhoerer.set(name, zuhoerer);
    }

    close(): void {
      this.geschlossen = true;
    }

    sende(name: string, daten: unknown): void {
      this.zuhoerer.get(name)?.(
        new MessageEvent(name, { data: JSON.stringify(daten) }) as Event,
      );
    }
  }

  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("liefert Fortschritt und schließt sich beim Ende von selbst", () => {
    const gesehen: string[] = [];
    subscribeToRun("abc", (ereignis) => gesehen.push(ereignis.kind));

    FakeEventSource.letzte?.sende("progress", { status: "running" });
    FakeEventSource.letzte?.sende("done", { status: "succeeded" });

    expect(gesehen).toEqual(["progress", "done"]);
    expect(FakeEventSource.letzte?.geschlossen).toBe(true);
  });

  it("unterscheidet einen Serverfehler von einem Verbindungsabbruch", () => {
    const gesehen: string[] = [];
    subscribeToRun("abc", (ereignis) => {
      if (ereignis.kind === "error") {
        gesehen.push(ereignis.detail);
      }
    });

    FakeEventSource.letzte?.sende("error", { detail: "Kein Lauf mit der ID abc." });

    expect(gesehen).toEqual(["Kein Lauf mit der ID abc."]);
  });

  it("lässt sich von außen beenden", () => {
    const beenden = subscribeToRun("abc", () => undefined);

    beenden();

    expect(FakeEventSource.letzte?.geschlossen).toBe(true);
  });
});
