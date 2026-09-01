# Konzept: Die Graphzentrale

Die UI ist das einzige Sichtfenster auf die App. Alles andere — Adapter, Läufe, Ranking,
Provenienz — nimmt ein Mensch nur durch sie wahr. Dieses Dokument legt fest, wie die Oberfläche
neu aufgebaut wird: für wen, in welcher Struktur, mit welcher Technik und in welcher Reihenfolge.

Es ersetzt nicht die Spezifikation, sondern konkretisiert deren §17; die Spezifikation wird
parallel auf diesen Stand gebracht. Paragraphenzeichen verweisen wie überall auf
`docs/architektur-spec-wissensgraph.md`.

---

## 1. Befund: was heute nicht reicht

Die Funktionen sind inhaltlich vorhanden — sechs Ansichten decken §17.2 ab, die Fachregeln kommen
aus `/config/effective`, jede Kuration ist journaliert und rücknehmbar. Was fehlt, ist die
Qualität des Fensters selbst. Konkret, nach Gewicht geordnet:

1. **Der Graph skaliert nicht weit genug.** Gemessen (STATUS.md, Stufe-13-Nachtrag):
   `cytoscape-cola` im Dauerbetrieb liefert 18/7/2/1 fps bei 300/600/1200/2000 Knoten; ein
   Mausrad-Zoom bei 2000 Knoten dauert 7,9 s. Der heutige Zwei-Motoren-Kompromiss (cola bis 400,
   `fcose` darüber) rettet die Bildrate auf ~144 fps bei 2000 Knoten — aber nur, indem er die
   Physik oberhalb von 400 Knoten **abschaltet**. Bei 5.000 Knoten ist auch das statische Bild
   zäh. Das Produktziel sind mehrere hundert bis einige tausend Knoten *mit* lebendigem Layout;
   das gibt Canvas-Rendering mit Layout im UI-Thread grundsätzlich nicht her. Das ist kein
   Tuning-Problem, sondern eine Architekturgrenze der Bibliothek.
2. **Das Gerüst ist starr.** Seitenpanels lassen sich weder einklappen noch in der Breite ziehen;
   auf schmalen Fenstern quetschen sie den Graphen, auf breiten verschenken sie Platz. Es gibt
   keinen Ort, der Detail und Übersicht gleichzeitig erlaubt.
3. **Die Bedienelemente sind Rohware.** Felder haben unvorteilhafte Größen, Schaltflächen
   reagieren teils nicht wie erwartet, Formulare sind funktional statt geführt. Die UI sieht aus
   wie das, was sie war: die schnellste Erfüllung der Spezifikation, nicht ein Produkt.
4. **Eine Navigation für alle.** Sechs gleichrangige Reiter behandeln den Leser einer Notiz, den
   Kurator von 300 Vorschlägen und den Betreiber des Syncs identisch. Jeder sieht alles, niemand
   sieht *seins* zuerst.

Was ausdrücklich **trägt** und übernommen wird: die API (inkl. `/graph/map` mit Cursor und
Deckel), der URL-Zustand (teilbare Ansichten), die visuelle Kodierung aus `ui/src/theme.ts`
(Taxonomie-Farben, das knappe Rot, `istModellvorschlag`), die Interaktionsregeln §17.3 und die
Schreibrechtematrix §17.4. Der Neubau betrifft Darstellung und Struktur, nicht die Fachlogik —
die UI hat keine, und das bleibt so.

---

## 2. Drei Anwendergruppen, drei Arbeitsbereiche

Die Navigation ordnet sich nicht mehr nach Datenarten (Dokumente, Cluster, …), sondern nach den
Menschen, die vor dem Bildschirm sitzen:

