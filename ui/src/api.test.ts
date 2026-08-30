/** Tests des API-Zugriffs (§16.2). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchReadyz } from "./api";

const STORES = [
  { store: "shared", healthy: true, dsn: "postgresql://db-shared:5432/wg", detail: null },
  { store: "personal", healthy: true, dsn: "postgresql://db-personal:5432/wg", detail: null },
];

describe("fetchReadyz", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("meldet 'verbunden', wenn alle Stores erreichbar sind", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ json: async () => ({ status: "ready", stores: STORES }) }),
    );

    const state = await fetchReadyz("http://localhost:8080");

    expect(state).toEqual({ kind: "verbunden", stores: STORES });
  });

  it("meldet 'nicht_bereit', wenn die API antwortet aber ein Store fehlt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ json: async () => ({ status: "not_ready", stores: STORES }) }),
    );

    const state = await fetchReadyz("http://localhost:8080");

    expect(state.kind).toBe("nicht_bereit");
  });

  it("meldet 'unerreichbar' bei einem Netzwerkfehler", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));

    const state = await fetchReadyz("http://localhost:8080");

    expect(state).toEqual({ kind: "unerreichbar", grund: "ECONNREFUSED" });
  });

  it("verkraftet einen geworfenen Nicht-Fehler", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue("kaputt"));

    const state = await fetchReadyz("http://localhost:8080");

    expect(state).toEqual({ kind: "unerreichbar", grund: "kaputt" });
  });

  it("fragt den /readyz-Pfad der übergebenen Basis-URL ab", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ json: async () => ({ status: "ready", stores: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReadyz("https://wg.example.com");

    expect(fetchMock).toHaveBeenCalledWith("https://wg.example.com/readyz", expect.anything());
  });
});
