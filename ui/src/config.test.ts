/** Tests der Laufzeitkonfiguration (§17.1). */

import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "./config";

describe("loadConfig", () => {
  afterEach(() => {
    delete window.__WG_CONFIG__;
  });

  it("liest die injizierte Basis-URL", () => {
    window.__WG_CONFIG__ = { apiBaseUrl: "https://wg.example.com" };

    expect(loadConfig().apiBaseUrl).toBe("https://wg.example.com");
  });

  it("nimmt ohne injizierte Konfiguration denselben Ursprung an", () => {
    // Im Vite-Dev-Server gibt es keine /config.js — das ist kein Fehler, der Proxy leitet weiter.
    expect(loadConfig().apiBaseUrl).toBe("");
  });

  it("nimmt denselben Ursprung an, wenn die Injektion unvollständig ist", () => {
    window.__WG_CONFIG__ = {};

    expect(loadConfig().apiBaseUrl).toBe("");
  });
});