| Gruppe | Aufgabe | Typische Sitzung |
|---|---|---|
| **Anwender** | Inhalte zu den eigenen Themen finden, lesen, verknüpfen; eigene Notizen pflegen; im Vorbeigehen kuratieren | „Was haben wir zu X?" → Suche oder Karte → Traversierung → lesen → ggf. eine Kante bestätigen |
| **Analysten** | den Graphen vernetzen: Cluster ordnen, Vorschläge abarbeiten, die Automatisierung (Clustering, Relationen, Waisen-Anbindung) parametrieren und ihre Qualität beurteilen | Kurationsstapel abarbeiten → auffällige Cluster nacharbeiten → Parameter justieren → Lauf mit `--dry-run` prüfen → scharf ausführen |
| **Admins** | Quellen und Sync verwalten, Läufe sehen und steuern, Modelle und Kosten, Diagnose — alles, was heute die CLI kann | Health prüfen → hängenden Lauf ansehen → Sync anstoßen → Kosten je Lauf kontrollieren |

Daraus werden **drei Arbeitsbereiche** in einer linken Navigationsleiste; die sechs heutigen
Ansichten gehen darin auf, keine Funktion entfällt:

```
ERKUNDEN                 ANALYSIEREN                VERWALTEN
├─ Graph (Karte /        ├─ Kuration (Warteschlange) ├─ Quellen & Sync
│  Traversierung)        ├─ Cluster-Arbeitsplatz     ├─ Läufe (live + Historie)
├─ Suche & Dokumente     ├─ Automatisierung          ├─ Modelle & Kosten
└─ Persönlicher Bereich  │  (Parameter + Probelauf)  └─ Konfiguration & Diagnose
                         └─ Qualität (Waisen,
                            Unbestätigtes, Abdeckung)
```

Zwei Festlegungen dazu:

* **Arbeitsbereiche sind Ordnung, keine Rechte.** Der POC kennt ein Token und keine Rollen
  (§20.3). Jeder sieht alle drei Bereiche; der zuletzt benutzte wird gemerkt. Wenn `oidc` kommt,
  liefert das Token die Rolle, und die Bereiche werden zu echten Berechtigungsgrenzen — die
  Struktur ist dann schon da. Rechte *erfinden* darf die UI bis dahin nicht: Was §17.4 erlaubt,
  bleibt überall erlaubt.
* **Der Graph gehört allen.** Erkunden ist sein Zuhause, aber Analysieren und Verwalten öffnen
  dieselbe Graphkomponente in ihrem Kontext (ein Cluster aus dem Arbeitsplatz heraus, das
  Ergebnis eines Laufs). Es gibt **eine** Graphkomponente, keine drei.

### 2.1 Neu gegenüber heute: Automatisierung und Qualität

Der Analyst hat heute keinen Ort. Kuration und Cluster-Arbeitsplatz existieren, aber das
*Parametrieren* — was die CLI mit `wg link-orphans --loose-threshold 1 --proximity-auto-commit
0.85 …` kann — gibt es in der UI nicht, obwohl die API die Parameter längst annimmt
(`POST /runs/link-orphans` trägt alle Felder aus §15.4).

**Automatisierung**: je Laufart (Clustering, Relationen, Waisen-Anbindung, Embeddings) ein
geführtes Formular. Die Felder kommen aus der aufgelösten Konfiguration und zeigen deren Werte
als Vorbelegung; Abweichungen sind sichtbar markiert. Jeder Lauf startet zuerst als
**Probelauf** (`dry_run: true`) mit einer Ergebnisvorschau — *n* Kanten würden entstehen, *m*
Waisen blieben übrig — und wird erst danach mit denselben Parametern scharf ausgeführt. Das
Prinzip ist von der CLI übernommen: kein schreibender Lauf ohne `--dry-run`-Angebot (§19).

**Qualität** beantwortet, ob die Automatisierung gut arbeitet: Anteil loser Knoten je Scope,
Alter und Größe der Kurationswarteschlange, Bestätigungs-/Verwerfungsquote der Modellvorschläge,
Cluster ohne kuratierten Titel. Alles davon ist aus vorhandenen Endpunkten ableitbar
(`/stats`, `/curation/queue`, `/graph/map` mit Facetten); was fehlt, ist die Verdichtung an
einem Ort.

### 2.2 CLI-Parität für Admins

Abgleich gegen §19, damit „alle Admin-Tasks der CLI" nicht Behauptung bleibt:

