/**
 * Die Anordnung des Graphen — reine Logik, ohne WebGL und ohne Zeichenfläche.
 *
 * Alles, was sich *berechnen* lässt, steht hier und ist ohne Browser-Fähigkeiten prüfbar:
 * der Abgleich der Daten in das graphology-Modell (unter Erhalt der Positionen), die
 * Übersetzung der Physik-Regler in ForceAtlas2-Einstellungen und die beiden geometrischen
 * Layouts (konzentrisch, hierarchisch). Die Zeichenfläche (`GraphCanvas`) ruft nur noch auf.
 *
 * Die visuelle Kodierung ist die aus §17.2:
 *
 * | Merkmal | Kodierung |
 * |---|---|
 * | Store | Knotenform — Kreis für `shared`, Raute für `personal` |
 * | Typ | Knotenfarbe (`theme.ts`) |
 * | Gewicht (Score bzw. Grad) | Knotengröße |
 * | Kantenart | Linienstärke — strukturell (`member`) kräftig, semantisch fein |
 * | Provenienz | Linienfarbe — manuell / Code / Modell |
 * | unbestätigte Modellkante | voll deckend, alles Bestätigte tritt halbtransparent zurück |
 *
 * Die letzten beiden Zeilen sind gegenüber Cytoscape **geändert**, und zwar bewusst: WebGL
 * kennt keine gestrichelten Linien, und ein eigenes Kantenprogramm dafür wäre mehr Fläche als
 * Aussage. Deckkraft trägt dieselbe Botschaft — was auf einen Menschen wartet, steht vorn;
 * was geprüft ist, tritt zurück (Leitprinzip 6). Spezifikation und `theme.ts` sind auf diese
 * Fassung gehoben.
 */

import type Graph from "graphology";

import type { Edge } from "../api/types";
import { farbeFuerKante, farbeFuerTyp, istUnbestaetigt } from "../theme";

/** Die Layouts aus §17.2: kraftbasiert (die Physik), konzentrisch, hierarchisch. */
export type LayoutName = "physik" | "concentric" | "breadthfirst";

/** Die Stellschrauben der Simulation — §17.2 verlangt steuerbare Layouts, nicht ein festes. */
export interface PhysikWerte {
  /** Wie stark sich Knoten abstoßen; wirkt als Abstand. */
  abstossung: number;
  /** Wunschlänge einer Kante — in ForceAtlas2 indirekt über die Skalierung. */
  kantenlaenge: number;
  /** Wie stark alles zur Mitte gezogen wird; 0 lässt den Graphen auseinanderdriften. */
  schwerkraft: number;
}

export const PHYSIK_VORGABE: PhysikWerte = {
  abstossung: 28,
  kantenlaenge: 110,
  schwerkraft: 0.6,
};

/**
 * Ein Knoten, wie ihn die Zeichenfläche braucht.
 *
 * Bewusst nicht `GraphNode` aus der API: Eine Karte hat keinen Score und eine Traversierung
 * keinen Grad (siehe `graph.py`). Was beide haben, ist *ein* Gewicht zwischen 0 und 1, und
 * welche Größe es bedeutet, entscheidet die Ansicht — nicht die Zeichenfläche.
 */
export interface CanvasNode {
  id: string;
  store: string;
  type: string;
  title: string | null;
  status: string;
  /** 0 … 1; steuert den Durchmesser. */
  gewicht: number;
}

/** Ab wie vielen Knoten ForceAtlas2 die Barnes-Hut-Näherung benutzt. */
export const BARNES_HUT_AB = 500;

/**
 * Wie lange sich die Simulation sortieren darf, bevor sie stehen bleibt.
 *
 * Der Worker rechnet zwar neben dem UI-Thread — aber ein Graph, der nie zur Ruhe kommt, ist
 * keine Antwort, sondern ein Bildschirmschoner. Große Graphen brauchen länger als kleine.
 */
export function einschwingzeitMs(anzahl: number): number {
  // Acht Millisekunden je Knoten, nicht eine: Der alte Wert stammt aus dem linearen Modus und
  // stoppte die Simulation beim Vollbestand (2.210 Knoten) nach 4,7 Sekunden — mitten im
  // Sortieren. Jedes Bild sah aus wie ein Klumpen, weil es das Standbild einer halb fertigen
  // Bewegung war. LinLog braucht länger, bis die Gruppen ihre Plätze gefunden haben.
  return Math.min(25_000, 2500 + anzahl * 8);
}

