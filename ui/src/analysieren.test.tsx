/**
 * Der Analysieren-Bereich (U4): Automatisierung mit Probelauf zuerst, Qualität als Verdichtung.
 *
 * Die eine Regel, an der hier alles hängt (§19, ins UI übertragen): Kein schreibender Lauf ohne
 * vorherige Vorschau — der scharfe Knopf existiert erst, wenn ein Probelauf durch ist, und er
 * schickt exakt dieselben Parameter.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Automation } from "./views/Automation";
import { Quality } from "./views/Quality";
import { FakeApi, KONFIGURATION, konzept, renderMitQuery } from "./test-support";

let api: FakeApi;

/** Der Fortschrittsstrom, von Hand gespeist — echte SSE braucht einen echten Server. */
class FakeEventSource {
  static letzte: FakeEventSource | null = null;
  readonly zuhoerer = new Map<string, EventListener>();

  constructor(readonly url: string) {
    FakeEventSource.letzte = this;
  }

  addEventListener(name: string, zuhoerer: EventListener): void {
    this.zuhoerer.set(name, zuhoerer);
  }

  close(): void {
    // nichts zu tun
  }

  sende(name: string, daten: unknown): void {
    this.zuhoerer.get(name)?.(new MessageEvent(name, { data: JSON.stringify(daten) }) as Event);
  }
}

function lauf(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    kind: "cluster",
    status: "queued",
    progress: 0,
    stats: {},
    error: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource);
  FakeEventSource.letzte = null;
  api = new FakeApi();
  api.on("GET", /config\/effective/, () => KONFIGURATION);
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Automatisierung (§17.2 Ansicht 7)", () => {
  it("startet zuerst den Probelauf — der scharfe Knopf existiert noch gar nicht", async () => {
    api.on("POST", /runs\/cluster/, () => lauf());
    renderMitQuery(<Automation state={{ view: "automatisierung", store: "shared" }} onChange={() => undefined} />);
    const karte = await screen.findByRole("region", { name: "Clustering" });

    expect(
      screen.queryByRole("button", { name: /scharf ausführen/ }),
    ).not.toBeInTheDocument();

    await userEvent.click(within(karte).getByRole("button", { name: "Probelauf" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("/runs/cluster"));
      expect(aufruf?.body).toEqual({ scope: "engineering", dry_run: true });
    });
  });

  it("bietet nach erfolgreicher Vorschau den scharfen Lauf mit denselben Parametern an", async () => {
    api.on("POST", /runs\/cluster/, () => lauf());
    renderMitQuery(<Automation state={{ view: "automatisierung", store: "shared" }} onChange={() => undefined} />);
    const karte = await screen.findByRole("region", { name: "Clustering" });
    await userEvent.click(within(karte).getByRole("button", { name: "Probelauf" }));
    await waitFor(() => expect(screen.getByTestId("fortschritt")).toBeInTheDocument());

    FakeEventSource.letzte?.sende("progress", lauf({ status: "running", progress: 0.5 }));
    FakeEventSource.letzte?.sende(
      "done",
      lauf({ status: "succeeded", progress: 1, stats: { clusters_created: 3, clusters_matched: 1, members_added: 13 } }),
    );

    const scharf = await screen.findByRole("button", { name: /scharf ausführen/ });
    expect(screen.getByText(/3 Cluster entstünden neu/)).toBeInTheDocument();
    expect(screen.getByText(/Geschrieben wurde nichts/)).toBeInTheDocument();

    await userEvent.click(scharf);
    await waitFor(() => {
      const aufrufe = api.calls.filter((eintrag) => eintrag.url.includes("/runs/cluster"));
      expect(aufrufe.at(-1)?.body).toEqual({ scope: "engineering", dry_run: false });
    });
  });

  it("Embeddings sind die Ausnahme: kein Probelauf, kein dry_run im Körper", async () => {
    api.on("POST", /runs\/embed/, () => lauf({ kind: "embed" }));
    renderMitQuery(<Automation state={{ view: "automatisierung", store: "shared" }} onChange={() => undefined} />);
    const karte = await screen.findByRole("region", { name: "Embeddings" });

    expect(within(karte).queryByRole("button", { name: "Probelauf" })).not.toBeInTheDocument();
    await userEvent.click(within(karte).getByRole("button", { name: "Starten" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("/runs/embed"));
      expect(aufruf?.body).toEqual({ scope: "engineering" });
    });
  });

  it("belegt die Waisen-Parameter aus der Konfiguration vor und schreibt Abweichungen an", async () => {
    api.on("POST", /runs\/link-orphans/, () => lauf({ kind: "link_orphans" }));
    renderMitQuery(<Automation state={{ view: "automatisierung", store: "shared" }} onChange={() => undefined} />);
    const karte = await screen.findByRole("region", { name: "Waisen-Anbindung" });

    const feld = within(karte).getByLabelText("Auto-Commit ab");
    // Die Vorbelegung kommt aus der (asynchron geladenen) Konfiguration.
    await waitFor(() => expect(feld).toHaveValue(0.85));
    expect(screen.queryByText(/abweichend/)).not.toBeInTheDocument();

    await userEvent.clear(feld);
    await userEvent.type(feld, "0.95");
    expect(screen.getByText(/abweichend — Konfiguration: 0.85/)).toBeInTheDocument();

    await userEvent.click(within(karte).getByRole("button", { name: "Probelauf" }));
    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("/runs/link-orphans"));
      expect(aufruf?.body).toEqual({
        scope: "engineering",
        proximity_auto_commit: 0.95,
        dry_run: true,
      });
    });
  });
});