| CLI | UI heute | UI künftig |
|---|---|---|
| `wg sources list` / Health | Betriebsansicht | Quellen & Sync, mit Health-Verlauf |
| `wg sync --source … [--full] [--dry-run]` | Lauf starten (ohne Optionen) | vollständig, inkl. Probelauf |
| `wg embed / cluster / relations / link-orphans` mit Parametern | nur Standardparameter | Automatisierung (Analysieren) |
| `wg models describe / usage` | Modellnutzung | Modelle & Kosten, je Task und Lauf |
| `wg config show` | aufgelöste Konfiguration | bleibt; Secrets maskiert wie in der API |
| `wg doctor` | — | Diagnose-Karte: Verbindungen, Provider, Adapter, Policies mit Ampel |
| `wg migrate` | — | **bewusst nicht in der UI** — Schemamigration gehört an die Konsole, nicht hinter einen Button |
| `wg export` | — | Ausbaustufe; braucht zuerst einen API-Endpunkt |

`wg doctor` braucht einen neuen Endpunkt (`GET /api/v1/doctor` o. ä.); der Service existiert,
es fehlt nur die HTTP-Hülle. Das ist die einzige nennenswerte API-Lücke des Admin-Bereichs.

---

## 3. Graph-Rendering: die Bibliotheksentscheidung

### 3.1 Warum Cytoscape.js ersetzt wird

Cytoscape zeichnet auf Canvas 2D und rechnet Layouts im UI-Thread. Beides zusammen setzt die
Grenze, die wir gemessen haben: Rendering wird ab wenigen tausend Elementen teuer, und jede
Layout-Iteration konkurriert mit der Bedienung um denselben Thread — deshalb fror `animate:
false` den Tab ein, deshalb ruckelt der Dauerbetrieb ab ein paar hundert Knoten. Der
Zwei-Motoren-Kompromiss war die beste Antwort *innerhalb* dieser Bibliothek; das Produktziel
liegt außerhalb.

### 3.2 Kandidaten

| | Rendering | Layout | Skalierung (interaktiv) | Risiko |
|---|---|---|---|---|
| **sigma.js v3 + graphology** | WebGL | ForceAtlas2 aus graphology, **im Web Worker** | zehntausende Knoten | gering: MIT, TypeScript, aktiv gepflegt, eigenes Datenmodell mit Algorithmen (Communities, Grad, Filter) |
| react-force-graph | WebGL (three.js) | d3-force im UI-Thread | ~5k, aber Simulation blockiert wieder den Thread | genau das Problem, das wir loswerden wollen |
| G6 (AntV) | Canvas/WebGL | eingebaut | hoch | schwergewichtig, eigenes Styling-Universum, Doku-Hürde |
| Cosmograph / cosmos | GPU (Layout **und** Rendering) | GPU-Force | hunderttausende | Lizenz der aktuellen Version nicht schlicht MIT — für ein Unternehmensprodukt zu klären, bevor man sich bindet; Layout kaum steuerbar |

### 3.3 Entscheidung

**sigma.js v3 mit graphology.** Es löst beide gemessenen Engpässe an der Wurzel: WebGL nimmt dem
Zeichnen die Elementzahl ab, und ForceAtlas2 im Web Worker nimmt dem UI-Thread die Simulation ab
— lebendige Physik bei 5.000 Knoten, während Zoom, Auswahl und Panels flüssig bleiben. Der
heutige Motorwechsel bei 400 Knoten **entfällt ersatzlos**; es gibt wieder einen Motor, und er
läuft auf jeder Größe. graphology bringt zusätzlich das, was wir bisher von Hand rechnen
(Gradzahlen, Filter auf dem Client, künftig Community-Erkennung als Layout-Hilfe).

Was die Entscheidung kostet, offen benannt:

* **Kein Compound/Nesting, kein eingebautes hierarchisches Layout.** Cluster als visuelle
  Container gibt es in sigma nicht geschenkt. Unser Modell braucht das nicht zwingend —
  Zugehörigkeit ist heute schon Kante (`member`), nicht Verschachtelung. Die hierarchische
  Sicht entlang `member` (§17.2) wird als eigene Layout-Berechnung in graphology umgesetzt
  (Ebenen nach Hop-Distanz), nicht als Bibliotheksfunktion.