/**
 * Die Physik-Regler, übersetzt in ForceAtlas2.
 *
 * ForceAtlas2 kennt keine Wunschlänge je Kante; der Gleichgewichtsabstand entsteht aus dem
 * Verhältnis von Abstoßung (`scalingRatio`) und Anziehung. Der Kantenlängen-Regler skaliert
 * deshalb die Abstoßung quadratisch mit — dieselbe Wirkung über einen anderen Hebel. Oberhalb
 * von `BARNES_HUT_AB` rechnet die Näherung: n·log n statt n², und genau das trägt die
 * Zielmarke von 5.000 Knoten mit laufender Physik.
 *
 * **Die Vorgabe ist bewusst weit.** Der Graph beantwortet am Anfang eine strukturelle Frage —
 * *wie viele Gruppen gibt es, was hängt zusammen, was liegt allein* —, und dafür braucht das
 * Auge Zwischenraum. Eine dichte Anordnung sieht nach viel Inhalt aus und zeigt nichts: Bei
 * einigen tausend Knoten verschmelzen die Kanten zu einem Gewebe, und die Gruppen, um die es
 * geht, verschwinden darin. Ins Einzelne geht man danach über Zoom und Filter, nicht dadurch,
 * dass von Anfang an alles eng beieinandersteht.
 *
 * Konkret gegenüber der ersten Fassung: die Abstoßung dreifach, die Schwerkraft (die alles zur
 * Mitte zieht und damit verdichtet) deutlich schwächer, und `outboundAttractionDistribution`
 * aus — es zieht stark verbundene Knoten zusammen und lässt genau die Ballungen entstehen, die
 * hier stören.
 *
 * **Die Abstoßung hängt an der Kantendichte, nicht nur an der Knotenzahl.** Das ist an echten
 * Daten gelernt: Die Werte oben waren an einem synthetischen Bestand mit mittlerem Grad 2,5
 * eingestellt und sahen dort gut aus. Der erste echte Bestand hatte 7,4 — und ballte sich zu
 * einem Klumpen, in dem die Beschriftungen übereinanderlagen. Der Grund steckt in FA2 selbst:
 * Die Anziehung summiert sich über die *Kanten*, die Abstoßung über die *Knoten*. Wer die
 * Kanten verdreifacht, ohne die Abstoßung anzuheben, zieht denselben Graphen dreifach
 * zusammen. Der Faktor unten gleicht das aus, damit dieselbe Einstellung für einen dünn und
 * einen dicht vernetzten Bestand dasselbe Bild ergibt.
 */
export function fa2Einstellungen(
  physik: PhysikWerte,
  anzahl: number,
  kanten = 0,
): Record<string, number | boolean> {
  const laenge = physik.kantenlaenge / PHYSIK_VORGABE.kantenlaenge;
  // Mittlerer Grad, gemessen am Ausschnitt. Bezugsgröße ist 2 — ein Baum, also die dünnste
  // Vernetzung, bei der ein Graph noch zusammenhängt.
  const grad = anzahl > 0 ? (2 * kanten) / anzahl : 0;
  const dichte = Math.min(6, Math.max(1, grad / 2));
  return {
    barnesHutOptimize: anzahl > BARNES_HUT_AB,
    // **Der Abstandsparameter, der lange fehlte.** Im Normalmodus wächst die Anziehung einer
    // Kante linear mit der Distanz — je weiter zwei verbundene Zentren auseinanderstehen,
    // desto härter zieht es sie zurück, und ein dicht verwobenes Zentrum fällt deshalb immer
    // wieder zu einem Ballen zusammen, egal wie hoch die Abstoßung steht. LinLog lässt die
    // Anziehung nur logarithmisch wachsen: Innerhalb einer Gruppe (kurze Wege) ändert sich
    // wenig, zwischen den Gruppen (lange Wege) verliert der Zug seine Kraft — die Gruppen
    // werden kompakt und die Räume dazwischen weit. Es ist der Modus, den Noack für genau
    // diese Aufgabe entworfen hat: Cluster als Bild sichtbar machen.
    linLogMode: true,
    // LinLog rechnet in einer anderen Größenordnung — dieselbe Abstoßung, die linear ein
    // ausgewogenes Bild gibt, sprengte logarithmisch alles auseinander. Der Teiler bringt die
    // Regler in denselben Wertebereich zurück; ihre Bedeutung bleibt.
    // Deutlich schwächer als früher, weil die Kollision unten die Untergrenze übernommen hat:
    // Die allgemeine Abstoßung muss keine Überlappung mehr verhindern, sondern nur noch den
    // Raum zwischen den Gruppen offen halten. Steht sie zu hoch, überstimmt sie zusammen mit
    // der Kollision jede Anziehung, und das Bild wird ein gleichmäßiges Gitter aus Punkten —
    // im Browser gesehen, nicht überlegt.
    scalingRatio: Math.max(0.3, ((physik.abstossung / 1.2) * laenge * laenge * dichte) / 2),
    gravity: 0.003 + Math.max(0, Math.min(0.9, physik.schwerkraft)) * 0.03,
    strongGravityMode: false,
    // Größere Graphen werden stärker gedämpft, sonst zappelt das Bild statt zu konvergieren.
    slowDown: 2 + anzahl / 800,
    // **Der Mindestabstand — und die eigentliche Antwort auf den Kern-Klumpen.**
    //
    // `adjustSizes` schaltet FA2 auf Anticollision um: Es rechnet den Abstand *minus beide
    // Radien*, und bei Überlappung wirkt ein hundertfacher Stoß. Anders als die gewöhnliche
    // Abstoßung ist das eine harte Untergrenze und keine Kraft, die man wegziehen kann.
    //
    // Warum das nötig ist, steht in FA2s Massenformel: Die Masse eines Knotens ist
    // `1 + Summe seiner Kantengewichte`, und die Abstoßung geht mit dem *Produkt* zweier
    // Massen. Ein Cluster mit zwanzig Mitgliedern kommt so auf Masse 160, ein Dokument ohne
    // jede Kante auf 1 — zwischen zwei solchen Dokumenten wirkt ein Zehntausendstel der Kraft,
    // die zwischen zwei Ankern wirkt. Genau deshalb sammelten sich die unverbundenen Dokumente
    // im Kern: Sie stoßen einander praktisch nicht ab, und die Schwerkraft zieht sie hinein.
    // Mit Anticollision hält jeder Knoten seinen Radius unabhängig von seiner Masse — die
    // Unverbundenen verteilen sich, und die Schwerkraft von außen macht daraus einen Ring.
    //
    // Die Einschränkung auf kleine Graphen fiel damit weg. Sie stammte aus dem linearen Modus
    // mit großen Knoten, wo die Kollision das Bild zu einem gleichmäßigen Teppich planierte;
    // unter LinLog, mit Radien in Layout-Koordinaten, ist das Gegenteil der Fall.
    adjustSizes: false,
    // "Dissuade Hubs" (Gephi) bleibt **aus**, und das ist eine Lehre aus einem sichtbaren
    // Fehlschlag: Es teilt die Anziehung jeder Kante durch den Grad ihres Ausgangsknotens —
    // und die Ausgangsknoten der `member`-Kanten sind genau die Cluster. Ein Anker mit zwanzig
    // Mitgliedern band jedes nur noch mit einem Zwanzigstel; im Bild standen die Anker als
    // Ring außen, während ihre Mitglieder sich ohne Halt in der Mitte ballten — die Sterne
    // waren invertiert. Die Aufgabe, Naben zu entmachten, übernehmen jetzt die Kantengewichte
    // und LinLog; dieser Schalter würde dieselbe Arbeit ein zweites Mal tun und dabei die
    // Struktur zerlegen, die er zeigen soll.
    outboundAttractionDistribution: false,
    // Die Kantengewichte zählen — siehe `kantenGewicht`. Ohne diese Zeile zieht jede Kante
    // gleich stark, und der Graph kann nicht zeigen, was eng zusammengehört und was nur
    // entfernt verwandt ist.
    edgeWeightInfluence: 1,
  };
}

