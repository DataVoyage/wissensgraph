/**
 * Der HTTP-Client der Oberfläche (§16.1, §17.1).
 *
 * Zwei Dinge macht er, und sonst nichts: Er hängt den Bearer-Token an, und er übersetzt eine
 * Fehlerantwort nach RFC 7807 in eine Ausnahme mit lesbarer Meldung. Beides gehört an genau eine
 * Stelle — sonst entscheidet jede Ansicht für sich, wie sie mit einem `409` umgeht, und der
 * Nutzer bekommt für dieselbe Regel drei verschiedene Sätze zu sehen.
 *
 * Fachlogik steht hier keine. Die Oberfläche enthält keine (§17.1): Ob ein Feld gesperrt ist,
 * sagt `locked_fields`; welche Kantenarten es gibt, sagt `/config/effective`.
 */

import type { EffectiveConfig } from "./types";

/** Ein Fehler der API, aufbereitet aus einem Problem-Detail nach RFC 7807. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Wie sich der Client ausweist und wohin er spricht. */
export interface ClientOptions {
  baseUrl: string;
  /** Bearer-Token; entfällt bei `auth_mode: none` (§17.1). */
  token?: string | null;
}

let optionen: ClientOptions = { baseUrl: "", token: null };

/** Setzt Basis-URL und Token für alle folgenden Aufrufe. */
export function configure(neu: ClientOptions): void {
  optionen = neu;
}

/** Die aktuell gesetzten Optionen — die Ereignisquelle braucht die Basis-URL. */
export function options(): ClientOptions {
  return optionen;
}

function headers(mitKoerper: boolean): HeadersInit {
  const kopf: Record<string, string> = { Accept: "application/json" };
  if (mitKoerper) {
    kopf["Content-Type"] = "application/json";
  }
  if (optionen.token) {
    kopf.Authorization = `Bearer ${optionen.token}`;
  }
  return kopf;
}

async function auswerten<T>(antwort: Response): Promise<T> {
  if (antwort.status === 204) {
    return undefined as T;
  }
  const text = await antwort.text();
  const nutzlast: unknown = text ? JSON.parse(text) : null;
  if (!antwort.ok) {
    const problem = (nutzlast ?? {}) as { title?: string; detail?: string };
    throw new ApiError(
      antwort.status,
      problem.title ?? "Fehler",
      problem.detail ?? problem.title ?? `HTTP ${antwort.status}`,
    );
  }
  return nutzlast as T;
}

/**
 * Eine GET-Anfrage; `params` werden als Query angehängt, `undefined` entfällt.
 *
 * Eine Liste wird zu mehreren gleichnamigen Parametern (`kinds=member&kinds=references`) und
 * nicht zu einer kommagetrennten Zeichenkette. So erwartet FastAPI eine `list[str]`, und eine
 * Kantenart mit Komma im Namen — die Konfiguration verbietet sie nicht — bliebe heil.
 */
export async function get<T>(
  pfad: string,
  params: Record<string, string | number | boolean | string[] | null | undefined> = {},
): Promise<T> {
  const suche = new URLSearchParams();
  for (const [name, wert] of Object.entries(params)) {
    if (Array.isArray(wert)) {
      for (const eintrag of wert) {
        suche.append(name, eintrag);
      }
    } else if (wert !== undefined && wert !== null && wert !== "") {
      suche.set(name, String(wert));
    }
  }
  const anhang = suche.toString();
  const antwort = await fetch(`${optionen.baseUrl}${pfad}${anhang ? `?${anhang}` : ""}`, {
    headers: headers(false),
  });
  return auswerten<T>(antwort);
}

/** Eine schreibende Anfrage mit JSON-Körper. */
export async function send<T>(
  methode: "POST" | "PATCH" | "DELETE",
  pfad: string,
  koerper?: unknown,
  params: Record<string, string | undefined> = {},
): Promise<T> {
  const suche = new URLSearchParams();
  for (const [name, wert] of Object.entries(params)) {
    if (wert) {
      suche.set(name, wert);
    }
  }
  const anhang = suche.toString();
  const antwort = await fetch(`${optionen.baseUrl}${pfad}${anhang ? `?${anhang}` : ""}`, {
    method: methode,
    headers: headers(koerper !== undefined),
    body: koerper === undefined ? undefined : JSON.stringify(koerper),
  });
  return auswerten<T>(antwort);
}

/**
 * Die aufgelöste Konfiguration — die einzige Quelle der Fachregeln in dieser Oberfläche (§17.1).
 */
export function fetchConfig(): Promise<EffectiveConfig> {
  return get<EffectiveConfig>("/api/v1/config/effective");
}