* **Gestrichelte Kanten sind in WebGL kein Einzeiler.** Die Kodierung „unbestätigt =
  gestrichelt" (§17.2) braucht ein eigenes Kanten-Programm oder weicht auf eine gleichwertige
  Kodierung aus (Transparenz + Farbe führen wir bereits). Festlegung: erst das vorhandene
  Kantenprogramm-Ökosystem prüfen, nur bei Fehlschlag die Kodierung ändern — und dann in
  `theme.ts` **und** §17.2 gleichzeitig.
* **Die jsdom-Teststrategie ändert sich.** Sigma braucht WebGL, das jsdom nicht stellt. Die
  Trennung, die `GraphCanvas` heute schon hat (Logik exportiert und ohne Canvas getestet:
  `motorFuer`, Spielflächenrechnung), wird zum Prinzip: Layoutsteuerung, Diffing und Kodierung
  liegen in reinen Modulen mit Unit-Tests auf graphology-Ebene; das Zeichnen selbst prüfen
  Playwright-Läufe gegen den echten Browser — die Infrastruktur dafür existiert seit dem
  Lasttest.

Unverändert bleiben: die **zwei Betriebsarten** (Karte über den Bestand, Traversierung vom
Startknoten — sie sind Konzept, nicht Bibliothek), die visuelle Kodierung aus `theme.ts`, die
Physik-Regler, `prefers-reduced-motion` als Aus-Schalter, und die Regel, dass die Instanz eine
Datenänderung überlebt (Diffing statt Neuaufbau — in graphology sogar natürlicher, weil das
Datenmodell von der Zeichnung getrennt ist).

Zielmarke, überprüfbar wie beim letzten Mal: **5.000 Knoten mit laufender Physik bedienbar**
(Zoom < 100 ms Reaktion, Auswahl sofort), gemessen im Playwright-Lasttest gegen geseedete Daten,
bevor die alte Komponente gelöscht wird.

---

## 4. Das App-Gerüst

Ein festes Raster aus vier Zonen, in jedem Arbeitsbereich gleich:

```
┌──────┬────────────────────────────────────────┬─────────────┐
│      │ Kopfzeile: Bereichstitel, Suche, Store │             │
│ Nav  ├────────────────────────────────────────┤  Inspektor  │
│ Rail │                                        │  (Detail    │
│      │            Hauptfläche                 │   zum       │
│  E   │                                        │   Selek-    │
│  A   │                                        │   tierten)  │
│  V   │                                        │             │
└──────┴────────────────────────────────────────┴─────────────┘
```

