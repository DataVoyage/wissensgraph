/**
 * Die drei Arbeitsbereiche (§17.2, §17.5) — die Ordnung der Navigation.
 *
 * Die Bereiche folgen den Anwendergruppen, nicht den Datenarten: **Erkunden** für die, die
 * Inhalte zu ihren Themen suchen; **Analysieren** für die, die den Graphen vernetzen;
 * **Verwalten** für die, die den Betrieb führen. Sie ordnen die Navigation und sind keine
 * Rechte — was §17.4 erlaubt, ist überall erlaubt. Erst mit `oidc` (§20.3) werden aus den
 * Bereichen Berechtigungsgrenzen; die Struktur dafür ist dann schon da.
 */

import type { ViewName } from "./state";

export type BereichName = "erkunden" | "analysieren" | "verwalten";

export interface Bereich {
  name: BereichName;
  label: string;
  /** Das Monogramm der schmalen Leiste. */
  kuerzel: string;
  ansichten: ReadonlyArray<{ name: ViewName; label: string }>;
}

export const BEREICHE: readonly Bereich[] = [
  {
    name: "erkunden",
    label: "Erkunden",
    kuerzel: "E",
    ansichten: [
      { name: "graph", label: "Graph" },
      { name: "browser", label: "Dokumente" },
      { name: "persoenlich", label: "Persönlich" },
    ],
  },
  {
    name: "analysieren",
    label: "Analysieren",
    kuerzel: "A",
    ansichten: [
      { name: "kuration", label: "Kuration" },
      { name: "cluster", label: "Cluster" },
      { name: "automatisierung", label: "Automatisierung" },
      { name: "qualitaet", label: "Qualität" },
    ],
  },
  {
    name: "verwalten",
    label: "Verwalten",
    kuerzel: "V",
    ansichten: [
      { name: "quellen", label: "Quellen & Sync" },
      { name: "laeufe", label: "Läufe" },
      { name: "modelle", label: "Modelle & Kosten" },
      { name: "diagnose", label: "Diagnose" },
    ],
  },
] as const;

/** Der Bereich, in dem eine Ansicht wohnt — für Kopfzeile und aktive Markierung. */
export function bereichVon(view: ViewName): Bereich {
  const treffer = BEREICHE.find((bereich) =>
    bereich.ansichten.some((ansicht) => ansicht.name === view),
  );
  // Jede Ansicht wohnt in genau einem Bereich; der Rückfall existiert für den Typprüfer.
  return treffer ?? (BEREICHE[0] as Bereich);
}
