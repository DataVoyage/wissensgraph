/**
 * Ein nachgebildeter Server für die Komponententests der Oberfläche.
 *
 * Nachgebildet wird die *API*, nicht der Graph: Die Tests hier prüfen, ob die Oberfläche die
 * Regeln aus §17.2 bis §17.4 sichtbar macht — ob ein gesperrtes Feld gesperrt aussieht, ob ein
 * unbestätigter Vorschlag als solcher erkennbar ist, ob eine Verschiebung wirklich zwei Aufrufe
 * auslöst. Ob die Regel *stimmt*, prüfen die Python-Tests gegen den Dienst; das hier doppelt
 * nachzubauen hieße, die Nachbildung zu prüfen.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import { configure } from "./api/client";

/** Eine aufgezeichnete Anfrage — die Grundlage der Zusicherungen über Aufrufe. */
export interface Aufruf {
  method: string;
  url: string;
  body: unknown;
}

/** Der nachgebildete Server: Antworten je Pfadmuster, plus ein Protokoll der Aufrufe. */
export class FakeApi {
  readonly calls: Aufruf[] = [];
  private readonly routen: Array<[RegExp, string, () => unknown]> = [];

  /** Legt eine Antwort für ein Pfadmuster fest; die zuletzt gesetzte gewinnt. */
  on(method: string, muster: RegExp, antwort: () => unknown): this {
    this.routen.unshift([muster, method, antwort]);
    return this;
  }

  /** Installiert sich als globales `fetch`. */
  install(): void {
    configure({ baseUrl: "", token: "test" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (eingabe: RequestInfo | URL, init?: RequestInit) => {
        const url = String(eingabe);
        const method = init?.method ?? "GET";
        this.calls.push({
          method,
          url,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        const treffer = this.routen.find(
          ([muster, verb]) => verb === method && muster.test(url),
        );
        if (treffer === undefined) {
          return new Response(
            JSON.stringify({ title: "Nicht gefunden", detail: `keine Route für ${method} ${url}` }),
            { status: 404, headers: { "Content-Type": "application/problem+json" } },
          );
        }
        return new Response(JSON.stringify(treffer[2]()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  }
}

/** Rendert eine Komponente mit einem frischen Query-Cache — kein Zustand zwischen Tests. */
export function renderMitQuery(element: ReactElement): RenderResult {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

/** Die aufgelöste Konfiguration, wie `/config/effective` sie liefert (§17.1). */
export const KONFIGURATION = {
  env: "test",
  scopes: [
    { name: "engineering", store: "shared", description: null },
    { name: "personal", store: "personal", description: null },
  ],
  concept_types: [
    { name: "Confluence Page", stores: ["shared"], source_mirrored: true },
    { name: "Cluster", stores: ["shared", "personal"], source_mirrored: false },
    { name: "Note", stores: ["personal"], source_mirrored: false },
  ],
  edge_kinds: { structural: ["member", "related"], semantic: ["references", "depends_on"] },
  stores: { shared: {}, personal: {} },
  orphans: { loose_threshold: 1 },
  api: { auth_mode: "token" },
};

/**
 * Ein Kartenausschnitt in der Form, die `/graph/map` liefert (§17.2 Ansicht 1).
 *
 * Die Karte ist der Vorgabemodus der Graph-Ansicht; ohne eine Antwort darauf liefe jeder Test
 * dieser Ansicht in einen 404 und prüfte anschließend die Fehleranzeige statt der Sache.
 */
export function karte(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    store: "shared",
    nodes: [],
    edges: [],
    edge_count: 0,
    next_cursor: null,
    truncated: false,
    ...overrides,
  };
}

/** Ein Kartenknoten — mit `degree` statt `score`, weil eine Karte keinen Startpunkt hat. */
export function kartenknoten(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "confluence:1",
    store: "shared",
    scope: "engineering",
    type: "Confluence Page",
    title: "Eine Seite",
    status: "stable",
    degree: 1,
    ...overrides,
  };
}

/** Ein Konzept in der Form, die die API liefert. */
export function konzept(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "confluence:1",
    store: "shared",
    scope: "engineering",
    type: "Confluence Page",
    title: "Eine Seite",
    description: "Kurz beschrieben",
    resource: null,
    tags: [],
    audience: [],
    status: "stable",
    source_name: null,
    external_id: null,
    source_updated_at: null,
    generated_by: null,
    verified_by: null,
    verified_at: null,
    curated: false,
    created_at: "2026-03-01T12:00:00+00:00",
    updated_at: "2026-03-01T12:00:00+00:00",
    ...overrides,
  };
}

/** Eine Kante in der Form, die die API liefert. */
export function kante(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    from_store: "shared",
    from_id: "confluence:1",
    to_store: "shared",
    to_id: "confluence:2",
    kind: "references",
    weight: null,
    confidence: 0.8,
    reasoning: "Beide beschreiben dieselbe Ladestrecke.",
    resolved: true,
    generated_by: "gemini:m/relation_extraction@v1",
    verified_by: null,
    verified_at: null,
    curated: false,
    created_at: "2026-03-01T12:00:00+00:00",
    ...overrides,
  };
}