* **Nav-Rail links**, schmal (Icons + Kürzel), auf Wunsch zu Text ausklappbar. Enthält die drei
  Arbeitsbereiche mit ihren Unterpunkten und den Kurationszähler (der Stapel bleibt sichtbar,
  egal wo man ist — die heutige Regel „eine Warteschlange, die man erst sieht, wenn man
  hinsieht, wächst unbemerkt" gilt weiter).
* **Inspektor rechts**: das heutige Seitenpanel, aber als echtes Panel — **einklappbar** (Taste
  und Griff), **in der Breite ziehbar** (Grenzen 280–560 px), Zustand in `localStorage`. Er
  zeigt immer das Selektierte: Knoten im Graphen, Zeile im Dokumentbrowser, Lauf in der
  Historie. Eine Komponente, drei Inhaltsarten.
* **Store-Wahl und der Satz „verlässt diesen Rechner nicht"** bleiben in der Kopfzeile, über
  allen Bereichen — die Begründung aus §17.2 (Ansicht 5) gilt unverändert. Im `personal`-Store
  färbt sich zusätzlich die Kopfzeilen-Unterkante als durchgehendes Signal.
* **Globale Suche in der Kopfzeile** (Kurzbefehl `/`): zweistufig wie §12.4, Ergebnis öffnet
  wahlweise Dokument oder Traversierung. Sie ist der Einstieg des Anwenders und macht den
  Umweg über „erst Reiter, dann Filter" überflüssig.
* **Tastatur durchgängig**: die Kurationsliste hat es vorgemacht (§17.2 Ansicht 4); künftig
  gilt es überall — Panel-Toggle, Suche, Bereichswechsel, im Graphen Auswahl und Aufklappen.

**Zustand**: geteilt wird, was eine Ansicht *bezeichnet* (Bereich, Unterpunkt, Store, Filter,
Selektion) — das bleibt in der URL. Persönlich ist, was die *Werkbank* einstellt (Panelbreiten,
eingeklappte Zonen, Physik-Regler) — das liegt in `localStorage`. Die Grenze ist heute schon so
gezogen und bewährt sich.

---

## 5. Designsystem

Die Farbwelt bleibt die beschlossene: **Grau, Weiß, Rot** nach Kaufland-CI, mit den bestehenden
Invarianten aus `theme.ts`, die Tests bereits bewachen — Rot ist knapp (Marke, genau eine
Primäraktion je Fläche, alles, was auf einen Menschen wartet), Konzepttypen kommen aus der
Taxonomie, Schwarz gehört den Clustern.

Was dazukommt, ist die Ebene *unter* den Farben, die heute fehlt:

* **Ein Komponentensatz statt Ad-hoc-Klassen.** Schaltfläche (drei Gewichte), Eingabefeld,
  Auswahl, Formularzeile, Panel, Tabelle, Leerzustand, Ladezustand, Fehlerzustand — einmal
  gebaut, überall benutzt. Die heutigen `wg-*`-Utilityklassen werden zu echten Komponenten mit
  festen Größen und Zuständen (hover/focus/disabled/busy); da liegen die „Buttons funktionieren
  nicht richtig"-Defekte begraben.
* **Dichte als Prinzip, nicht als Zufall.** Zwei Raster: *Lesen* (Dokumente, Persönlich —
  großzügig) und *Arbeiten* (Tabellen, Kuration, Betrieb — kompakt). Feldbreiten folgen dem
  Inhalt (eine ID ist schmal, ein Titel breit), nicht dem Container.
* **Jeder Zustand ist gestaltet.** Leere Warteschlange, laufender Lauf, abgebrochener Lauf,
  gesperrtes Feld eines quellgespiegelten Konzepts (§17.3: sichtbar gesperrt, nicht nur
  schreibgeschützt) — nichts davon ist ein leeres Rechteck.
* Grundlage bleibt **Tailwind mit dem Token-Set** aus `theme.ts`/`tailwind.config`; es kommt
  keine Komponentenbibliothek von der Stange dazu. Der Satz ist klein genug, um ihn selbst zu
  bauen, und die CI-Treue ist mit Fremdkomponenten teurer als ohne.

---

## 6. Was sich an API und Spezifikation ändert

Der Neubau ist fast vollständig aus vorhandenen Endpunkten speisbar. Die Restliste:

| Lücke | Für | Aufwand |
|---|---|---|
| `GET /api/v1/doctor` — Diagnose als JSON | Verwalten → Diagnose | klein: Service existiert, HTTP-Hülle fehlt |
| `dry_run` einheitlich in allen `POST /runs/*` (bei `link-orphans` vorhanden, Rest prüfen) | Automatisierung | klein |
| Verdichtete Qualitätszahlen (Quoten aus dem Journal: bestätigt/verworfen je Zeitraum) | Analysieren → Qualität | mittel; erste Ausbaustufe rechnet die UI aus `/stats` + Warteschlange selbst |
| schlanke Kantenform für die Karte (heute 14 Felder × 4.774 Kanten = 2,1 MB bei 2.000 Knoten) | Graph ab ~2k Knoten | mittel; war schon als Beobachtung notiert |

In der Spezifikation wird §17 auf dieses Konzept gehoben: 17.1 (Stack: sigma.js/graphology,
Layout im Worker), 17.2 (Arbeitsbereiche statt flacher Ansichtenliste; die sechs Ansichten
bleiben als Funktionen erhalten), neu 17.5 (Anwendergruppen und App-Gerüst). §17.3 und §17.4
bleiben unverändert — die Regeln und die Schreibrechte sind vom Neubau nicht berührt.

---

## 7. Umsetzung in Stufen

Jede Stufe lässt das System benutzbar zurück; die alte und die neue Welt existieren nie lange
parallel.

**Stand: U1 bis U5 sind umgesetzt.** Was aus jeder Stufe geworden ist, hält
[`STATUS.md`](STATUS.md) fest — Stufe für Stufe, mit den Entscheidungen und ihren Gründen.

| Stufe | Inhalt | Fertig, wenn |
|---|---|---|
| **U1 — Fundament** | Komponentensatz, App-Gerüst (Rail, Kopfzeile, Inspektor mit Einklappen/Ziehen), die sechs Ansichten unverändert in die drei Bereiche eingehängt | alle heutigen Tests grün, Panel-Verhalten per Test bewacht, kein Funktionsverlust |
| **U2 — Graphmotor** | sigma.js/graphology hinter der bestehenden `GraphCanvas`-Schnittstelle; Kodierung, beide Betriebsarten, Regler, Diffing portiert; Playwright-Lasttest bei 5.000 Knoten | Zielmarke aus 3.3 gemessen erreicht; Cytoscape-Abhängigkeiten entfernt |
| **U3 — Erkunden** | globale Suche, Dokumentlesen im Inspektor, Persönlich überarbeitet | Anwender-Sitzung („Was haben wir zu X?") ohne Reiterwissen durchführbar |
| **U4 — Analysieren** | Automatisierung (Formulare + Probelauf), Qualität, Kuration/Arbeitsplatz in den neuen Komponenten | jeder CLI-Aufbaulauf inkl. Parametern aus der UI startbar, Probelauf zuerst |
| **U5 — Verwalten** | Quellen & Sync vollständig, Diagnose (`/doctor`), Modelle & Kosten, Feinschliff | CLI-Paritätstabelle aus 2.2 erfüllt (außer den bewussten Ausnahmen) |

Testmaßstab unverändert: über 90 % Abdeckung, Logik bibliotheksfrei testbar, Playwright für das,
was nur der Browser beweisen kann.

---

## 8. Risiken und offene Punkte — wie sie ausgegangen sind

Die Stufen U1–U5 sind umgesetzt. Was hier als Risiko stand, ist damit entschieden:

* **Gestrichelte Kanten in WebGL** (3.3) — der benannte Rückfallweg wurde gezogen: Sigma
  bietet kein gestricheltes Kantenprogramm, und ein eigenes wäre mehr Fläche als Aussage.
  Unbestätigtes steht jetzt **voll deckend** vorn, Geprüftes tritt halbtransparent zurück;
  die Kantenart wanderte auf die Linienstärke. §17.2, `theme.ts` und die Legende sind
  gemeinsam auf diese Fassung gehoben — die Kodierung ist geändert, nicht verloren.
* **ForceAtlas2 ergibt andere Bilder als cola/fcose** — trat wie erwartet ein und ist
  einmalig. Positionen waren ohnehin nie geteilt (die URL trägt die Frage, nicht die
  Anordnung).
* **Rollen sind Ordnung, keine Grenze** — erledigt: Der Verwalten-Bereich schreibt es an,
  solange `auth_mode` nicht `oidc` ist. Wer dort steht, liest, dass die Sichtbarkeit keine
  Absicherung ist.
* **Bundle-Größe** — nachgemessen, und deutlicher als erhofft. Derselbe Build-Befehl, einmal
  auf dem Stand vor dem Motortausch (U1, Cytoscape + cola + fcose) und einmal heute:

  | | JS-Bündel | gzip |
  |---|---|---|
  | vorher (Cytoscape) | 903 kB | 280 kB |
  | heute (sigma + graphology, **inkl. U3–U5**) | 442 kB | 125 kB |

  Die Hälfte, obwohl vier Ansichten, ein Markdown-Renderer und die globale Suche
  dazugekommen sind.

**Vorhaben: die SAP-BTP-Dokumentation als Quelle für Tests mit echten Daten.**

Alles, was bisher in großer Menge geprüft wurde, ist synthetisch — und synthetische Bestände
sind, selbst wenn man sie absichtlich ungleichmäßig baut, immer noch *ausgedacht*. Ein
zusätzlicher Adapter gegen echte technische Dokumentation würde das ändern und genau die
Fachkonzepte prüfbar machen, die an synthetischen Daten nichts beweisen: ob das Clustering
brauchbare Themen findet, ob die Betitelung trifft, ob die Relationserkennung Sinnvolles
vorschlägt, ob die zweistufige Suche greift.

Gewählt ist **[`SAP-docs/btp-cloud-platform`](https://github.com/SAP-docs/btp-cloud-platform)**,
die SAP von sich aus auf GitHub veröffentlicht. Die Eckdaten passen bemerkenswert genau:

| | |
|---|---|
| Umfang | **2.070** Markdown-Dokumente unter `docs/`, 8,5 MB, im Mittel 4,3 kB je Dokument |
| Lizenz | **CC-BY-4.0** — Weiterverwendung mit Namensnennung ausdrücklich erlaubt |
| Zugang | `git clone`, kein Crawling, keine Grauzone |
| Gliederung | sieben Themenordner von 54 bis 1.066 Dokumenten — von Natur aus ungleichmäßig |

Zwei Eigenschaften machen sie für diesen Zweck besser als ein enzyklopädischer Bestand. Jedes
Dokument trägt eine stabile SAP-Kennung (`<!-- loiofa5af4ecdf90496b8eec54fe0e22150c -->`), die
sich unmittelbar als `external_id` verwenden lässt — genau das, was §10.1 für inkrementelles
Syncen braucht. Und die Dokumente verweisen mit relativen Links aufeinander, auch über
Ordnergrenzen hinweg. Das ist eine **von Menschen gepflegte Verweisstruktur** und damit ein
Prüfmaßstab, den kein synthetischer Bestand liefert: Man kann messen, ob das Clustering
Dokumente zusammenlegt, die auch tatsächlich aufeinander verweisen, und ob die
Relationserkennung Verbindungen findet, die die Autoren selbst gezogen haben. Dazu ist es
echte technische Unternehmensdokumentation — dieselbe Textsorte wie die Confluence-Seiten im
Betrieb.

Der Adapter wäre eine reguläre Quelle nach §8.4 neben Confluence und Jira, kein Sonderweg.

**Geprüft und verworfen — damit es niemand ein zweites Mal prüft:**

* **help.sap.com und api.sap.com direkt.** Beide `robots.txt` beginnen mit `User-agent: *` →
  `Disallow: /`; erlaubt sind nur namentlich benannte Suchmaschinen. Die SAP-Nutzungsbedingungen
  untersagen automatisiertes Abgreifen ausdrücklich **einschließlich Text- und Data-Mining**.
  Technisch möglich, rechtlich ausgeschlossen.
* **ERP- und EWM-Dokumentation.** Auf dem offenen Weg schlicht nicht vorhanden: Unter
  `SAP-docs` liegen 39 Repositories (37 davon CC-BY-4.0), aber ausschließlich BTP, SAPUI5,
  Datasphere und ABAP-Umgebung. Eine gezielte Suche nach offener EWM-Dokumentation fand
  86 Repositories, darunter keine einzige Dokumentation — nur Code. Wer SAP-EWM-Inhalte
  braucht, muss das über die Unternehmenslizenz und in einer internen Umgebung klären; in ein
  öffentliches Repository gehören sie nicht.
* **`SAP-samples`** (312 Repositories, 306 davon Apache-2.0): Code- und Workshop-Material,
  keine Fachdokumentation.
* **Open-Source-ERP** als Alternative für die Fachdomäne Lager/Produktion/Beschaffung —
  Odoo (CC-BY-SA-4.0, aktiv) und Apache OFBiz (Apache-2.0) wären brauchbar und bleiben als
  Rückfall notiert. Für den ersten Anlauf gibt SAP-BTP den Vorzug, weil es SAP-Fachsprache
  ist und die Verweisstruktur mitbringt.

**Was bewusst offen bleibt** (jeweils mit Grund, nicht vergessen):

* Die Bestätigungs-/Verwerfungsquote in der Qualitätsansicht braucht einen Endpunkt über das
  Journal; bis dahin steht dort keine geratene Zahl.
* `wg export` fehlt in der UI, weil es noch keinen API-Endpunkt dafür gibt; `wg migrate`
  fehlt absichtlich.
* Die schlanke Kantenform für die Karte (2,1 MB Nutzlast bei 2.000 Knoten) ist eine
  API-Aufgabe geblieben — der Motor trägt die Menge inzwischen mühelos.
