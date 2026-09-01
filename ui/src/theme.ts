/**
 * Die visuelle Kodierung aus §17.2 als Daten — an einer Stelle, für Zeichenfläche und Legende.
 *
 * §17.2 legt fest, *dass* Typ über Farbe und Provenienz über Linienfarbe kodiert wird. Welche
 * Farbe das im Einzelnen ist, legt diese Datei fest — und zwar genau einmal. Der Grund ist banal
 * und wichtig: Cytoscape braucht Farben als Zeichenkette, Tailwind vergibt sie als Klasse. Ohne
 * eine gemeinsame Quelle driften Graph und Legende auseinander, und eine Legende, die etwas
 * anderes behauptet als das Bild, ist schlimmer als keine.
 *
 * **Warum eine feste Reihe und keine berechnete Farbe.** Vorher entstand die Typfarbe aus einem
 * Hash über den Namen. Das war stabil, aber unkontrolliert: Es konnte zwei kaum unterscheidbare
 * Nachbartöne vergeben, und es konnte Rot vergeben — die eine Farbe, die in dieser Oberfläche für
 * "sieh her" reserviert ist. Die Reihe unten ist von Hand gewählt, durchgehend gedämpft und
 * rotfrei; der Hash wählt nur noch *aus ihr aus*.
 */

/** Die Grautöne, auf die sich Zeichenfläche und Bedienelemente stützen. */
export const TON = {
  weiss: "#ffffff",
  hell: "#f8f8f9",
  linie: "#e4e4e8",
  gedaempft: "#a1a1ab",
  text: "#3e3e46",
  tinte: "#18181b",
} as const;

/** Das Markenrot in den beiden Abstufungen, die die Oberfläche benutzt. */
export const SIGNAL = { normal: "#e10915", dunkel: "#c00812", blass: "#fbd5d8" } as const;

/**
 * Die Farbreihe der Konzepttypen — gedämpft, unterscheidbar, ohne Rot und ohne Schwarz.
 *
 * Acht Töne, weil eine Taxonomie mit mehr als acht gleichzeitig sichtbaren Typen ohnehin nicht
 * mehr über Farbe zu lesen wäre; ab da trägt die Beschriftung.
 *
 * Alle liegen bewusst in einem mittleren Helligkeitsband. Ein sehr dunkler Ton stand hier einmal
 * an erster Stelle und war der Cluster-Farbe zum Verwechseln ähnlich — ausgerechnet an der
 * Unterscheidung, auf die es in einer Karte zuerst ankommt: Behälter gegen Inhalt. Schwarz gehört
 * den Clustern, Rot gehört dem Signal, und was übrig bleibt, gehört den Typen.
 */
export const TYPTOENE: readonly string[] = [
  "#4a6b8a", // Stahlblau
  "#2f7a6f", // Petrol
  "#8a6d3b", // Ocker
  "#6b5b8a", // Pflaume
  "#5c7a3f", // Oliv
  "#7a5548", // Kastanie
  "#3f6b7a", // Schiefer
  "#8a5a7a", // Mauve
] as const;

/**
 * Eine Farbe je Konzepttyp.
 *
 * Cluster fallen aus der Reihe und bekommen den dunkelsten Ton: Sie sind keine Inhalte, sondern
 * Behälter, und in einer Karte über den ganzen Bestand ist das der Unterschied, an dem man sich
 * zuerst orientiert.
 *
 * **Warum die Taxonomie mitkommt.** Zuerst entstand die Farbe allein aus einem Hash über den
 * Namen. Das war stabil und trotzdem falsch: `Confluence Page` und `Jira Issue` fielen auf
 * denselben Ton, und die Karte zeigte zwei verschiedene Dinge in einer Farbe — die Kodierung aus
 * §17.2 war damit an genau der Stelle wirkungslos, an der man sie braucht. Kennt der Aufrufer die
 * Typenliste aus `/config/effective`, wird stattdessen nach ihrer Reihenfolge vergeben; dann sind
 * so viele Typen unterscheidbar, wie es Töne gibt. Der Hash bleibt als Rückfall für Typen, die in
 * der Liste nicht vorkommen — er ist immer noch besser als gar keine Farbe.
 *
 * Args:
 *   typ: Der Konzepttyp.
 *   reihenfolge: Die Typen dieser Installation in ihrer konfigurierten Reihenfolge.
 */