/** Der Typname des Strukturknotens aus §7 — kein Taxonomie-Eintrag, sondern ein Baustein. */
export const TYP_CLUSTER = "Cluster";

/**
 * Wie fest ein Mitglied an seiner Gruppe hängt, verglichen mit einer semantischen Kante.
 *
 * Deutlich mehr als die stärkste semantische Kante, und zwar mit Grund: Ein Mitglied hängt
 * typischerweise mit mehreren Verweisen im allgemeinen Geflecht, aber nur mit einer Kante an
 * seiner Gruppe. Soll der Stern die Form bestimmen, muss die eine Kante das Bündel der vielen
 * schlagen.
 *
 * **Alle Gewichte hier bleiben unter eins, und das ist keine Kosmetik.** In ForceAtlas2 ist ein
 * Kantengewicht nicht nur Zugkraft: Die Masse eines Knotens ist `1 + Summe seiner
 * Kantengewichte`, und die Abstoßung geht mit dem *Produkt* zweier Massen. Hohe Gewichte machen
 * verbundene Knoten also schwer und lassen unverbundene federleicht zurück — bei einem
 * Mitgliedsgewicht von 8 kam ein Cluster mit zwanzig Mitgliedern auf Masse 160, ein Dokument
 * ohne Kante auf 1, und zwischen zwei solchen Dokumenten wirkte ein Fünfundzwanzigtausendstel
 * der Kraft, die zwischen zwei Ankern wirkte. Sie konnten sich gar nicht auseinanderschieben
 * und sammelten sich als Klumpen in der Mitte. Genau der Kern, der zuletzt übrig blieb.
 *
 * Die *Verhältnisse* untereinander tragen die Aussage, nicht die absolute Höhe. Klein gehalten
 * bleiben die Massen beieinander, und die Abstoßung wirkt zwischen allen Knoten ähnlich stark.
 */
const BINDUNG_MITGLIED = 0.9;

/**
 * Wie stark zwei Cluster einander über eine `related`-Kante halten.
 *
 * Deutlich schwächer als eine Mitgliedschaft, und das ist der Kern des Bildes: Die Cluster
 * sind untereinander *auch* verbunden — jedes mit seinen drei nächsten —, und zögen diese
 * Kanten mit voller Ähnlichkeit (im Bestand gemessen: 0,87 im Mittel), pressten sie sämtliche
 * Sterne zu einem einzigen Ballen zusammen. Als lockere Feder gedacht ordnen sie stattdessen
 * die Nachbarschaft: Verwandte Themen liegen nebeneinander, jeder Stern bleibt für sich
 * lesbar.
 */
const BINDUNG_VERWANDT = 0.04;

/**
 * Wie stark eine semantische Kante *innerhalb* einer Gruppe gedämpft wird.
 *
 * Ohne diese Dämpfung fiel jede Gruppe zu einem Punkt zusammen, und das ist kein Zufall,
 * sondern die Folge davon, wie die Gruppen entstehen: Ein Cluster fasst zusammen, was sich
 * ähnlich ist — und genau deshalb haben seine Mitglieder auch untereinander Kanten. Zwanzig
 * Mitglieder, die sich gegenseitig anziehen, ziehen den Ring, auf dem sie um ihr Cluster
 * stehen sollten, in die Mitte.
 *
 * Die Zugehörigkeit ist an dieser Stelle schon gesagt — die `member`-Kante sagt sie. Eine
 * zweite Kante zwischen zwei Knoten derselben Gruppe wiederholt sie nur und trägt nichts bei.
 * Ihre Aufgabe hat eine semantische Kante dort, wo sie *zwei* Gruppen verbindet: Dann summieren
 * sich mehrere solcher Kanten und rücken die Gruppen zusammen.
 */
const DAEMPFUNG_INNERHALB = 0.15;