describe("Kuration und Kopfzeilen-Suche vertragen sich", () => {
  it("ignoriert Kurationstasten, während in einem Feld getippt wird", async () => {
    api.on("GET", /curation\/queue/, () => ({
      store: "shared",
      items: [
        {
          kind: "unverified_edge",
          store: "shared",
          confidence: 0.9,
          edge: {
            id: "11111111-1111-4111-8111-111111111111",
            from_store: "shared",
            from_id: "a",
            to_store: "shared",
            to_id: "b",
            kind: "references",
            generated_by: "gemini:m/x@v1",
            verified_at: null,
            curated: false,
            created_at: "2026-03-01T12:00:00+00:00",
          },
          concepts: [konzept()],
          entry: null,
        },
      ],
    }));
    const { CurationList } = await import("./views/CurationList");
    renderMitQuery(
      <div>
        <input aria-label="Suchfeld" />
        <CurationList state={{ view: "kuration", store: "shared" }} />
      </div>,
    );
    await screen.findByRole("listitem");

    // "x" im Suchfeld ist ein Buchstabe, kein Verwerfen.
    await userEvent.type(screen.getByLabelText("Suchfeld"), "x");
    expect(api.calls.filter((aufruf) => aufruf.method === "POST")).toEqual([]);
  });
});

describe("Qualität (§17.2 Ansicht 8)", () => {
  beforeEach(() => {
    api.on("GET", /\/api\/v1\/stats/, () => ({
      stores: [
        {
          store: "shared",
          concepts: 200,
          edges: 500,
          clusters: 10,
          loose: 50,
          by_scope: {},
          by_type: {},
          by_status: {},
        },
      ],
    }));
    api.on("GET", /curation\/queue/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [
        { ...konzept({ id: "cluster:a", title: "Automatisch 7" }), member_count: 3, curated: false },
        { ...konzept({ id: "cluster:b", title: "Warehouse", curated: true }), member_count: 5, curated: true },
      ],
      next_cursor: null,
    }));
  });

  it("rechnet den Anteil loser Knoten aus und hebt zu viel davon hervor", async () => {
    renderMitQuery(<Quality state={{ view: "qualitaet", store: "shared" }} onChange={() => undefined} />);

    const anteil = await screen.findByText("25.0 %");
    // Über 20 % lose: Das ist ein Rückstand, der auf einen Menschen wartet — also Rot.
    expect(anteil.className).toContain("text-signal-700");
  });

  it("zeigt eine leere Warteschlange als guten Zustand, nicht als Fehler", async () => {
    renderMitQuery(<Quality state={{ view: "qualitaet", store: "shared" }} onChange={() => undefined} />);
    expect(await screen.findByText("Nichts offen.")).toBeInTheDocument();
  });

  it("führt von einem unbetitelten Cluster direkt in den Arbeitsplatz", async () => {
    const gemerkt: unknown[] = [];
    renderMitQuery(
      <Quality state={{ view: "qualitaet", store: "shared" }} onChange={(a) => gemerkt.push(a)} />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Automatisch 7" }));
    expect(gemerkt).toContainEqual({ view: "cluster", cluster: "cluster:a" });
    // Das kuratierte steht nicht in der Liste.
    expect(screen.queryByRole("button", { name: "Warehouse" })).not.toBeInTheDocument();
  });
});