export function farbeFuerTyp(typ: string, reihenfolge: readonly string[] = []): string {
  if (typ === "Cluster") {
    return TON.tinte;
  }
  const platz = reihenfolge.indexOf(typ);
  if (platz >= 0) {
    return TYPTOENE[platz % TYPTOENE.length] as string;
  }
  let hash = 0;
  for (const zeichen of typ) {
    hash = (hash * 31 + zeichen.charCodeAt(0)) % 4096;
  }
  return TYPTOENE[hash % TYPTOENE.length] as string;
}

/**
 * Die Provenienz einer Kante als Farbe (§17.2).
 *
 * Die Zuordnung ist nicht beliebig: Was ein Mensch gesetzt hat, ist beinahe schwarz — fest und
 * fertig. Was Code aus einer Quelle abgeleitet hat, ist grau — vorhanden, aber nicht der Punkt.
 * Was ein Modell vorgeschlagen hat, ist rot — es wartet auf jemanden (Leitprinzip 6).
 */
export function farbeFuerKante(generatedBy: string | null): string {
  if (generatedBy === null) {
    return "#2a2a30";
  }
  return generatedBy.startsWith("code:") ? "#a1a1ab" : SIGNAL.normal;
}

/** Die Felder, aus denen sich Herkunft und Bestätigungsstand einer Kante ablesen lassen. */
interface Herkunft {
  generated_by: string | null;
  curated: boolean;
  verified_at: string | null;
}

/**
 * Ob eine Kante erzeugt und von niemandem bestätigt ist (§17.3, Leitprinzip 6).
 *
 * Das schließt beides ein: den Vorschlag eines Modells und die Ableitung aus dem Inhalt durch
 * Code. §17.3 verlangt für "generierte, unbestätigte Kanten" eine klare Unterscheidung, und ein
 * Clustering-Ergebnis ist genauso wenig von Hand gesetzt wie eine Modellrelation.
 */
export function istUnbestaetigt(kante: Herkunft): boolean {
  return kante.generated_by !== null && !kante.curated && kante.verified_at === null;
}

/**
 * Ob eine Kante der Vorschlag eines **Modells** ist — die engere Frage.
 *
 * Der Unterschied ist der zwischen "erzeugt" und "geraten", und er entscheidet, wo Rot stehen
 * darf. Eine `member`-Kante aus dem Clustering ist unbestätigt, aber sie ist eine Rechnung; ein
 * großes Cluster brächte auf einen Schlag zwanzig rote Zeilen ins Panel, und danach hieße Rot
 * dort nichts mehr. Was ein Modell *vorgeschlagen* hat, wartet dagegen wirklich auf einen
 * Menschen — und nur das ist in dieser Oberfläche rot.
 */
export function istModellvorschlag(kante: Herkunft): boolean {
  return istUnbestaetigt(kante) && !(kante.generated_by ?? "").startsWith("code:");
}

/** Die Einträge der Legende zur Provenienz — dieselbe Quelle wie der Graph. */
export const PROVENIENZ: ReadonlyArray<{ label: string; farbe: string; erklaerung: string }> = [
  { label: "von Hand", farbe: farbeFuerKante(null), erklaerung: "Ein Mensch hat sie gesetzt." },
  {
    label: "aus der Quelle",
    farbe: farbeFuerKante("code:sync"),
    erklaerung: "Aus dem Inhalt abgeleitet, kein Modell beteiligt.",
  },
  {
    label: "Modellvorschlag",
    farbe: farbeFuerKante("model:x"),
    erklaerung: "Gestrichelt, solange niemand sie bestätigt hat.",
  },
];