/**
 * Wie stark eine Kante zieht — nach ihrer Rolle und nach der Ähnlichkeit, die in ihr steht.
 *
 * Vorher zog jede Kante gleich stark, und das ist der Grund, warum sich alles auf wenige Knoten
 * ballte: Ein Graph, in dem eine Cluster-Zugehörigkeit genauso wiegt wie ein beiläufiger
 * Querverweis, hat keine Gruppen — er hat einen Mittelpunkt.
 *
 * Zwei Kräfte, die verschiedene Dinge tun:
 *
 * - **`member` bindet fest.** Ein Cluster ist die kleine Gruppe, um die seine Mitglieder
 *   kreisen; das soll die stärkste Kraft im Bild sein. Damit wird der Cluster-Knoten zum
 *   Schwerpunkt seiner Umgebung, und die Cluster stoßen sich untereinander ab, statt dass
 *   jedes Mitglied einzeln mit jedem fremden Knoten verhandelt.
 * - **Semantische Kanten ziehen nach Ähnlichkeit.** Sie sind schwächer, und deshalb bewegen sie
 *   nicht ein einzelnes Mitglied aus seiner Gruppe heraus. Wo aber *mehrere* Knoten zwei
 *   Gruppen verbinden, summieren sie sich, und die beiden Gruppen rücken zusammen. Genau das
 *   soll man sehen: Nähe im Bild heißt dann inhaltliche Nähe.
 *
 * Die vierte Potenz ist kein Zierrat. Die Ähnlichkeiten im Bestand liegen dicht beieinander —
 * gemessen: `references` im Mittel 0,71, `related` 0,87 —, und linear übertragen wäre der
 * Unterschied zwischen einer engen und einer entfernten Verwandtschaft nicht zu sehen. Die
 * Potenz spreizt denselben Bereich auf ungefähr das Dreizehnfache.
 *
 * `gleicheGruppe` sagt, ob beide Enden im selben Cluster hängen — dann wird gedämpft, siehe
 * `DAEMPFUNG_INNERHALB`.
 */
export function kantenGewicht(kante: Edge, gleicheGruppe = false): number {
  const naehe = Math.max(0, Math.min(1, kante.weight ?? kante.confidence ?? 0.5));
  if (kante.kind === "member") {
    return BINDUNG_MITGLIED * (0.4 + 0.6 * naehe);
  }
  if (kante.kind === "related") {
    return BINDUNG_VERWANDT * (0.4 + 0.6 * naehe);
  }
  // Der Sockel hält auch die schwächste Kante sichtbar wirksam: Sie soll wenig ziehen, aber
  // nicht nichts — sonst zerfiele der Graph in Gruppen ohne jeden Bezug zueinander.
  const zug = 0.01 + naehe ** 4 * 0.12;
  return gleicheGruppe ? zug * DAEMPFUNG_INNERHALB : zug;
}

/**
 * Wer gehört zu welcher Gruppe — abgelesen an den `member`-Kanten des Ausschnitts.
 *
 * Nur das, was im Bild ist, zählt: Ein Cluster, das der Filter weggeschnitten hat, gruppiert
 * hier nichts. Ein Knoten in mehreren Gruppen bekommt die erste; für die Dämpfung genügt das,
 * weil eine Kante schon dann nicht gedämpft wird, wenn die beiden Enden *irgendwie*
 * auseinanderfallen.
 */
export function gruppenZuordnung(edges: Iterable<Edge>): Map<string, string> {
  const zuordnung = new Map<string, string>();
  for (const kante of edges) {
    if (kante.kind === "member" && !zuordnung.has(kante.to_id)) {
      zuordnung.set(kante.to_id, kante.from_id);
    }
  }
  return zuordnung;
}

function knotenAttribute(
  knoten: CanvasNode,
  typen: readonly string[],
): Record<string, unknown> {
  const grabstein = knoten.status === "tombstone";
  const anker = knoten.type === TYP_CLUSTER;
  const farbe = farbeFuerTyp(knoten.type, typen);
  return {
    label: knoten.title ?? knoten.id,
    store: knoten.store,
    typ: knoten.type,
    status: knoten.status,
    gewicht: knoten.gewicht,
    // Store über die Form (§17.2): der eigene Bestand als Raute, der geteilte als Kreis.
    type: knoten.store === "personal" ? "raute" : "circle",
    // Ein Grabstein bleibt sichtbar, aber blass — er ist Geschichte, kein Inhalt (§7.6).
    color: grabstein ? mitDeckkraft(farbe, 0.35) : farbe,
    // **`size` ist hier der Layout-Radius, nicht die Zeichengröße.** Die Trennung ist nötig,
    // weil dasselbe Attribut in zwei verschiedenen Einheiten gebraucht wird: ForceAtlas2 liest
    // `size` als Kollisionsradius in *Layout*-Koordinaten (`adjustSizes` rechnet Abstand minus
    // beide Radien), sigma zeichnet es als Durchmesser in *Pixeln*. Ein Wert, der als Pixel
    // stimmt, ist als Layout-Radius bedeutungslos — bei Distanzen in Hunderten verhindert ein
    // Radius von drei keine einzige Überlappung. Die Zeichenfläche rechnet über ihren Reducer
    // auf `zeichenGroesse` zurück.
    size: anker ? 9 : 4,
    // Was sigma tatsächlich malt. Klein, und das ist Arithmetik statt Geschmack: Über
    // zweitausend Knoten teilen sich rund 900 x 650 Pixel — gut fünfzehn Pixel je Knoten.
    // Alles darüber überlappt zwangsläufig, wie gut die Physik auch sortiert; die Vorbilder
    // dieser Ansicht zeichnen ihre Punkte mit zwei bis fünf Pixeln. Wer einen einzelnen Knoten
    // braucht, zoomt — dann wachsen die Abstände, die Pixel nicht.
    //
    // Ein Cluster ist der Anker seines Sterns und bleibt in beiden Maßen der größere: Das Auge
    // soll die Struktur zuerst an den Ankern ablesen, die Mitglieder hängen daran.
    zeichenGroesse: anker
      ? Math.round(6 + Math.sqrt(Math.max(0, Math.min(1, knoten.gewicht))) * 3)
      : Math.round(2 + Math.sqrt(Math.max(0, Math.min(1, knoten.gewicht))) * 3),
    tombstone: grabstein,
  };
}

