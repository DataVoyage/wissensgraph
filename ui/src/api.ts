/**
 * Zugriff auf die HTTP-API (§16).
 *
 * In Stufe 0 nur die Betriebsendpunkte. Ab Stufe 11 wird dieser Client aus dem
 * OpenAPI-Schema (`/api/v1/openapi.json`) generiert; bis dahin bleibt er von Hand geschrieben
 * und klein.
 */

/** Zustand eines einzelnen Stores, wie ihn `/readyz` meldet. */
export interface StoreStatus {
  store: string;
  healthy: boolean;
  /** Maskierter DSN — enthält nie ein Passwort (§20.2). */
  dsn: string;
  detail: string | null;
}

/** Antwort von `GET /readyz`. */
export interface ReadyzResponse {
  status: "ready" | "not_ready";
  stores: StoreStatus[];
}

/** Ergebnis einer Verbindungsprüfung aus Sicht der UI. */
export type ConnectionState =
  | { kind: "pruefe" }
  | { kind: "verbunden"; stores: StoreStatus[] }
  | { kind: "nicht_bereit"; stores: StoreStatus[] }
  | { kind: "unerreichbar"; grund: string };

/**
 * Fragt die Bereitschaft der API ab.
 *
 * Unterscheidet bewusst drei Fälle: erreichbar und bereit, erreichbar aber nicht bereit (eine
 * Datenbank fehlt), und gar nicht erreichbar. Für den Menschen vor dem Bildschirm sind das drei
 * verschiedene Probleme mit drei verschiedenen nächsten Schritten.
 */
export async function fetchReadyz(apiBaseUrl: string): Promise<ConnectionState> {
  try {
    const response = await fetch(`${apiBaseUrl}/readyz`, {
      headers: { Accept: "application/json" },
    });
    const payload = (await response.json()) as ReadyzResponse;
    return payload.status === "ready"
      ? { kind: "verbunden", stores: payload.stores }
      : { kind: "nicht_bereit", stores: payload.stores };
  } catch (error) {
    return {
      kind: "unerreichbar",
      grund: error instanceof Error ? error.message : String(error),
    };
  }
}
