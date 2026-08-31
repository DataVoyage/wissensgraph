/**
 * Die Kurationswege, die den Bestand verändern (§17.3).
 *
 * Jede Kuration ist sofort persistent, im Journal sichtbar und rückgängig zu machen. Diese Tests
 * prüfen genau das an der Oberfläche: dass die Aktion einen Aufruf auslöst, dass der
 * Journaleintrag mit seinem Undo angeboten wird — und dass eine abgelehnte Rücknahme als Meldung
 * erscheint statt stillschweigend zu verpuffen.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, get, send } from "./api/client";
import { ConceptPanel } from "./components/ConceptPanel";
import { ClusterWorkbench } from "./views/ClusterWorkbench";
import { Operations } from "./views/Operations";
import { FakeApi, kante, konzept, renderMitQuery } from "./test-support";

let api: FakeApi;

function detail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...konzept(),
    body: null,
    content_hash: "abc",
    outgoing: [],
    incoming: [],
    clusters: [],
    locked_fields: [],
    ...overrides,
  };
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
  api = new FakeApi();
  api.install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Client", () => {
  it("übersetzt ein Problem-Detail in eine lesbare Ausnahme (§16.1)", async () => {
    api.on("GET", /kaputt/, () => ({}));

    await expect(get("/nichts")).rejects.toBeInstanceOf(ApiError);
    await expect(get("/nichts")).rejects.toThrow(/keine Route/);
  });

  it("hängt Query-Parameter an und lässt leere weg", async () => {
    api.on("GET", /\/api\/v1\/concepts/, () => ({ items: [] }));

    await get("/api/v1/concepts", { store: "shared", scope: undefined, q: "" });

    expect(api.calls[0]?.url).toBe("/api/v1/concepts?store=shared");
  });

  it("schickt einen JSON-Körper und Query-Parameter zugleich", async () => {
    api.on("POST", /\/api\/v1\/edges/, () => ({ ok: true }));

    await send("POST", "/api/v1/edges", { from_id: "a" }, { store: "personal" });

    expect(api.calls[0]?.url).toBe("/api/v1/edges?store=personal");
    expect(api.calls[0]?.body).toEqual({ from_id: "a" });
  });
});

describe("Journal und Undo (§17.3)", () => {
  it("bietet zu einem umkehrbaren Eintrag ein Undo an", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({
      items: [
        {
          id: 7,
          change_type: "edge_added",
          actor: "user:token",
          concept_id: "confluence:1",
          edge_id: null,
          run_id: null,
          changed_at: "2026-03-01T12:00:00+00:00",
          detail: null,
          undoable: true,
        },
      ],
    }));
    api.on("POST", /curation\/undo/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);
    await waitFor(() => expect(screen.getByText("edge_added")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "rückgängig" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.url.includes("curation/undo"));
      expect(aufruf?.body).toEqual({ entry_id: 7, store: "shared" });
    });
  });

  it("zeigt kein Undo an einem Eintrag, der sich nicht zurücknehmen lässt", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({
      items: [
        {
          id: 8,
          change_type: "updated",
          actor: "user:token",
          concept_id: "confluence:1",
          edge_id: null,
          run_id: null,
          changed_at: null,
          detail: null,
          undoable: false,
        },
      ],
    }));

    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);

    await waitFor(() => expect(screen.getByText("updated")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "rückgängig" })).not.toBeInTheDocument();
  });

  it("meldet eine abgelehnte Rücknahme sichtbar", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({
      items: [
        {
          id: 9,
          change_type: "edge_added",
          actor: "user:token",
          concept_id: "confluence:1",
          edge_id: null,
          run_id: null,
          changed_at: null,
          detail: null,
          undoable: true,
        },
      ],
    }));

    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);
    await waitFor(() => expect(screen.getByText("edge_added")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "rückgängig" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("meldet ein leeres Journal als Zustand", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));

    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);

    await waitFor(() => expect(screen.getByText("Noch keine Änderung.")).toBeInTheDocument());
  });
});

describe("Detailpanel", () => {
  it("setzt einen geänderten Status erst auf Knopfdruck", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    api.on("PATCH", /concepts\//, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(<ConceptPanel detail={detail() as never} onOpen={() => undefined} />);
    const knopf = screen.getByRole("button", { name: "Setzen" });
    expect(knopf).toBeDisabled();

    await userEvent.clear(screen.getByLabelText("Status"));
    await userEvent.type(screen.getByLabelText("Status"), "deprecated");
    await userEvent.click(knopf);

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.method === "PATCH")).toBe(true),
    );
  });

  it("öffnet ein Kantenziel", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    const geoeffnet: string[] = [];

    renderMitQuery(
      <ConceptPanel
        detail={detail({ outgoing: [kante({ curated: true, verified_at: "x" })] }) as never}
        onOpen={(id) => geoeffnet.push(id)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "confluence:2" }));

    expect(geoeffnet).toEqual(["confluence:2"]);
  });

  it("kennzeichnet den persönlichen Store deutlich (§17.2 Ansicht 5)", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));

    renderMitQuery(
      <ConceptPanel detail={detail({ store: "personal" }) as never} onOpen={() => undefined} />,
    );

    expect(screen.getByText(/verlässt diesen Rechner nicht/)).toBeInTheDocument();
  });

  it("listet die Cluster eines Konzepts", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));

    renderMitQuery(
      <ConceptPanel
        detail={detail({ clusters: [{ id: "cluster:a", title: "Warehouse" }] }) as never}
        onOpen={() => undefined}
      />,
    );

    expect(screen.getByText("Warehouse")).toBeInTheDocument();
  });

  it("bestätigt eine unbestätigte Kante direkt aus dem Panel", async () => {
    api.on("GET", /concepts\/.*\/history/, () => ({ items: [] }));
    api.on("POST", /edges\/.*\/verify/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(
      <ConceptPanel detail={detail({ outgoing: [kante()] }) as never} onOpen={() => undefined} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "✓" }));

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.url.includes("/verify"))).toBe(true),
    );
  });
});

describe("Cluster-Arbeitsplatz — weitere Wege", () => {
  const zusammenfassung = {
    ...konzept({ id: "cluster:a", title: "Warehouse", type: "Cluster" }),
    member_count: 1,
    centroid_age_seconds: 120,
  };

  beforeEach(() => {
    api.on("GET", /\/api\/v1\/clusters\?/, () => ({
      store: "shared",
      items: [zusammenfassung, { ...zusammenfassung, id: "cluster:b", title: "Incident" }],
      next_cursor: null,
    }));
    api.on("GET", /clusters\/cluster:a\?/, () => ({
      ...konzept({ id: "cluster:a", title: "Warehouse", type: "Cluster" }),
      members: [konzept()],
      related: [{ id: "cluster:b", title: "Incident", similarity: 0.72 }],
      centroid_age_seconds: 120,
      manual_title: false,
    }));
  });

  it("benennt ein Cluster um und sperrt damit die Neubetitelung (§13.2)", async () => {
    api.on("PATCH", /clusters\/cluster:a/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText("Neuer Titel")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("Neuer Titel"), "Mein Titel");
    await userEvent.click(screen.getByRole("button", { name: "Umbenennen" }));

    await waitFor(() => {
      const aufruf = api.calls.find((eintrag) => eintrag.method === "PATCH");
      expect(aufruf?.body).toEqual({ title: "Mein Titel" });
    });
  });

  it("verschmilzt zwei Cluster und wechselt auf das Ziel", async () => {
    const gemerkt: unknown[] = [];
    api.on("POST", /clusters\/merge/, () => ({ entry: {}, concept: null, edge: null }));

    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={(aenderung) => gemerkt.push(aenderung)}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText("Verschmelzen mit")).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Verschmelzen mit"), "cluster:b");

    await waitFor(() => expect(gemerkt).toContainEqual({ cluster: "cluster:b" }));
  });

  it("zeigt verwandte Cluster mit ihrem Ähnlichkeitswert", async () => {
    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );

    await waitFor(() => expect(screen.getByText("0.72")).toBeInTheDocument());
  });

  it("entfernt ein Mitglied einzeln — mit Ausschlussvermerk auf Serverseite (§13.4)", async () => {
    api.on("DELETE", /clusters\/cluster:a\/members\//, () => ({ entry: {} }));

    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "entfernen" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "entfernen" }));

    await waitFor(() =>
      expect(api.calls.some((aufruf) => aufruf.method === "DELETE")).toBe(true),
    );
  });

  it("fordert eine Auswahl, bevor irgendetwas passiert", async () => {
    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared" }}
        onChange={() => undefined}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("Cluster links auswählen.")).toBeInTheDocument(),
    );
  });

  it("meldet einen gescheiterten Verschiebeschritt sichtbar", async () => {
    renderMitQuery(
      <ClusterWorkbench
        state={{ view: "cluster", store: "shared", cluster: "cluster:a" }}
        onChange={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mitglied-confluence:1")).toBeInTheDocument());

    const daten = new Map<string, string>();
    const transfer = {
      setData: (typ: string, wert: string) => daten.set(typ, wert),
      getData: (typ: string) => daten.get(typ) ?? "",
    };
    screen
      .getByTestId("mitglied-confluence:1")
      .dispatchEvent(
        Object.assign(new Event("dragstart", { bubbles: true }), { dataTransfer: transfer }),
      );
    screen
      .getByTestId("cluster-cluster:b")
      .dispatchEvent(
        Object.assign(new Event("drop", { bubbles: true }), { dataTransfer: transfer }),
      );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});

describe("Betriebsansicht — Zahlen", () => {
  it("zeigt Bestand und Modellnutzung", async () => {
    api.on("GET", /config\/effective/, () => ({ scopes: [], stores: {} }));
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [] }));
    api.on("GET", /\/api\/v1\/runs\?/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/stats/, () => ({
      stores: [
        {
          store: "shared",
          concepts: 14,
          edges: 6,
          clusters: 3,
          loose: 1,
          by_scope: {},
          by_type: {},
          by_status: {},
        },
      ],
    }));
    api.on("GET", /models\/usage/, () => ({
      store: "shared",
      items: [
        {
          task: "embedding",
          provider: "p",
          model: "m",
          calls: 2,
          tokens_in: 100,
          tokens_out: 0,
          cost_estimate_eur: 0.0001,
          cache_hits: 0,
          errors: 0,
        },
      ],
    }));
    api.on("GET", /\/api\/v1\/models$/, () => ({
      tasks: [
        {
          task: "embedding",
          provider: "p",
          model: "m",
          model_key: "p:m",
          local: false,
          dim: 512,
          temperature: null,
          configured: false,
          fallbacks: [],
          generated_by: "p:m/embedding@v1",
        },
      ],
      policies: {},
      budget: {},
    }));

    renderMitQuery(
      <Operations state={{ view: "betrieb", store: "shared" }} onChange={() => undefined} />,
    );

    await waitFor(() => expect(screen.getByText("14")).toBeInTheDocument());
    expect(screen.getByText("0.0001")).toBeInTheDocument();
    expect(screen.getByText(/kein Schlüssel hinterlegt/)).toBeInTheDocument();
  });

  it("meldet eine abgelehnte Lauf-Anfrage sichtbar", async () => {
    api.on("GET", /config\/effective/, () => ({
      scopes: [{ name: "engineering", store: "shared", description: null }],
      stores: {},
    }));
    api.on("GET", /\/api\/v1\/sources/, () => ({ items: [] }));
    api.on("GET", /\/api\/v1\/runs\?/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/stats/, () => ({ stores: [] }));
    api.on("GET", /models\/usage/, () => ({ store: "shared", items: [] }));
    api.on("GET", /\/api\/v1\/models$/, () => ({ tasks: [], policies: {}, budget: {} }));

    renderMitQuery(
      <Operations state={{ view: "betrieb", store: "shared" }} onChange={() => undefined} />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "embed" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "embed" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