/** Deckkraft als Teil der Farbe — WebGL kennt keine gestrichelte Linie (siehe Kopfkommentar). */
function mitDeckkraft(farbe: string, deckkraft: number): string {
  const wert = Math.round(Math.max(0, Math.min(1, deckkraft)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${farbe}${wert}`;
}

function kantenAttribute(kante: Edge, gruppe: Map<string, string>): Record<string, unknown> {
  const wartet = istUnbestaetigt(kante);
  const von = gruppe.get(kante.from_id);
  const gleicheGruppe = von !== undefined && von === gruppe.get(kante.to_id);
  return {
    kind: kante.kind,
    unbestaetigt: wartet,
    // Kantenart über die Stärke: `member` trägt die Struktur und ist kräftig, alles
    // Semantische ist fein. Provenienz über die Farbe; Unbestätigtes voll deckend.
    //
    // Die Stärken sind gegenüber der ersten Fassung halbiert. Sie waren für ein Dutzend Kanten
    // gewählt und wurden bei einigen tausend zum Problem: Nebeneinanderlaufende Kanten
    // verschmolzen zu Bändern, und das Bild zeigte Fläche statt Struktur. Eine Kante muss
    // sichtbar sein, wenn man sie sucht — sie muss nicht auffallen, wenn man die Gruppen sucht.
    size: (kante.kind === "member" ? 1.2 : 0.6) + (wartet ? 0.3 : 0),
    // Bestätigtes tritt weit zurück: Bei viertausend Kanten ist jede einzelne Rauschen und
    // erst ihre Summe das Bild — die Vorbilder dieser Ansicht zeichnen Kanten hauchdünn.
    color: mitDeckkraft(farbeFuerKante(kante.generated_by), wartet ? 0.9 : 0.16),
    // Für die Simulation, nicht für das Auge: ForceAtlas2 liest `weight` als Zugkraft.
    weight: kantenGewicht(kante, gleicheGruppe),
  };
}

/**
 * Gleicht die Daten der Ansicht in das graphology-Modell ab — unter Erhalt der Positionen.
 *
 * Das ist die Regel "die Instanz überlebt eine Datenänderung" aus der Cytoscape-Fassung, nur
 * dass sie hier natürlicher liegt: Das Datenmodell ist von der Zeichnung getrennt. Vorhandene
 * Knoten behalten x/y; neue kommen an der Stelle eines schon platzierten Nachbarn zur Welt
 * und schieben sich von dort ins Bild — nicht am Nullpunkt, wo sie erst einen weiten Weg
 * durchs Bild fliegen müssten.
 */
export function spiegeln(
  graph: Graph,
  nodes: CanvasNode[],
  edges: Edge[],
  typen: readonly string[],
): { neu: number } {
  const sollKnoten = new Set(nodes.map((knoten) => knoten.id));
  const sollKanten = new Map(
    edges
      .filter((kante) => sollKnoten.has(kante.from_id) && sollKnoten.has(kante.to_id))
      .map((kante) => [kante.id, kante]),
  );

  for (const id of graph.nodes()) {
    if (!sollKnoten.has(id)) {
      graph.dropNode(id);
    }
  }
  for (const id of graph.edges()) {
    if (!sollKanten.has(id)) {
      graph.dropEdge(id);
    }
  }

  // Neue Knoten kommen mit einer vorläufigen Position zur Welt — sigma verlangt x/y schon
  // beim Anlegen. Nachgerückt (neben einen Nachbarn) wird, sobald die Kanten da sind, damit
  // auch ein Nachbar zählt, der im selben Abgleich gekommen ist.
  const spanne = Math.sqrt(Math.max(1, graph.order + nodes.length)) * 30;
  const neue: string[] = [];
  for (const knoten of nodes) {
    const attribute = knotenAttribute(knoten, typen);
    if (graph.hasNode(knoten.id)) {
      graph.mergeNodeAttributes(knoten.id, attribute);
    } else {
      graph.addNode(knoten.id, { ...attribute, x: zufall(spanne), y: zufall(spanne) });
      neue.push(knoten.id);
    }
  }

  // Erst die Zugehörigkeiten, dann die Kanten: Das Gewicht einer Kante hängt davon ab, ob sie
  // innerhalb einer Gruppe verläuft oder zwischen zweien.
  const gruppe = gruppenZuordnung(sollKanten.values());
  for (const [id, kante] of sollKanten) {
    if (graph.hasEdge(id)) {
      graph.mergeEdgeAttributes(id, kantenAttribute(kante, gruppe));
    } else {
      graph.addEdgeWithKey(id, kante.from_id, kante.to_id, kantenAttribute(kante, gruppe));
    }
  }

  const frisch = new Set(neue);
  for (const id of neue) {
    const nachbar = graph.neighbors(id).find((kandidat) => !frisch.has(kandidat));
    if (nachbar !== undefined) {
      graph.setNodeAttribute(id, "x", (graph.getNodeAttribute(nachbar, "x") as number) + zufall(40));
      graph.setNodeAttribute(id, "y", (graph.getNodeAttribute(nachbar, "y") as number) + zufall(40));
    }
  }
  return { neu: neue.length };
}

function zufall(spanne: number): number {
  return (Math.random() - 0.5) * 2 * spanne;
}

export type Positionen = Map<string, { x: number; y: number }>;

/**
 * Die **Sternenkarte**: Cluster als Zentren, ihre Mitglieder als Ring darum, alles Übrige
 * außen — und die Zentren untereinander kraftbasiert angeordnet.
 *
 * Dieses Layout gibt es, weil ForceAtlas2 das gewünschte Bild strukturell nicht zeichnen kann,
 * und das ist keine Frage der Einstellung. In FA2 ist die Masse eines Knotens
 * `1 + Summe seiner Kantengewichte`, und die Abstoßung geht mit dem *Produkt* zweier Massen.
 * Ein Cluster mit zwanzig Mitgliedern ist damit zwangsläufig schwer und wird nach außen
 * gedrängt; ein Dokument ohne Kante hat Masse 1, spürt fast keine Abstoßung und fällt nach
 * innen. Das Ergebnis ist der Stern von innen nach außen gekehrt: Anker außen als Schale,
 * Mitglieder als Klumpen in der Mitte. Genau das war im Browser zu sehen, über mehrere
 * Parametrierungen hinweg — mit und ohne LinLog, mit und ohne Anticollision, mit Gewichten
 * über und unter eins.
 *
 * Die Zugehörigkeit ist aber bereits bekannt: Sie steht in den `member`-Kanten und muss nicht
 * aus Kräften erraten werden. Wer sie kennt, kann sie direkt zeichnen. Drei Schritte:
 *
 * 1. **Die Zentren.** Nur die Cluster, mit ihren `related`-Kanten und den Verweisen zwischen
 *    ihren Mitgliedern als Brücken, kraftbasiert angeordnet. Das sind einige hundert Knoten
 *    statt einiger tausend — dort konvergiert FA2 sauber, und verwandte Themen kommen
 *    nebeneinander zu liegen. Der Regler „Abstoßung" wirkt hier.
 * 2. **Die Ringe.** Jedes Mitglied auf einen Kreis um sein Zentrum. Der Radius wächst mit der
 *    Wurzel der Mitgliederzahl, damit auch ein großer Stern seine Nachbarn nicht überdeckt;
 *    die Reihenfolge auf dem Ring folgt der ID und ist damit über Neuzeichnungen hinweg stabil.
 *
 *    **Für die Lage eines Dokuments zählt allein seine Kante zum Cluster.** Verweise zwischen
 *    Dokumenten bleiben hier ohne Wirkung, und das ist Absicht: Ein Verweis ist eine Aussage
 *    über Inhalte, keine über Orte. Er war der Grund, warum jeder Ring wieder zusammenfiel —
 *    Mitglieder eines Themas verweisen naturgemäß aufeinander, und ein paar hundert solcher
 *    Züge sind stärker als die eine Kante, die die Zugehörigkeit trägt. Sichtbar bleiben die
 *    Verweise als Linien; auf die Anordnung wirken sie nur noch aggregiert, eine Ebene höher.
 * 3. **Der äußere Ring.** Was zu keiner Gruppe gehört, steht außerhalb von allem — sichtbar
 *    als das, was es ist: unverbundener Bestand, der auf Anbindung wartet (§15.1).
 */
export function sterne(graph: Graph, physik: PhysikWerte, fa2: Fa2Assign): Positionen {
  const zentren: string[] = [];
  const mitglieder = new Map<string, string[]>();
  const heimat = new Map<string, string>();

  for (const id of graph.nodes()) {
    if (graph.getNodeAttribute(id, "typ") === TYP_CLUSTER) {
      zentren.push(id);
      mitglieder.set(id, []);
    }
  }
  for (const kante of graph.edges()) {
    if (graph.getEdgeAttribute(kante, "kind") !== "member") {
      continue;
    }
    const zentrum = graph.source(kante);
    const kind = graph.target(kante);
    const liste = mitglieder.get(zentrum);
    if (liste !== undefined && !heimat.has(kind)) {
      liste.push(kind);
      heimat.set(kind, zentrum);
    }
  }

  const abstand = physik.kantenlaenge;
  // Der Radius wächst mit der Wurzel: Ein Stern mit hundert Mitgliedern wird größer als einer
  // mit zehn, aber nicht zehnmal so groß — sonst verschlänge er seine Nachbarn.
  const sternRadius = (zentrum: string): number =>
    abstand * (0.45 + Math.sqrt((mitglieder.get(zentrum) ?? []).length) * 0.16);

  const lagen = zentrenLegen(graph, zentren, heimat, physik, fa2, sternRadius);
  const positionen: Positionen = new Map();

  for (const [zentrum, punkt] of lagen) {
    positionen.set(zentrum, punkt);
    const kinder = [...(mitglieder.get(zentrum) ?? [])].sort();
    const radius = sternRadius(zentrum);
    kinder.forEach((kind, platz) => {
      const winkel = (2 * Math.PI * platz) / Math.max(1, kinder.length);
      positionen.set(kind, {
        x: punkt.x + Math.cos(winkel) * radius,
        y: punkt.y + Math.sin(winkel) * radius,
      });
    });
  }

  const heimatlos = graph.nodes().filter((id) => !positionen.has(id));
  if (heimatlos.length > 0) {
    let weiteste = 0;
    for (const punkt of positionen.values()) {
      weiteste = Math.max(weiteste, Math.hypot(punkt.x, punkt.y));
    }
    // Ein **Band** aus mehreren Ringen, kein einzelner Kreis. Ein Kreis müsste bei über
    // tausend Knoten einen Umfang von tausend Abständen haben und wäre um ein Vielfaches
    // größer als alles, was er umschließt — die Sterne schrumpften darin zu einem Punkt in
    // der Mitte. Im Band bleibt die Randzone ungefähr so breit, wie die Sterne Platz brauchen.
    const proRing = Math.max(24, Math.ceil((2 * Math.PI * (weiteste + abstand * 2)) / abstand));
    heimatlos.sort();
    heimatlos.forEach((id, platz) => {
      const ring = Math.floor(platz / proRing);
      const imRing = platz % proRing;
      const radius = weiteste + abstand * (2 + ring * 0.85);
      // Jeder Ring um einen halben Platz versetzt, sonst entstehen Speichen statt Fläche.
      const winkel = (2 * Math.PI * (imRing + (ring % 2) * 0.5)) / proRing;
      positionen.set(id, { x: Math.cos(winkel) * radius, y: Math.sin(winkel) * radius });
    });
  }
  return positionen;
}

/** Die Signatur von `forceatlas2.assign` — hereingereicht, damit dieses Modul importfrei bleibt. */
export type Fa2Assign = (graph: Graph, optionen: Record<string, unknown>) => void;

/**
 * Schritt 1 der Sternenkarte: die Zentren untereinander.
 *
 * Der Hilfsgraph enthält nur die Cluster. Zwei Zentren bekommen eine Kante, wenn sie direkt
 * verwandt sind (`related`) oder wenn ein Verweis zwischen ihren Mitgliedern verläuft — solche
 * Brücken werden gezählt, und die Zahl ist das Gewicht. Damit gilt, was in der Karte sichtbar
 * werden soll: Zwei Themen rücken zusammen, wenn *viele* Dokumente sie verbinden, nicht schon
 * wegen eines einzelnen Querverweises.
 */
function zentrenLegen(
  graph: Graph,
  zentren: readonly string[],
  heimat: ReadonlyMap<string, string>,
  physik: PhysikWerte,
  fa2: Fa2Assign,
  sternRadius: (zentrum: string) => number,
): Positionen {
  const lagen: Positionen = new Map();
  if (zentren.length === 0) {
    return lagen;
  }

  const bruecken = new Map<string, number>();
  for (const kante of graph.edges()) {
    const art = graph.getEdgeAttribute(kante, "kind");
    if (art === "member") {
      continue;
    }
    const a = heimat.get(graph.source(kante)) ?? graph.source(kante);
    const b = heimat.get(graph.target(kante)) ?? graph.target(kante);
    if (a === b || !lagenFaehig(a, zentren) || !lagenFaehig(b, zentren)) {
      continue;
    }
    const schluessel = a < b ? `${a} ${b}` : `${b} ${a}`;
    bruecken.set(schluessel, (bruecken.get(schluessel) ?? 0) + (art === "related" ? 2 : 1));
  }

  // Ein eigener, kleiner Graph — der große bleibt unangetastet. graphology bringt keinen
  // Untergraphen mit, der Kanten aggregiert, und die Rechnung ist zu einfach für eine
  // Abhängigkeit mehr.
  const klein = graph.emptyCopy() as Graph;
  klein.clear();
  const spanne = Math.sqrt(zentren.length) * physik.kantenlaenge * 0.5;
  for (const id of zentren) {
    // `size` ist hier die **Sperrfläche des ganzen Sterns**, nicht die des Zentrumspunkts:
    // Mit Anticollision schieben sich damit ganze Sterne auseinander wie Scheiben auf einem
    // Tisch, statt dass ihre Ringe einander durchdringen. Genau dafür wird die Kollision auf
    // dieser Ebene gebraucht — und hier ist sie auch bezahlbar, weil es um einige hundert
    // Knoten geht und nicht um einige tausend. Ohne sie sammeln sich die Zentren ohne Brücke
    // (bei 191 Clustern gemessen: nur 144 Brückenpaare, die meisten Zentren hängen an keinem)
    // unter der Schwerkraft in der Mitte — derselbe Klumpen wie zuvor, eine Ebene höher.
    klein.addNode(id, { x: zufall(spanne), y: zufall(spanne), size: sternRadius(id) });
  }
  for (const [schluessel, anzahl] of bruecken) {
    const [a, b] = schluessel.split(" ");
    if (!klein.hasEdge(a, b)) {
      klein.addEdge(a, b, { weight: Math.min(6, anzahl) });
    }
  }

  fa2(klein, {
    iterations: 600,
    settings: {
      barnesHutOptimize: zentren.length > BARNES_HUT_AB,
      // Auf dieser Ebene sind die Massen unschädlich: Alle Knoten sind Cluster, und keiner
      // ist wegen seiner Mitgliederzahl schwerer als der andere — die Mitglieder sind hier
      // gar nicht vertreten. Genau deshalb funktioniert die Kraftrechnung hier und im großen
      // Graphen nicht.
      scalingRatio: Math.max(2, physik.abstossung * 1.6),
      gravity: 0.02 + Math.max(0, Math.min(0.9, physik.schwerkraft)) * 0.08,
      slowDown: 3,
      linLogMode: false,
      adjustSizes: true,
      outboundAttractionDistribution: false,
      edgeWeightInfluence: 1,
    },
  });

  // Die Zentren stehen jetzt in beliebigem Maßstab — auf den Kantenlängen-Regler bringen.
  let ausdehnung = 0;
  for (const id of zentren) {
    ausdehnung = Math.max(
      ausdehnung,
      Math.hypot(Number(klein.getNodeAttribute(id, "x")), Number(klein.getNodeAttribute(id, "y"))),
    );
  }
  // Die Anticollision hat die Sterne bereits in echten Abständen verteilt — der Maßstab
  // stimmt also schon und darf nur noch behutsam nachgeführt werden. Nach oben begrenzt,
  // damit ein einzelner weit abgeschlagener Trabant nicht alles andere zusammenschrumpfen
  // lässt: Der Faktor wird nie kleiner als eins, gespreizt wird höchstens.
  const soll = physik.kantenlaenge * Math.sqrt(zentren.length) * 2.4;
  const faktor = ausdehnung > 0 ? Math.max(1, soll / ausdehnung) : 1;
  for (const id of zentren) {
    lagen.set(id, {
      x: Number(klein.getNodeAttribute(id, "x")) * faktor,
      y: Number(klein.getNodeAttribute(id, "y")) * faktor,
    });
  }
  return lagen;
}

function lagenFaehig(id: string, zentren: readonly string[]): boolean {
  return zentren.includes(id);
}

/**
 * Ringe nach Gewicht (§17.2 "konzentrisch"): Was schwer wiegt, steht innen.
 *
 * In der Traversierung ist das Gewicht der Score und fällt mit der Hop-Distanz — die Ringe
 * zeigen dann die Entfernung vom Startknoten. In der Karte ist es der Grad: Die vernetzten
 * Knoten stehen im Zentrum, die losen am Rand.
 */
export function konzentrisch(graph: Graph, abstand: number): Positionen {
  const ringe = new Map<number, string[]>();
  for (const id of graph.nodes()) {
    const gewicht = Number(graph.getNodeAttribute(id, "gewicht")) || 0;
    const ring = Math.round((1 - Math.max(0, Math.min(1, gewicht))) * 4);
    const eintraege = ringe.get(ring) ?? [];
    eintraege.push(id);
    ringe.set(ring, eintraege);
  }

  const positionen: Positionen = new Map();
  for (const [ring, eintraege] of ringe) {
    // Der Umfang muss die Knoten fassen: Ein voller innerer Ring wird notfalls weiter.
    const radius =
      ring === 0 && eintraege.length === 1
        ? 0
        : Math.max((ring + 1) * abstand, (eintraege.length * abstand * 0.6) / (2 * Math.PI));
    eintraege.forEach((id, platz) => {
      const winkel = (2 * Math.PI * platz) / eintraege.length;
      positionen.set(id, { x: Math.cos(winkel) * radius, y: Math.sin(winkel) * radius });
    });
  }
  return positionen;
}

/**
 * Ebenen entlang der `member`-Kanten (§17.2 "hierarchisch") — als eigene Rechnung, weil
 * sigma kein hierarchisches Layout mitbringt (Konzept, Abschnitt 3.3).
 *
 * Wurzeln sind Knoten ohne eingehende `member`-Kante; jede Ebene liegt eine Zeile tiefer.
 * Was gar nicht an der Mitglieds-Struktur hängt, steht gesammelt unter dem Baum statt
 * irgendwo dazwischen.
 */
export function hierarchisch(graph: Graph, abstand: number): Positionen {
  const eltern = new Map<string, string[]>();
  for (const kante of graph.edges()) {
    if (graph.getEdgeAttribute(kante, "kind") === "member") {
      const von = graph.source(kante);
      const nach = graph.target(kante);
      const kinder = eltern.get(von) ?? [];
      kinder.push(nach);
      eltern.set(von, kinder);
    }
  }
  const hatEingang = new Set([...eltern.values()].flat());

  const ebene = new Map<string, number>();
  const warteschlange: string[] = [];
  for (const id of graph.nodes()) {
    if (eltern.has(id) && !hatEingang.has(id)) {
      ebene.set(id, 0);
      warteschlange.push(id);
    }
  }
  while (warteschlange.length > 0) {
    const id = warteschlange.shift() as string;
    for (const kind of eltern.get(id) ?? []) {
      if (!ebene.has(kind)) {
        ebene.set(kind, (ebene.get(id) ?? 0) + 1);
        warteschlange.push(kind);
      }
    }
  }

  const tiefste = Math.max(0, ...ebene.values());
  const zeilen = new Map<number, string[]>();
  for (const id of graph.nodes()) {
    const zeile = ebene.get(id) ?? tiefste + 1;
    const eintraege = zeilen.get(zeile) ?? [];
    eintraege.push(id);
    zeilen.set(zeile, eintraege);
  }

  const positionen: Positionen = new Map();
  for (const [zeile, eintraege] of zeilen) {
    eintraege.sort();
    eintraege.forEach((id, platz) => {
      positionen.set(id, {
        x: (platz - (eintraege.length - 1) / 2) * abstand,
        y: zeile * abstand * 1.4,
      });
    });
  }
  return positionen;
}
