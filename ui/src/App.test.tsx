/** Tests der Verbindungsanzeige — das Abnahmekriterium der SPA in Stufe 0 (§24). */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function mockReadyz(payload: unknown, ok = true): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      json: async () => payload,
    }),
  );
}

describe("App — Verbindungsanzeige", () => {
  beforeEach(() => {
    window.__WG_CONFIG__ = { apiBaseUrl: "http://localhost:8080" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.__WG_CONFIG__;
  });

  it("zeigt beide Stores, wenn die API bereit ist", async () => {
    mockReadyz({
      status: "ready",
      stores: [
        { store: "shared", healthy: true, dsn: "postgresql://db-shared:5432/wg", detail: null },
        { store: "personal", healthy: true, dsn: "postgresql://db-personal:5432/wg", detail: null },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/Beide Stores sind erreichbar/)).toBeInTheDocument();
    });
    expect(screen.getByText(/shared: erreichbar/)).toBeInTheDocument();
    expect(screen.getByText(/personal: erreichbar/)).toBeInTheDocument();
  });

  it("unterscheidet 'nicht bereit' von 'unerreichbar'", async () => {
    // Für den Menschen vor dem Bildschirm sind das zwei verschiedene Probleme mit zwei
    // verschiedenen nächsten Schritten.
    mockReadyz({
      status: "not_ready",
      stores: [
        { store: "shared", healthy: true, dsn: "postgresql://db-shared:5432/wg", detail: null },
        { store: "personal", healthy: false, dsn: "postgresql://db-personal:5432/wg", detail: "weg" },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/nicht bereit/);
    });
    expect(screen.getByText(/personal: nicht erreichbar/)).toBeInTheDocument();
  });

  it("meldet eine unerreichbare API mit Begründung", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Verbindung abgelehnt")));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Keine Verbindung/);
    });
    expect(screen.getByText(/Verbindung abgelehnt/)).toBeInTheDocument();
  });

  it("zeigt zunächst den Prüfzustand", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<App />);

    expect(screen.getByRole("status")).toHaveTextContent(/wird geprüft/);
  });
});
