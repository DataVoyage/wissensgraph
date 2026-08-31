# Umsetzungsstand

Dieses Repository setzt das Architektur- und Spezifikationsdokument
[`architektur-spec-wissensgraph.md`](architektur-spec-wissensgraph.md) entlang des dort
definierten Stufenplans (§24) um. Das Dokument ist die verbindliche Grundlage; §-Verweise in
Docstrings und Kommentaren beziehen sich darauf.

**Arbeitsweise:** Stufe für Stufe. Eine Stufe beginnt erst, wenn die vorherige ihre im Dokument
festgelegte Abnahme erfüllt.

## Stand der Stufen

| Stufe | Inhalt | Stand |
|---|---|---|
| 0 | Projekt-, Container- und Konfigurationsgrundgerüst | **fertig** |
| 1 | Datenmodell und Migrationen | **fertig** |
| 2 | Domänenkern: Konzepte, Kanten, Upsert | **fertig** |
| 3 | Adapter-Framework und Mock-Quellen | **fertig** |
| 4 | Sync-Orchestrierung | **fertig** |
| 5 | Store-Trennung und Brücken | **fertig** |
| 6 | Kernspace-Auflösung und Referenzdichte | **fertig** |
| 7 | Model-Router über LangChain (Gemini als Entwicklungsmodell) | **fertig** |
| 8 | Embeddings und Clustering | **fertig** |
| 9 | Semantische Kantenerkennung | **fertig** |
| 10 | Verwaiste-Knoten-Vernetzung | **fertig** |
| 11 | HTTP-API und Web-UI | **fertig** |
| 12 | MCP-Retrieval-Layer | **fertig** |
| 13 | Anbindung der echten Quellen | **fertig** |

Stufe 14 (Föderation) ist im Dokument als Ausblick geführt und nicht Teil dieser Umsetzung.

---

## Stufe 0 — was steht

**Zweck laut §24:** "Ein Skelett, in das sich alles Weitere ohne Umbau einfügt. Die
Konfigurationsschicht steht zuerst, weil Leitprinzip 12 sonst nachträglich nicht mehr
durchsetzbar ist."

### Konfigurationsschicht (§6)

Die Präzedenzkette aus §6.2 ist vollständig umgesetzt:

```
Code-Defaults  <  config/*.yaml  <  .env-Datei  <  Prozess-ENV  <  CLI-Flag / API-Parameter
```

| Datei | Aufgabe |
|---|---|
| `src/wissensgraph/config/defaults.py` | Die einzige Stelle im Code mit Konfigurationsliteralen (§6.1 Regel 1) |
| `src/wissensgraph/config/placeholders.py` | `${WG_...}`-Auflösung; nicht auflösbar = Startfehler, kein leerer String |
| `src/wissensgraph/config/dotenv.py` | `.env`-Parser; die Prozessumgebung schlägt die Datei |
| `src/wissensgraph/config/env_mapping.py` | Die ENV-Schnittstelle aus §6.4 an einer Stelle |
| `src/wissensgraph/config/schema.py` | Pydantic-Schema, unveränderlich, mit den Regeln aus §6.5 |
| `src/wissensgraph/config/masking.py` | Secret-Maskierung für Logs und `/config/effective` (§20.2) |
| `src/wissensgraph/config/network.py` | Lokalitätsprüfung des `personal`-DSN (Leitprinzip 2) |

### Weitere Bausteine

- **Store-Registry** (`infrastructure/db/registry.py`) — nach §20.1 der einzige Weg zu einer
  Datenbankverbindung. Kein Codepfad wählt selbst einen DSN.
- **HTTP-API** (`api/`) — `/healthz`, `/readyz`, `/api/v1/config/effective`, Bearer-Auth,
  RFC-7807-Fehler, Korrelations-ID je Anfrage.
- **Strukturiertes Logging** (`observability/logging.py`) — Pflichtfelder aus §21.1; `body` und
  verwandte Inhaltsfelder werden per Prozessor entfernt, Secrets unabhängig vom Level maskiert.
- **CLI** (`cli.py`) — `wg config show`, `wg doctor`, `wg version`.
- **Container** — `docker-compose.yml` mit `db-shared`, `db-personal`, `api`, `worker`, `broker`,
  `mcp`, `ui` und der Netzsegmentierung aus §5.2 (`wg-personal` ist `internal: true`).
- **Web-UI** — leere SPA (React/Vite) mit Verbindungsanzeige; Konfiguration zur Laufzeit aus
  `/config.js`, nicht zur Bauzeit (§17.1).
- **Schichtentrennung** — `import-linter` erzwingt, dass `domain` und `ports` keine
  Infrastruktur importieren (§4.2).

### Abnahme (§24, Stufe 0)

| Kriterium | Stand |
|---|---|
| `docker compose --profile minimal up` startet alles | erfüllt |
| `/readyz` meldet beide Datenbanken | erfüllt |
| `wg config show` zeigt die aufgelöste Konfiguration mit maskierten Secrets | erfüllt |
| Ein fehlender Pflichtwert bricht den Start mit klarer Meldung ab | erfüllt |

### Ausdrücklich außen vor

Jede fachliche Logik. Es gibt noch keine Tabellen, keine Konzepte, keine Kanten und keinen
Model-Router — das sind Stufe 1, 2 und 7.

---

## Stufe 1 — was steht

**Zweck laut §24:** "Das Schema aus §7 existiert in beiden Stores, inklusive der Invarianten."

### Ein Skriptbaum, zwei Datenbanken

§7.3 verlangt "zwei PostgreSQL-Datenbanken mit identischem Schema". Getrennte Migrationsbäume je
Store wären die naheliegende Antwort — und die falsche: Sie driften über die Zeit auseinander,
und genau das soll ausgeschlossen sein. Es gibt deshalb **einen** Satz Versionsskripte
(`src/wissensgraph/migrations/`), der beide Datenbanken bedient. Die einzige zulässige Abweichung
steht als Fallunterscheidung *innerhalb* der Migration.

| Datei | Aufgabe |
|---|---|
| `migrations/versions/0001_initial_schema.py` | Die DDL aus §7.4 — Tabellen, Indizes, Sicht, Invarianten |
| `migrations/context.py` | Store-Name und Vektordimension aus der geprüften Konfiguration |
| `migrations/env.py` | Alembic-Umgebung; baut bewusst keine Verbindung selbst auf |
| `infrastructure/db/migrations.py` | Ausführung samt Advisory-Lock (§5.5) |
| `infrastructure/db/introspection.py` | Nachschlagen dessen, was tatsächlich im Schema steht |

Zwei Werte kommen von außen in die Migration: der **Store-Name**, weil §7.4 den CHECK gegen
personal-Verweise ausdrücklich nur im shared-Store anlegt, und die **Vektordimension** aus
`WG_EMBEDDING_DIM`, die als `vector(n)` in den Spaltentyp eingeht. Beides über
Umgebungsvariablen direkt im Skript zu lesen, würde die Präzedenzkette aus §6.2 umgehen — die
Werte kommen deshalb aus der bereits validierten Konfiguration.

Getrennt sind die **Versionstabellen**: `alembic_version_shared` und `alembic_version_personal`.
Der Store steht im Tabellennamen, damit die Trennung auch dann gilt, wenn beide Schemata je in
derselben Datenbank landen sollten.

### Was sich dadurch am Betrieb ändert

- **`wg migrate`** — beide Stores, wiederholbar. `--check` berichtet nur (Rückgabewert 1 bei
  ausstehenden Migrationen), `--sql` rendert den Trockenlauf ohne Datenbankzugriff,
  `--downgrade-to` nimmt zurück und verlangt dafür eine ausdrücklich genannte Revision.
- **`wg serve`** ist der neue Startbefehl des api-Containers und setzt §5.5 um: erst Migrationen,
  dann Server. Die Reihenfolge steht als Python-Code und nicht als verkettetes Shell-Kommando —
  so gilt sie auf jeder Plattform gleich und ist testbar.
- **Advisory-Lock** (§5.5): Migrationen laufen nie parallel. Benutzt wird `pg_try_advisory_lock`
  in einer Schleife mit Frist statt des blockierenden `pg_advisory_lock`; ein Container, der beim
  Start unbegrenzt wartet, ist von einem hängenden Container nicht zu unterscheiden.
- **`wg doctor`** prüft jetzt zusätzlich das Schema: Ist die Migration durch, und passt die
  Dimension im Schema noch zur Konfiguration? Die zweite Frage fängt den Fall ab, dass
  `WG_EMBEDDING_DIM` *nach* der Migration geändert wurde — sonst fiele der Widerspruch erst beim
  ersten Embedding-Lauf auf (§11.7).
- **Kein Init-Skript mehr** für die Datenbank-Container. `vector`, `pg_trgm` und `uuid-ossp` legt
  die Migration selbst an (§7.3). Damit gilt für eine von Hand oder in einem Test angelegte
  Datenbank dasselbe wie für den Container — es gibt nur eine Stelle, die das tut.

### Abweichung vom Dokument

`concepts.store` ist laut §7.4 "redundant, aber explizit". Redundanz ohne Prüfung driftet
auseinander, sobald ein Schreibpfad den falschen Wert setzt. Die Migration legt deshalb
zusätzlich `ck_concepts_store` an, der die Spalte an die Datenbank bindet, in der die Zeile
tatsächlich liegt. Ein falsch geroutetes Upsert wird damit zum Fehler statt zu stillem
Datenschaden. Das ist die einzige Ergänzung über die DDL des Dokuments hinaus.

### Abnahme (§24, Stufe 1)

| Kriterium | Stand |
|---|---|
| Migration läuft auf leeren Datenbanken durch | erfüllt — der api-Container migriert beide Stores beim Start |
| Migration ist wiederholbar | erfüllt — zweiter Lauf meldet "unverändert", `--check` gibt 0 zurück |
| Der Constraint lehnt einen personal-Verweis im shared-Store ab | erfüllt — `ck_shared_no_personal_ref`, geprüft per `INSERT` an der Anwendung vorbei |
| Ein HNSW-Index existiert | erfüllt — `ix_emb_hnsw`, Zugriffsmethode `hnsw` in beiden Stores |

Guard-Test 4 aus §20.1 ist damit erfüllt und steht in `tests/guards/test_store_invarianten.py`.

### Ausdrücklich außen vor

Repositories und Fachlogik. Es gibt Tabellen, aber noch keinen Code, der Konzepte oder Kanten
schreibt — das ist Stufe 2.

---

## Stufe 2 — was steht

Die Kernoperation `upsert_concept()` aus §10.2 samt allem, worauf sie aufsetzt: Domänenmodelle,
Content-Hash, `[[id]]`-Referenzen, Kantenpflege, Änderungsjournal und Repositories je Store.

### Die Aufteilung, die alles andere trägt

§10.2 nennt fünf Regeln. Vier davon sind Entscheidungen über Werte, eine ist eine Aussage über
Infrastruktur. Genau entlang dieser Linie ist der Code geschnitten:

| Ort | Aufgabe |
|---|---|
| `domain/upsert.py` | Regeln 1 bis 4 als **reine Funktion** über zwei Zustände — ohne Datenbank |
| `services/concepts.py` | Regel 5: Konzept, Kanten und Journal in *einer* Transaktion |
| `ports/repositories.py` | Was gebraucht wird, nie womit es erfüllt wird |
| `infrastructure/db/` | Repositories, Arbeitseinheit, Tabellenbeschreibungen |

Der Gewinn ist konkret: Die gesamte Kurationslogik aus §10.4 — welches Feld gewinnt, wann eine
Bestätigung verfällt, wann ein Konflikt entsteht — lässt sich ohne PostgreSQL durchspielen. Ein
neuer Kurationsfall ist ein Unit-Test, kein Integrationstest.

### Wie die beiden Kurationsregeln zusammenpassen

§10.4 sagt für `title`, `description`, `body`, `resource`: "Quelle gewinnt immer". §10.2 Regel 4
sagt: "Kuratierte Felder werden von der Quelle nicht überschrieben." Das ist kein Widerspruch,
sondern eine Fallunterscheidung nach Konzepttyp:

- **quellgespiegelt** (`source_mirrored: true`, §7.2): Die Inhaltsfelder sind für UI, API und
  Agent ohnehin schreibgeschützt — eine kuratierte Fassung des Bodys kann es dort gar nicht
  geben. Kuratierbar sind `status`, `tags` und die Verifikationsfelder; für sie gilt die Tabelle
  aus §10.4 wörtlich.
- **nicht gespiegelt** (Notiz, Brücken-Konzept): Hier kann ein Mensch den Inhalt geschrieben
  haben. Versucht eine Quelle später, ihn zu überschreiben, greift Regel 4.

Ein lokaler Schreibvorgang — erkennbar am fehlenden `source_name` — ist davon nicht betroffen:
Wer von Hand schreibt, überschreibt seine eigene Kuration.

### Abweichungen und Ergänzungen

Drei Stellen gehen über den Wortlaut des Dokuments hinaus. Alle drei folgen aus einer Vorgabe an
anderer Stelle des Dokuments:

1. **Zwei zusätzliche Änderungsarten.** §7.4 zählt die Werte von `change_log.change_type` auf,
   ohne `curation_conflict` (von §10.2 Regel 4 verlangt) und ohne `verification_reset` (von §10.4
   verlangt: `verified_*` "wird bei inhaltlicher Änderung zurückgesetzt, mit change_log-Eintrag").
   Beide sind ergänzt; die Spalte ist Freitext, das Schema ändert sich dafür nicht.

2. **Ein Kurationskonflikt wird einmal vermerkt, nicht einmal je Lauf.** Ein Konflikt ist ein
   Zustand, der besteht, bis ein Mensch ihn auflöst — kein Ereignis. Solange die Quelle denselben
   abgewehrten Inhalt liefert, ist es derselbe Konflikt. Ohne diese Entdopplung wüchse die
   Kurationsliste (§17.2) mit jedem Sync-Lauf, und ein UPDATE, das nur `updated_at` fortschreibt,
   käme dazu. Erkennungsmerkmal ist der Hash des Quellinhalts im `detail`.

3. **`[[id]]`-Referenzen zeigen in dieser Stufe in den eigenen Store.** Eine Referenz nennt eine
   ID, keinen Store. Die Auflösung über die Store-Grenze — eine Notiz in `personal`, die auf eine
   Confluence-Seite in `shared` zeigt — kam mit der Brückenlogik der Stufe 5 hinzu; seither wird
   der Zielstore gesucht statt angenommen. Bleibt eine ID unauffindbar, entsteht die Kante
   weiterhin mit `resolved = false` und dem eigenen Store als Ziel, was genau der Zustand ist, den
   §8.5 dafür vorsieht.

### Zwei neue Schutzregeln

- **`tests/guards/test_schema_abgleich.py`** vergleicht die Tabellenbeschreibungen in
  `infrastructure/db/tables.py` Spalte für Spalte mit einer wirklich migrierten Datenbank —
  Namen, Vorhandensein, Nullbarkeit. Zwei Beschreibungen derselben Sache driften auseinander,
  sobald jemand nur eine davon ändert; eine fehlende Spalte fiele sonst erst zur Laufzeit auf.
- **Import-Kontrakt "Dienste kennen keine Infrastruktur".** Ohne ihn wandert das erste
  `select(...)` in einen Service, und die Kernoperation ist nicht mehr ohne Datenbank testbar.
  Zusätzlich stehen die Ports jetzt *über* der Domäne statt neben ihr: Ein Port beschreibt seine
  Operationen in Domänenbegriffen, die Domäne kennt umgekehrt keinen Port.

### Abnahme (§24, Stufe 2)

Jedes Kriterium ist zweimal geprüft: gegen die speicherresidenten Ports (`tests/unit`) und gegen
echtes PostgreSQL (`tests/integration`).

| Kriterium | Nachweis |
|---|---|
| Zweifaches Upsert derselben unveränderten ID erzeugt genau einen `change_log`-Eintrag | `TestAbnahme::test_zweifaches_upsert_erzeugt_genau_einen_eintrag` |
| Ein geänderter Hash erzeugt einen zweiten | `TestAbnahme::test_geaenderter_hash_erzeugt_den_zweiten_eintrag` |
| Eine Kante auf ein unbekanntes Ziel entsteht mit `resolved = false` und ohne Fehler | `TestAbnahme::test_kante_auf_unbekanntes_ziel_entsteht_ohne_fehler` |
| Ein kuratiertes Feld überlebt ein Quell-Update und erzeugt einen Konfliktvermerk | `TestAbnahme::test_kuratiertes_feld_ueberlebt_und_erzeugt_einen_konfliktvermerk` |

Zusätzlich gegen den laufenden `dev`-Stack durchgespielt: Anlegen, unverändertes Wiederholen,
unaufgelöste Kante, Journal und die Bestätigung, dass der `shared`-Store das Konzept aus
`personal` nicht kennt.

### Ausdrücklich außen vor

Quellen, Embeddings, API — so vorgesehen in §24. Es gibt noch keinen Adapter, der Entwürfe
liefert, und keinen Lauf, der sie einsammelt; das sind die Stufen 3 und 4. `runs`,
`source_cursors` und `model_calls` sind migriert, aber unbenutzt.

---

## Stufe 3 — was steht

Quellanbindung so, dass die nächste Quelle nichts am Kern ändert — und Entwicklung ohne Zugang
zu den echten Systemen.

### Der Kontrakt und die drei Wege zu einem Adapter

`ports/sources.py` beschreibt, was der Kern von einer Quelle verlangt: `SourceDocument`,
`Cursor`, `AdapterCapabilities`, `HealthStatus` und das Protokoll `SourceAdapter` (§8.2). Es ist
ein `Protocol` und keine Basisklasse — wer einen Adapter beisteuert, schuldet Methoden, keine
Abstammung. Der Dummy-Adapter unter `tests/support/dummy_adapter.py` erbt von nichts und besteht
trotzdem die volle Contract-Suite; das ist der Beleg.

Die Registry (`infrastructure/adapters/registry.py`) findet einen Adapter auf drei Wegen, in
dieser Rangfolge:

1. **`class: "paket.modul:Klasse"`** in `sources.yaml` — der spezifischste Weg, deshalb der
   stärkste. Keine Installation, kein Entry Point, keine Zeile Kerncode.
2. **Entry Point** unter der Gruppe `wissensgraph.adapters` — für ein installiertes Paket, das
   sich selbst anmeldet. Ein gleichnamiger Entry Point verdrängt eine mitgelieferte Umsetzung.
3. **Mitgeliefert** — Confluence, Jira, Fixture. Eingebaut, damit ein frisch ausgechecktes
   Repository ohne Installationsschritt läuft.

Zwei Fehlerarten sind dabei bewusst getrennt, weil das Dokument sie an zwei Stellen verschieden
behandelt: **Nicht auffindbar** ist ein Startfehler (§6.5, letzter Punkt) — eine Konfiguration,
die auf einen Adapter zeigt, den es nicht gibt, ist falsch, und zwar jetzt. **Auffindbar, aber
kaputt** ist keiner: "Ein fehlerhafter Adapter deaktiviert sich selbst und erscheint als
`unhealthy`, ohne den Start zu verhindern" (§8.3). Eine unerreichbare Confluence-Instanz darf
nicht verhindern, dass die Jira-Quelle läuft.

### Gemockt wird das Quellsystem, nicht der Adapter

§9.1 ist an dieser Stelle ungewöhnlich streng, und die Strenge lohnt sich. Ein gemockter Adapter
bewiese nur, dass der Kern mit einem Adapter umgehen kann, der sich wie erwartet verhält. Der
Dienst `mock-sources` bildet stattdessen die Confluence- und Jira-Endpunkte nach, die die Adapter
wirklich benutzen — mit ihrer Paginierung (`start`/`limit` gegen `startAt`/`maxResults`) und ihrer
Verschachtelung. Damit läuft der komplette Codepfad in der Entwicklung tatsächlich, und die
Umstellung auf die echte Quelle ist eine URL (§9.4).

Die Steuerungs-API aus §9.3 ist vollständig da: `reset`, `scenario/{name}`, `latency`, `fail`,
`state`. Ihr wichtigster Parameter ist `after_requests` bei `fail` — damit lässt sich ein Abbruch
*mitten* in einer Iteration auslösen. Ohne ihn wäre die letzte Zusicherung aus §22.3
("Netzwerkfehler mitten in der Iteration lassen den Cursor unverändert") nicht prüfbar, sondern
nur behauptet.

Der Seed-Korpus unter `fixtures/` hat den Umfang aus §9.2: 120 Seiten in drei klar trennbaren
Themenfeldern, 80 Vorgänge, dazu die vier vorbereiteten Sonderfälle — ein Dokument zwischen zwei
Themenfeldern (Single Sign-on im Kundenportal), ein bewusst isolierter Knoten (Wartungsplan der
Kaffeemaschine), ein `depends_on`-Paar über Themengrenzen hinweg (Ladestrecke → Token-Dienst) und
118 interne Verweise für die Referenzauflösung.

### Die Contract-Suite gehört dem Kern

`src/wissensgraph/testing/adapter_contract.py` steht im Paket und nicht unter `tests/`. §8.6
verlangt das ausdrücklich: "sie ist Teil des Kerns und wird nicht kopiert". Läge sie in der
Testsuite, könnte ein externer Adapter sie nur abschreiben — und ab der ersten Abschrift gäbe es
zwei Fassungen.

`tests/contract/test_adapter_contract.py` besteht deshalb aus vier Klassen und keinem einzigen
eigenen Test. Wer die Quelle steuern kann, überschreibt die Haken `aendern`,
`rate_limit_erzwingen` und `ausfall_erzwingen`; wer nicht, überspringt die betroffenen Prüfungen
**mit Begründung**. Eine Zusicherung, die nicht geprüft werden kann, soll als ungeprüft dastehen
und nicht als bestanden — deshalb die fünf sichtbaren Skips beim Fixture- und Dummy-Adapter.

Confluence und Jira laufen in dieser Suite gegen den echten Mock-Server, nur ohne Socket
(`starlette.testclient.TestClient` ist ein `httpx.Client`, der direkt in die ASGI-Anwendung
spricht). Damit prüfen die Contract-Tests wirklich Paginierung, 429-Behandlung und
Abbruchverhalten — auf jedem Rechner, auch ohne Docker.

### Abweichungen und Ergänzungen

Drei Stellen gehen über den Wortlaut des Dokuments hinaus, jede aus einer anderen Vorgabe
desselben Dokuments begründet:

1. **`target.store` ist nur Kontrolle, nicht Steuerung.** §8.4 zeigt `store:` im Beispiel einer
   Quelle. Benutzt wird er nicht: Der Store ergibt sich aus dem Scope, und die Zuordnung
   Scope → Store steht in `wissensgraph.yaml` (§7.3). Ein angegebener abweichender Store ist ein
   Startfehler. Sonst gäbe es zwei Wahrheiten darüber, in welche Datenbank eine Quelle schreibt,
   und die Datenschutzgrenze aus Leitprinzip 2 hinge an der Sorgfalt beim Ausfüllen einer
   YAML-Datei (§20.1).

2. **JSONPath als bewusst kleine Teilmenge.** §8.4 schreibt Ausdrücke wie
   `$.metadata.labels[*].name`. Unterstützt sind `$`, `.name`, `['name']`, `[0]` und `[*]` —
   mehr kommt in einer Mapping-Konfiguration nicht vor. Filterausdrücke, Slices und rekursiver
   Abstieg fehlen, und zwar sichtbar: Ein nicht unterstützter Ausdruck bricht beim *Laden* ab und
   nennt Position und Zeichen, statt beim ersten Lauf still einen leeren Titel zu erzeugen.

3. **Quellübergreifende Referenzen über bekannte Präfixe.** §8.5 nennt den Regelfall: externe ID
   plus Quellpräfix ergibt die interne ID. Ein Jira-Vorgang, der auf eine Confluence-Seite zeigt,
   kann das mit einer externen ID nicht ausdrücken. Nennt eine Referenz deshalb bereits ein in
   `sources.yaml` konfiguriertes Präfix, wird sie unverändert übernommen. Die Einschränkung auf
   *bekannte* Präfixe ist wesentlich — sonst würde eine externe ID, die zufällig einen
   Doppelpunkt enthält, stillschweigend als fremde Konzept-ID gelesen.

### Zwei Änderungen am Domänenkern der Stufe 2

Beide durch §8.5 veranlasst:

- **`replace_generated` nimmt jetzt eine Menge von Erzeugern.** §8.5 verlangt für Referenzen aus
  der Quelle `generated_by: 'code:source-reference'`, für die aus dem Fließtext gilt
  `code:body-reference`. Zwei getrennte Aufrufe wären nicht atomar gewesen: Ein Verweis, der von
  der einen Herkunft in die andere wandert, verschwände zwischen ihnen für die Dauer eines Laufs.
  Jeder Entwurf trägt nun sein eigenes `generated_by`; der Parameter sagt nur, welche bestehenden
  Kanten der Aufruf ersetzen darf.
- **`ConceptDraft` trennt `body_references` und `source_references`.** Steht derselbe Verweis in
  beidem, gewinnt der Text — er ist der belegbare Nachweis, und zwei Kanten mit demselben Tripel
  kann es ohnehin nicht geben.

### Zwei neue Schutzregeln

- **"Adapter kennen den Graphen nicht"** (import-linter). §8.2 Regel 2: "Der Adapter kennt weder
  `concepts` noch SQL noch Scopes. Er liefert DTOs." Ohne die Regel baut der erste Adapter, dem
  ein Feld fehlt, sich selbst einen Konzept-Entwurf zusammen — und das Versprechen aus §8.1, dass
  eine neue Quelle den Kern nicht anfasst, ist weg.
- **"Mock-Quellen kennen den Graphen nicht"**. Ein Mock-Server, der den Graphen kennt, wäre keiner
  mehr: Er würde beweisen, dass der Kern mit einer Quelle umgehen kann, die er selbst gebaut hat.

### Abnahme (§24, Stufe 3)

| Kriterium | Wo geprüft | Ergebnis |
|---|---|---|
| Beide Adapter bestehen die Contract-Suite | `tests/contract/test_adapter_contract.py` | 59 bestanden, 5 begründet übersprungen |
| Der Fixture-Korpus ist vollständig als Konzepte abgebildet | `test_ingest.py`, `test_quellen_postgres.py` | 120 + 80 = 200 Konzepte, 217 Kanten |
| Das Änderungsszenario führt beim zweiten Lauf nur zu den erwarteten Aktualisierungen | dieselben | 1 Dokument, 1 `updated`; ohne Szenario 0 Journaleinträge |
| Ein im Test angelegter Dummy-Adapter wird allein über einen Config-Eintrag aktiv | `test_adapter_registry.py` | `class:`-Eintrag genügt, kein Kerncode berührt |

Zusätzlich gegen den laufenden Stack geprüft: `wg sources list` und `wg doctor` melden beide
Quellen als `healthy`, und ein Lauf im `api`-Container gegen den Container `mock-sources` und die
echten Entwicklungsdatenbanken ergibt 200 Konzepte, 217 Kanten, 0 unaufgelöste Kanten — der
zweite Lauf schreibt nichts (§22.2 Punkt 1). Die Testdaten wurden danach wieder entfernt.

### Ausdrücklich außen vor

Echte Zugangsdaten und Zeitsteuerung, so vorgesehen in §24. `schedule.cron` wird gelesen und
validiert, aber nicht ausgeführt.

Der Endpunkt `GET /api/v1/sources` (§16.2) fehlt noch; die HTTP-API ist Stufe 11. Bis dahin
beantwortet `wg sources list --json` dieselbe Frage.

---

## Stufe 4 — was steht

Aus einem Durchlauf wird ein **Lauf**: wiederholbar, nachvollziehbar, abbrechbar. Die Stufe fügt
dem Dokumentendurchlauf der Stufe 3 nichts hinzu, was ein einzelnes Dokument betrifft — sie fügt
alles hinzu, was den *Vorgang* betrifft.

### Die Reihenfolge am Ende ist der ganze Punkt

`SyncService.sync` folgt dem Flussdiagramm aus §10.1 Schritt für Schritt. Die einzige Stelle, an
der die Reihenfolge nicht offensichtlich ist, ist zugleich die wichtigste:

1. Dokumente lesen und schreiben — jedes in seiner eigenen Transaktion,
2. Löschungen als Grabsteine setzen,
3. **erst jetzt** den Cursor speichern,
4. den Lauf abschließen und die Statistik schreiben.

§21.3 sagt für eine nicht erreichbare Quelle: „Lauf endet mit `failed`, Cursor bleibt unverändert,
Wiederholung ist gefahrlos." Ein Cursor, der schon unterwegs fortgeschrieben würde, ließe den Rest
des Bestands stillschweigend verschwinden — der nächste Lauf setzte hinter dem Abbruch auf und
holte das Übersprungene nie nach. Deshalb liefert der Adapter seine Fortschrittsmarke auch erst in
`next_cursor()` nach vollständig durchlaufener Iteration (§8.2), und deshalb steht das Speichern
hier hinter allem anderen.

Dass ein einzelnes Dokument eine eigene Transaktion bekommt, gehört zur selben Zusicherung: Was
vor dem Abbruch geschrieben wurde, bleibt geschrieben, und weil der Cursor stehen bleibt, kommt
es beim nächsten Lauf noch einmal — mit unverändertem Hash und damit ohne Wirkung (§10.2 Regel 3).

### Ein Trockenlauf tut alles und verwirft es

`--dry-run` täuscht nichts vor. Der Lauf öffnet **eine** Transaktion, führt jedes Dokument wirklich
durch die Kernoperation bis zum `INSERT` und rollt am Ende zurück. Technisch steckt dahinter eine
Fabrik (`_Probelauf`), die statt einer neuen Arbeitseinheit immer dieselbe offene herausgibt und
deren Lebenszyklus stilllegt; der äußere Block rollt genau einmal zurück.

Der Aufwand lohnt sich, weil die Alternative die Frage nicht beantwortet: Eine Vorschau, die den
Schreibpfad umgeht, sagt über den Schreibpfad nichts. Ein Trockenlauf, der 120 Konzepte meldet,
hat 120 Konzepte geschrieben — nur eben nicht behalten.

Konsequent zu Ende gedacht heißt das auch: Ein Trockenlauf hinterlässt **keine Zeile in `runs`**.
`--dry-run` verspricht, nichts zu verändern, und eine Lauf-Zeile wäre eine Veränderung. Der
Bericht ist derselbe, er wird nur nirgends abgelegt.

### Löschung setzt Grabsteine und rührt keine Kante an

Meldet ein Adapter mit `capabilities.deletions` gelöschte Objekte, werden die zugehörigen Konzepte
auf `status = tombstone` gesetzt — Inhalt und Kanten bleiben vollständig stehen. §7.6 begründet
das: „damit persönliche Notizen, die darauf verlinkt haben, nachvollziehbar bleiben." Das ist
zugleich der Fall, in dem §10.4 die Kuration ausdrücklich überstimmt: „Kuration gewinnt, außer die
Quelle meldet Löschung."

Die Fähigkeit wird am Flag abgelesen und nicht an einer Ausnahme erprobt — §8.2 Regel 3: „Der
`SyncService` fragt Flags ab, nicht Ausnahmen."

Eine wiederholte Löschmeldung schreibt nichts: Löschung ist ein Zustand, kein Ereignis. Ohne diese
Prüfung entstünde bei jedem Lauf eine neue Journalzeile für dasselbe längst gelöschte Objekt.

### Nebenläufigkeit: abweisen, nicht warten

Die Sperre je Quelle ist ein PostgreSQL-Advisory-Lock auf dem Quellnamen (§10.5), und drei Details
entscheiden darüber, ob sie wirkt:

- **Eine eigene Verbindung.** Advisory-Locks hängen an der Sitzung. Läge die Sperre auf der
  Verbindung einer Arbeitseinheit, fiele sie nach dem ersten geschriebenen Dokument.
- **`pg_try_advisory_lock`, nicht `pg_advisory_lock`.** §10.5 verlangt eine Abweisung („liefert
  `409 Conflict`"), keine Warteschlange. Ein zweiter Aufruf, der stumm wartet, sähe für den
  Aufrufer aus wie ein besonders langsamer Lauf.
- **Sie umschließt auch das Speichern von Cursor und Statistik.** Läge sie enger, könnte ein
  zweiter Lauf zwischen dem letzten Dokument und dem Cursor starten — und mit dem *alten* Cursor
  loslaufen.

Die Abweisung nennt die ID des laufenden Laufs, wie §10.5 es verlangt. Die Sperre selbst kennt sie
nicht; der Dienst schlägt sie in `runs` nach und reichert die Ausnahme an. Bewusst in dieser
Richtung: Entschieden wird über den Lock, nicht über die Abfrage. Umgekehrt entstünde zwischen
Abfrage und Anlage genau das Zeitfenster, in dem zwei Läufe zugleich starten könnten.

### Die Queue transportiert Aufträge, keine Arbeit

§16.3 trennt Anstoßen und Ausführen: Ein `POST /runs/*` legt einen Lauf an, stellt einen Job ein
und antwortet mit `202 Accepted`; der `worker` führt aus. Der Job trägt deshalb **nur einen
Verweis** — Lauf-ID, Art, Store, Parameter — und nie Nutzlast. Der Zustand liegt in `runs`.

Das macht die einzige Schwäche eines `BLPOP` erträglich: Es entnimmt *at most once*. Stürzt der
Worker zwischen Entnehmen und Abschluss ab, ist der Job weg — der Lauf aber steht weiterhin
sichtbar als `queued` oder `running` da. Verloren geht nur der Anstoß, und den kann ein Mensch
wiederholen. Die Alternative wäre eine zweite Liste als Zwischenablage samt Aufräumlauf für
verwaiste Einträge: Zustandshaltung, die die Datenbank bereits leistet.

Ohne konfigurierten Broker wählt die Laufzeit eine Warteschlange im Speicher. Das ist kein
Testhilfsmittel, sondern der Normalfall für `wg sync`: Der Befehl arbeitet synchron und soll keinen
laufenden Redis voraussetzen.

### Wo ein Lauf verbucht wird

Im Store, in den er schreibt — für einen Sync also im Store des Ziel-Scopes. Ein Lauf über eine
persönliche Quelle hinterlässt damit keine Spur im geteilten Store (Leitprinzip 2). `runs` und
`source_cursors` liegen deshalb in beiden Datenbanken; die Migration aus Stufe 1 legt sie ohnehin
schon dort an.

### Ein neues Modul: `runtime.py`

Alle bisherigen Module halten ihre Schicht strikt ein — die Dienste kennen nur Ports, die Adapter
kennen den Graphen nicht. Irgendwo muss trotzdem entschieden werden, *welche* Umsetzung ein Port
bekommt. Das steht jetzt in `wissensgraph/runtime.py`, an einer Stelle statt verstreut in CLI, API
und Worker. `wg sync`, das spätere `POST /runs/sync` und der Worker sind drei Wege zu demselben
Lauf — es soll keinen geben, auf dem er nach anderen Regeln liefe (Leitprinzip 14).

### Ergänzungen am Bestand

- **`plan_upsert` kennt die Rückkehr aus dem Grabstein.** Liefert eine Quelle ein zuvor gelöschtes
  Objekt wieder aus, ist das eine Aussage über seine *Existenz* und geht am Content-Hash vorbei.
  Ohne diese Regel bliebe ein wiederhergestelltes Objekt für immer ein Grabstein, weil sein Text
  sich nicht geändert hat.
- **`SourceIngestService` überspringt fehlerhafte Einzelobjekte.** §21.3: „Einzelnes Quellobjekt
  fehlerhaft → überspringen, in `runs.stats.errors` zählen, Lauf fortsetzen." Ein Ausfall der
  *Quelle* wird dagegen durchgereicht — er betrifft nicht ein Objekt, sondern alle noch
  ausstehenden.
- **`wg doctor` prüft den Broker.** Nie als Fehler, immer als Warnung: Ohne Broker fallen nur die
  asynchronen Läufe aus, und im Profil `minimal` läuft gar keiner (§5.4). Ein Diagnosewerkzeug, das
  im Regelbetrieb Fehlalarme gibt, wird nicht mehr gelesen.
- **`worker` und `mcp` haben im Compose keinen Healthcheck mehr.** Der des Images fragt `/healthz`
  auf Port 8080 ab, den es nur im `api`-Dienst gibt; ein einwandfrei arbeitender Worker stand
  dauerhaft als `unhealthy` da. (Für `mcp` gilt das seit dem Umstieg auf Streamable HTTP nicht
  mehr — er hat einen eigenen, siehe den Nachtrag zu Stufe 12.)

### Eine neue Schutzregel

**„Die Job-Queue kennt den Graphen nicht"** (import-linter, damit acht Verträge). Eine Queue, die
den Graphen kennt, wäre die Einladung, Nutzlast statt Verweisen zu verschicken — und damit eine
zweite Wahrheit über den Zustand des Systems.

### Abnahme (§24, Stufe 4)

| Kriterium | Wo geprüft | Ergebnis |
|---|---|---|
| Vollständiger und inkrementeller Lauf über den Mock | `test_sync_service.py`, `test_sync_postgres.py` | 120 Seiten beim ersten Lauf, 1 Dokument beim zweiten; ohne Änderung 0 |
| Löschszenario setzt Tombstones ohne Kantenverlust | dieselben | `confluence:100003` wird `tombstone`, Kantenzahl unverändert |
| Paralleler Start derselben Quelle wird abgewiesen | `test_sync_postgres.py` | zweite Sitzung bekommt `SourceBusy` mit der ID des laufenden Laufs |
| Netzwerkabbruch mitten im Lauf lässt den Cursor unverändert | dieselben | Lauf `failed`, `source_cursors.cursor` unverändert |

Der dritte Punkt zählt nur im Integrationstest wirklich: Der Advisory-Lock wirkt über
Verbindungsgrenzen, die Speicher-Sperre der Unit-Tests nur innerhalb eines Prozesses.

### Ausdrücklich außen vor

Zeitplanung und echte Quellen, so vorgesehen in §24. `schedule.cron` wird weiterhin nur gelesen
und validiert. Kein Lauf startet von selbst; jeder braucht `wg sync`, einen Job in der Queue oder
später `POST /runs/sync`.

Ebenfalls noch offen: `progress` bleibt während eines Laufs auf 0 und springt am Ende auf 1.0. Ein
Anteil setzt eine bekannte Gesamtmenge voraus, und die hat ein Adapter nicht — `iter_documents`
ist ein Generator und weiß selbst nicht, wie viele Objekte noch kommen (§8.2). Stattdessen
schreibt ein Lauf alle 100 Dokumente seinen Zwischenstand nach `runs.stats`: Die Zahl der bisher
verarbeiteten Dokumente ist eine Tatsache, eine Prozentzahl wäre eine Behauptung.

Läufe lassen sich noch nicht abbrechen; `RunStatus.CANCELLED` ist vorgesehen, aber nichts setzt
ihn. Der Abbruch braucht einen Weg vom API-Prozess zum Worker und gehört zu Stufe 11.

---

## Stufe 5 — was steht

Bis hierher war die Datenschutzgrenze eine Absprache mit einem Constraint dahinter. Jetzt ist sie
eine Eigenschaft des Systems — und zugleich durchlässig in genau einer Richtung.

### Eine Kante sucht ihren Zielstore, statt ihn anzunehmen

Eine Referenz nennt nur eine ID (`[[confluence:184320]]`), keinen Store. Bis Stufe 4 landete jede
solche Kante im eigenen Store; eine Notiz in `personal`, die auf eine Confluence-Seite zeigte,
blieb dauerhaft `resolved = false`. Jetzt wird gesucht: erst im eigenen Store, dann in den
erlaubten Brückenzielen. Was gefunden wird, bestimmt `to_store`.

Der eigene Store hat Vorrang, und das ist keine Willkür. `[[note:abc]]` in einer persönlichen
Notiz meint die persönliche Notiz — nicht ein gleichnamiges Konzept anderswo.

Findet sich die ID nirgends, entsteht die Kante trotzdem, mit dem eigenen Store als Ziel und
`resolved = false`. Etwas anderes wäre eine Behauptung: Wo ein noch nicht synchronisiertes Objekt
einmal liegen wird, weiß in diesem Augenblick niemand.

### Die Richtung steht an einer Stelle

`domain/bridges.py` beantwortet drei Fragen, die einander nie widersprechen dürfen: wohin eine
Referenz zeigen darf, welche fremden Stores beim erneuten Auflösen befragt werden, und wo beim
Traversieren die Gegenrichtung liegt. §12.1 gibt die Regel vor — `personal → shared` ist erlaubt,
`shared → personal` nicht.

Der Grund für die Asymmetrie ist kein technischer. Wüsste der geteilte Store von persönlichen
Konzepten, stünde die *Existenz* einer privaten Notiz in der Datenbank, die eines Tages auf einem
zentralen Server liegen soll (§5.1). Leitprinzip 2 wäre gebrochen, noch bevor ein einziges
Inhaltsfeld die Grenze überquert.

### Die offene Frage wird nachträglich beantwortet

Der häufigere Fall im Alltag ist der umgekehrte: Jemand schreibt eine Notiz mit
`[[confluence:184320]]`, **bevor** Confluence das erste Mal synchronisiert wurde. Die Kante
entsteht unaufgelöst mit dem eigenen Store als Ziel. Taucht das Objekt später drüben auf, wird sie
angehängt — `to_store` wandert nach `shared`, `resolved` wird wahr, und die Notiz selbst wurde
dafür nicht angefasst.

Das ist zulässig, weil eine unaufgelöste Kante über ihren Zielstore nie etwas *behauptet* hat.
Ihn jetzt zu setzen nimmt nichts zurück; es beantwortet, was beim Anlegen niemand wissen konnte.

Der Abgleich läuft in beide Richtungen, und beide werden gebraucht:

- `refresh_edge_resolution(store)` prüft alles, was in diesem Store **beginnt**.
- `refresh_bridges_into(store)` prüft die Brücken, die auf diesen Store **zeigen**.

Ohne die zweite bliebe eine persönliche Kante nach einem Confluence-Sync falsch beschriftet — sie
liegt ja gar nicht im geänderten Store, und einen Schreibvorgang in ihrem eigenen kann es lange
nicht geben. Jeder Sync-Lauf ruft sie deshalb am Ende auf und meldet `bridges_resolved` in seiner
Statistik.

### Ein Grabstein macht Kanten unauflösbar, nicht unsichtbar

Stufe 4 rührte beim Setzen eines Grabsteins bewusst keine Kante an. §7.6 verlangt aber genau das:
„Kanten bleiben bestehen und werden als `resolved = false` markiert." Beides zusammen ergibt die
richtige Regel — die Kante bleibt vollständig erhalten, nur ihre *Auflösbarkeit* endet. Auffindbar
heißt jetzt: vorhanden **und** kein Grabstein.

Damit ist die Rückkehr aus dem Grabstein von selbst erledigt. Wird das Objekt in der Quelle
wiederhergestellt, ist die Kante beim nächsten Abgleich wieder auflösbar; es braucht keine
gespeicherte Erinnerung daran, dass sie es einmal war.

Live nachvollzogen: `confluence:100003` wird zum Grabstein, seine drei eingehenden Kanten —
darunter zwei Brücken aus `personal` — stehen weiter da und melden „nicht auflösbar"; nach der
Wiederherstellung sind alle drei wieder aufgelöst, keine einzige ging verloren.

### Ein nur lesender Zugang, der es in der Datenbank ist

§20.1 verlangt als fünften Guard-Test, dass die MCP-Verbindung auf `shared` „bei jedem
Schreibversuch einen **Datenbankfehler**" erzeugt. Das Wort ist der Punkt: Eine Prüfung im
Anwendungscode wäre nur so gut wie der Codepfad, der sie aufruft.

`StoreRegistry.readonly_engine()` liefert deshalb eine Verbindung mit erzwungenem
`default_transaction_read_only`. Jede schreibende Anweisung scheitert in PostgreSQL selbst. Wer es
strenger will, hinterlegt unter `stores.<name>.readonly_dsn` eine eigene Datenbankrolle — die Form
für den Betrieb, weil sie auch dann hält, wenn der Prozess selbst kompromittiert ist. Der
Sitzungsschalter ist die Voreinstellung: schwächer, aber ohne jede Einrichtung vorhanden.

### `wg doctor` prüft jetzt die Grenze

Drei Fragen je Store, und alle drei lassen sich nur am laufenden System beantworten: Steht
`ck_shared_no_personal_ref` dort, wo er hingehört, und **nur** dort? Gibt es trotzdem Kanten über
die Grenze? Ist der lesende Zugang wirklich nur lesend? Ein Fehlschlag ist hier ein Fehler und
keine Warnung — bei allem anderen kostet ein Fehlalarm Aufmerksamkeit, hier kostet ein übersehener
Befund die Datenschutzgrenze.

### Zwei neue Kommandos

`wg concepts add` und `wg concepts show`. Sie kommen früher als geplant, weil die Abnahme der
Stufe 5 einen Weg braucht, ein Brücken-Konzept anzulegen und es „in beide Richtungen auffindbar"
zu zeigen; die entsprechenden API-Endpunkte sind Stufe 11. Ein Brücken-Konzept ist dabei nichts
Besonderes: ein `Project` im Scope `personal` mit Verweisen nach `shared`.

### Die fünf Guard-Tests aus §20.1

| # | Guard | Wo |
|---|---|---|
| 1 | Kein Netzzugang beim Öffnen des personal-Stores | `tests/guards/test_datenschutzgrenze.py` |
| 2 | `allow_remote = false` mit fernem DSN verhindert den Start | dieselbe Datei |
| 3 | Router-Aufruf mit `store = personal` gegen fernen Provider wirft | dieselbe Datei |
| 4 | `INSERT` in `shared.edges` mit `to_store = personal` wird abgelehnt | `test_store_invarianten.py` |
| 5 | Schreibversuch über die lesende Verbindung erzeugt einen Datenbankfehler | `test_bruecken_postgres.py` |

Guard 1 ist wörtlich unerfüllbar — eine PostgreSQL-Verbindung *ist* eine ausgehende Verbindung.
Geprüft wird die Aussage dahinter: Gesperrt wird jede Verbindung, die der Python-Prozess selbst
aufbaut; der Store bleibt über libpq erreichbar, dessen Socket in C entsteht. Ein Adapter, ein
Modell-Provider oder eine Telemetriebibliothek, die sich hier einschlichen, ließen den Test sofort
scheitern.

Guard 3 braucht den Router aus Stufe 7. Die Regel, die er einhalten muss, steht deshalb jetzt
schon als `domain/policies.py` im Kern — ohne Netzwerk, ohne Konfigurationsdatei, ohne
Provider-Objekt. Der Router wird sie aufrufen; er wird sie nicht nachbilden.

### Abnahme (§24, Stufe 5)

| Kriterium | Ergebnis |
|---|---|
| Alle fünf Guard-Tests grün | 24 Tests in `tests/guards/`, davon 8 neu |
| Brücken-Konzept verlinkt auf `shared` und ist in beide Richtungen auffindbar | live: `project:finance-integration` → `confluence:100003`; die Gegenrichtung erscheint in `wg concepts show confluence:100003`, während der geteilte Store **null** Kanten über die Grenze führt |

### Ausdrücklich außen vor

Föderation über mehrere Menschen, so vorgesehen in §24.

---

## Stufe 6 — was steht

Der Graph wird aus eigener Perspektive lesbar: `services/graph.py` mit Traversierung, Dichte,
Ranking und lexikalischer Suche.

### Die Traversierung arbeitet auf Kanten, nicht auf Konzepten

§12.1 skizziert je Hop einen Batch-Load der Konzepte. Umgesetzt ist es anders: Das Ausbreiten
braucht nur, was in den Kanten steht — Herkunft, Ziel, Store, Art. Die Konzepte werden **einmal am
Ende** geladen, ein Stapel je Store. Der Unterschied ist nicht nur Sparsamkeit: Ein Batch-Load je
Hop lüde Konzepte, die `max_nodes` gleich darauf wieder verwirft.

Ein Knoten ist dabei erst mit seinem Store eindeutig. Dieselbe ID kann es in beiden Datenbanken
geben, und sie meint dann nicht dasselbe (§12.1, Schritt 5).

### Der Preis der Trennung ist eine Abfrage, kein Join

Die Rückrichtung einer Brücke liegt nicht im Zielstore. Wer von einer Confluence-Seite aus wissen
will, welche eigenen Notizen auf sie zeigen, muss den *persönlichen* Store fragen — auch wenn dort
kein einziger Knoten der aktuellen Front liegt.

Damit das nicht jeden Hop kostet, fragt die Traversierung **einmal vorab**, wohin dieser Bestand
überhaupt Brücken schlägt. Die Antwort ist eine Menge von IDs und gilt für den ganzen Lauf; ein
rein geteilter Graph kostet danach keine einzige Abfrage an den persönlichen Store.

Gemessene Kosten:

| Fall | Abfragen |
|---|---|
| 3 Hops innerhalb eines Stores | 5 (1 Index + 3 Kantenrunden + 1 Batch-Load) |
| 3 Hops über die Brücke | bis 9 (1 + 3 × 2 + 2) |

Die Abnahme aus §24 („höchstens 6 Datenbankabfragen") ist damit im Ein-Store-Fall erfüllt und im
store-übergreifenden Fall überschritten. Das ist keine Nachlässigkeit, sondern die Rechnung, die
§12.1 selbst aufmacht: „ein Query pro Store und Hop". Über die Grenze gibt es keinen Join, und
zwei Datenbanken kosten zwei Abfragen. Die Zahl steht deshalb **im Ergebnis** (`queries`) und
nicht nur in einem Kommentar — eine zugesicherte Eigenschaft, die niemand messen kann, ist keine.

### Referenzdichte zählt den eigenen Bestand

`density(z)` ist die Zahl der Konzepte aus `personal`, die innerhalb von *d* Hops auf `z` zeigen
— berechnet als Rückwärtssuche auf dem tatsächlich aufgelösten Teilgraphen, nicht global. Zwei
Menschen bekommen für dasselbe globale Dokument verschiedene Werte; genau das ist der Zweck
(§12.2).

Ein Schritt entlang einer `member`-Kante zählt dabei **nicht** als Hop. §12.2 sagt „auf z oder auf
ein Cluster von z", und ohne diese Ausnahme wäre der Zusatz bei `d = 1` wirkungslos: Ein Cluster
ist keine Zwischenstation, sondern eine andere Adresse für dieselbe Sache.

Dass die Dichte nur auf dem aufgelösten Teilgraphen zählt, ist ebenfalls Absicht. Ein Wert, der
über nicht Abgefragtes urteilte, wäre geraten.

### Ranking und Suche

Die Formel aus §12.3 unverändert: Nähe, normierte Dichte, Aktualität mit Halbwertszeit. Normiert
wird durch den größten Wert *dieser Antwort* — eine globale Normierung bräuchte einen Bezugswert
über den ganzen Bestand, und der änderte sich mit jedem Lauf. Die Gewichte sind je Anfrage
überschreibbar, damit sich Varianten vergleichen lassen. Bei gleichem Wert entscheidet die ID:
Zwei Aufrufe über denselben Bestand sollen dieselbe Reihenfolge liefern.

Die Suche ist die Dokument-Ebene aus §12.4: `search_tsv` für Volltext, `pg_trgm` für den
vertippten Titel, zusammengeführt über Reciprocal Rank Fusion. Über die *Plätze* und nicht über
die Werte — ein `ts_rank` von 0,08 und eine Ähnlichkeit von 0,42 sagen nichts übereinander. Der
Modus steht im Ergebnis (`mode: "lexical"`), weil ein stiller Qualitätsverlust die schlechtere
Variante wäre.

Grabsteine erscheinen weder in der Suche noch — ohne ausdrückliches Flag — in einer Traversierung.
Traversiert wird trotzdem über sie hinweg: Ein Grabstein ist unsichtbar, aber nicht abwesend,
sonst zerfiele der Graph an jeder gelöschten Seite.

### Eine neue Schutzregel

**„Lesepfad und Schreibpfad sind unabhängig"** (import-linter, damit neun Verträge). Wer den
Graphen abfragt, soll das auch dann können, wenn an der Sync-Orchestrierung etwas kaputt ist. Die
Trennung ist zugleich die Voraussetzung dafür, dass der MCP-Server (Stufe 12) nur den Lesepfad
einbindet und den Schreibpfad gar nicht erst mitbringt (§18.3).

### Abnahme (§24, Stufe 6)

| Kriterium | Ergebnis |
|---|---|
| Ein nur über eine Brücke erreichbares Konzept erscheint mit kurzer Distanz | live: `project:finance-integration` liegt einen Hop von `confluence:100003` entfernt, über zwei Datenbanken hinweg |
| Identische Zielkonzepte erhalten bei unterschiedlicher lokaler Struktur unterschiedliche Dichtewerte | im Integrationstest und live: Dichte 2 gegen 0 bei sonst gleichem Konzept |
| Ein Traversal über 3 Hops braucht höchstens 6 Datenbankabfragen | erfüllt innerhalb eines Stores (5); über die Store-Grenze bis 9, siehe oben |

### Ausdrücklich außen vor

Embeddings und Vektorsuche, so vorgesehen in §24 — sie kommen mit Stufe 8, und mit ihnen die
Cluster-Ebene der zweistufigen Suche (§12.4).

`wg graph overview` aus §19 fehlt ebenfalls. Es ist eine Bestandsübersicht und gehört zu den
Ansichten der Stufe 11; die Abnahme der Stufe 6 verlangt es nicht.

---

## Stufe 7 — was steht

**Zweck (§24):** Modellzugriff an genau einer Stelle, austauschbar per Konfiguration.

### Abweichung vom Dokument: LangChain statt eigener Provider-Hüllen

§11.4 zählt Provider-Typen auf und überlässt offen, womit sie angesprochen werden. Umgesetzt ist
das über **LangChain** — auf ausdrücklichen Wunsch und aus einem Grund, der über Bequemlichkeit
hinausgeht: §11.1 verlangt, dass ein Modellwechsel eine Änderung in `models.yaml` ist,
*einschließlich eines anderen Anbieters*. Mit eigenen Hüllen wäre jede neue Integration neuer
Code. So sind `ChatGoogleGenerativeAI` und `ChatOpenAI` zwei Zeilen derselben Fallunterscheidung,
und `openai_compatible` deckt Ollama und vLLM ohne eine weitere ab.

**Was LangChain hier ausdrücklich nicht tut:** Es routet nicht, wiederholt nicht, speichert nicht
zwischen und validiert keine Struktur. All das liegt im Router, weil es dort protokolliert,
budgetiert und geprüft werden muss. Die Clients werden deshalb mit `max_retries=0` gebaut — zwei
Wiederholungsmechanismen übereinander ergäben im ungünstigen Fall das Produkt beider
Versuchszahlen, und keiner der zusätzlichen Aufrufe stünde in `model_calls`.

### Die zwei Ebenen

| Ebene | Datei | Kennt |
|---|---|---|
| Was die Dienste sehen | `ports/models.py` (`ModelRouter`) | eine **Aufgabe** und einen **Store** — nie ein Modell |
| Was ein Anbieter erfüllt | `ports/models.py` (`ChatClient`, `EmbeddingClient`) | einen Aufruf und seine Antwort |
| Dazwischen | `services/router.py` | Policy, Budget, Cache, Retries, Fallback, Protokoll |
| Das SDK | `infrastructure/models/langchain.py` | als einziges Modul überhaupt ein Modell-SDK |

Diese Trennung ist der Grund, warum kein einziger Test der Stufen 7 bis 10 ein echtes Modell
braucht: `wissensgraph/testing/models.py` erfüllt die untere Ebene, und die obere ist davon
unberührt.

### Die Reihenfolge der Prüfungen

**Policy → Cache → Budget → Aufruf.** Keine der drei Stellen ist beliebig:

- Die **Policy zuerst**, weil ihre Verletzung als einzige nicht rückgängig zu machen ist: Ein
  Inhalt, der den Rechner verlassen hat, ist draußen (§11.5).
- Der **Cache vor dem Budget**, weil ein Treffer nichts kostet. Ein Wächter, der Treffer
  mitzählte, brächte einen Wiederholungslauf zum Erliegen, obwohl er nichts verbraucht.
- Das **Budget zuletzt und vor dem Aufruf**. Eine Grenze, die nach der Antwort greift, hat sie
  schon bezahlt.

Ein Policy-Verstoß führt **nie** zu einem Fallback. §11.5 ist dort ausdrücklich: nie "ein stiller
Fallback auf einen erlaubten, aber schlechteren Anbieter".

### Die Datenschutzgrenze hat zwei Hälften

`domain/policies.py` entscheidet beide, ohne Netzwerk und ohne Provider-Objekt:

- `check_store_policy` — die **Ortsregel**: Persönliche Inhalte nur an einen als `local: true`
  deklarierten Anbieter. `WG_PERSONAL_ALLOW_REMOTE_MODELS=true` weicht sie bewusst auf und wird
  protokolliert.
- `check_allowed_providers` — die **Freigabeliste** aus `policies.<store>.allowed_providers`. Eine
  *fehlende* Liste erlaubt alles, eine *leere* nichts; der Unterschied steht im Rückgabetyp.

Ein Verstoß hinterlässt einen `model_calls`-Eintrag mit `budget_denied` — so verlangt es §11.5
wörtlich. Der Wert wirkt schief und ist richtig: Aus Sicht der Abrechnung ist ein Aufruf, der
nicht stattfinden *darf*, derselbe Vorgang wie einer, der nicht mehr stattfinden *kann*.

### Abnahme (§24, Stufe 7)

| Kriterium | Ergebnis |
|---|---|
| Ein Modellwechsel in `models.yaml` wirkt ohne Codeänderung und ohne Neubau des Images | erfüllt — `config/` ist read-only gemountet, `wg models describe` zeigt die neue Route |
| Ein Aufruf mit `store = personal` gegen einen nicht-lokalen Provider wirft | live: `wg embed --scope personal` endet mit `skipped_policy: 1`, kein Byte geht hinaus |
| Ungültiges JSON löst genau **einen** Reparaturversuch aus | Unit-Test zählt die Aufrufe: zwei, nicht drei |
| Ein wiederholter identischer Aufruf ist ein Cache-Treffer | live gegen Redis: zwei Aufrufe, **eine** HTTP-Anfrage, 21 gegen 0 Token |
| Ein Budgetüberschritt beendet den Lauf sauber mit Teilergebnis | live: `WG_BUDGET_MAX_MODEL_CALLS_PER_RUN=0` → `considered: 200`, `budget_exceeded: True`, Lauf erfolgreich |

### Das Budget ist jetzt eine ENV-Variable

`WG_BUDGET_MAX_MODEL_CALLS_PER_RUN`, `WG_BUDGET_MAX_COST_PER_RUN_EUR` und
`WG_BUDGET_ON_EXCEED` stehen als Platzhalter in `wissensgraph.yaml`. §6.2 zählt Schwellen und
Limits ausdrücklich zu den laufbezogenen Parametern; auf einem Entwicklerrechner will man den
Rahmen eng ziehen, im Betrieb weit. **`0` schaltet jeden Modellaufruf ab** und ist damit der
sicherste Weg, eine Pipeline einmal vollständig durchlaufen zu lassen, ohne ein Token zu
verbrauchen.

---

## Stufe 8 — was steht

**Zweck (§24):** Die semantische Schicht, auf der Cluster, Suche und Vernetzung aufsetzen.

### Der Content-Hash bleibt beim Erzeugen einer Beschreibung stehen

§13.1 lässt eine fehlende `description` einmalig über Task `summarization` erzeugen. Der
`content_hash` wird dabei **nicht** neu berechnet, und das ist mehr als ein Detail: Er ist der
Hash des *Quellinhalts* (§10.3) und beantwortet die Frage „hat sich die Quelle geändert?". Eine
hier erzeugte Beschreibung ist keine Quelländerung. Würde sie den Hash verschieben, meldete der
nächste Sync eine Änderung, überschriebe die Beschreibung mit `NULL`, und der nächste
Embedding-Lauf erzeugte sie neu — ein Kreislauf, der bei jedem Lauf Token kostet und nichts
verbessert.

### Cluster-Identität über Läufe hinweg

Die Stabilitätsschwelle aus §13.3 hat eine Folge, die im Dokument nicht ausgeschrieben steht: In
den Läufen bis zum Erreichen der Schwelle hat ein Cluster **keine einzige Kante**. Eine Zuordnung
allein über geschriebene `member`-Kanten fände deshalb im zweiten Lauf keine Überschneidung, legte
ein zweites Cluster an — und die Schwelle wäre nie zu erreichen, weil jeder Lauf von vorn begänne.
`_bestehende_cluster` zählt darum Kanten **und** Kandidaten. Die Kandidatentabelle *ist* die
Erinnerung an eine vorläufige Zuordnung.

Die ID eines neuen Clusters ist zufällig und nicht aus seinen Mitgliedern abgeleitet. Eine
abgeleitete ID wäre verlockend und falsch: Sie änderte sich mit jeder Mitgliederänderung, und
damit wäre kein Cluster über zwei Läufe hinweg dasselbe.

### Was Code entscheidet und was das Modell entscheidet

| Schritt | Wer |
|---|---|
| Welche Konzepte zusammengehören | **Code** — k-nächste Nachbarn, Kantenschwelle, Zusammenhangskomponenten |
| Wie die Gruppe heißt | **Modell** — Task `cluster_labeling` |
| Ob die Zuordnung geschrieben wird | **Code** — die Stabilitätsschwelle aus §13.3 |

Deshalb tragen `member`- und `related`-Kanten die Kennung `code:clustering` beziehungsweise
`code:cluster-similarity` und **nicht** die Provenienz des Modells: Wären beide gleich benannt,
verlöre ein Lauf nach einem Modellwechsel seine eigenen Mitgliedskanten aus den Augen (§10.4).

### Eine neue Migration

`0002_cluster_exclusions` ergänzt `cluster_assignment_candidates` um `excluded`. §13.4 verlangt
für ein von Hand entferntes Mitglied einen Ausschlussvermerk, und der brauchte einen Ort: Eine
gelöschte `member`-Kante hinterlässt nichts. Ohne Vermerk fände der nächste Lauf dieselbe Nähe
wieder und schriebe dieselbe Zuordnung — die Handarbeit wäre nach einem Lauf verschwunden, und das
ist genau der Fall, den Leitprinzip 15 ausschließt.

### Abnahme (§24, Stufe 8)

| Kriterium | Ergebnis |
|---|---|
| Die drei Themenfelder des Korpus ergeben mindestens drei Cluster | erfüllt — Unit-Test: genau drei; live über den Mock-Korpus: 20 Cluster aus 200 Konzepten |
| Das Grenzdokument landet stabil | erfüllt — es berührt zwei Themen und bleibt über drei Läufe in demselben Cluster |
| Mindestens zwei Cluster sind über `related` verbunden | erfüllt — live 60 `related`-Kanten zwischen 20 Clustern |
| Eine Zuordnung entsteht erst im zweiten Lauf | erfüllt — live: Lauf 1 `candidates: 171`, `members_added: 0`; Lauf 2 `members_added: 171` |
| Eine kuratierte Zuordnung überlebt zwei Läufe | erfüllt — `curated = true` bleibt unangetastet (§10.4) |
| Ohne verfügbares Embedding-Modell degradiert die Suche sichtbar auf `mode: lexical` | erfüllt — live alle drei Modi beobachtet: `lexical`, `cluster`, `hybrid` |

### Die zweistufige Suche in Zahlen

Live gegen Gemini-Embeddings, gemessene Ähnlichkeit zwischen Anfrage und Zentroid:

| Anfrage | bester Zentroid | Modus |
|---|---|---|
| „Wie läuft der nächtliche ETL-Lauf?" | 0,775 | `cluster` — über der Schwelle 0,75 |
| „Data Warehouse ETL" | 0,747 | `hybrid` — knapp darunter, Rückfall auf die Dokumentebene |
| „Kaffeemaschine entkalken" | — | `hybrid` |

Die Schwelle `search.cluster_hit_threshold` sitzt damit dort, wo sie sitzen soll: Eine klar
gestellte Frage bekommt ein Thema, eine vage bekommt Dokumente.

---

## Stufe 9 — was steht

**Zweck (§24):** Typisierte Beziehungen statt bloßer Nähe.

### „Keine Beziehung" steht im Prompt

§14.2 Schritt 4 sagt es, und der Prompt sagt es dem Modell ebenso ausdrücklich. Ohne diesen Satz
erfindet ein Sprachmodell zuverlässig einen Zusammenhang, weil die Frage einen nahelegt — und der
Graph füllt sich mit Kanten, die niemand nachvollziehen kann.

### Der Vorfilter, und was er live wirklich leistet

§14.5 nennt `relations.min_pair_similarity` als wichtigsten Kostenhebel. Live über den
Mock-Korpus zeigt sich: **Er filtert weniger, als das Dokument annimmt.** Von 1162 Paaren fielen
bei der Vorgabe 0,60 nur 8 weg — Gemini-Embeddings zweier Dokumente desselben Clusters liegen fast
immer über 0,60. Der wirksame Schutz war der Budget-Wächter, der den Lauf nach 51 Aufrufen sauber
mit einem Teilergebnis von 31 Kanten beendete.

Das ist eine Beobachtung über die *Kalibrierung*, nicht über das Verfahren: Wer diesen Schritt
regelmäßig laufen lässt, sollte `relations.min_pair_similarity` deutlich höher ansetzen — die
Größenordnung 0,80 bis 0,85 entspricht bei diesem Modell dem, was 0,60 im Dokument meint.

### `supersedes` wirkt nicht

§14.4: Eine erkannte Ablösung setzt **nicht** `status = 'deprecated'`. Die Kante wird geschrieben,
die Folge zieht ein Mensch — als `change_log`-Eintrag mit `vorschlag: deprecate`. Ein Konzept auf
Verdacht eines Modells stillzulegen widerspräche Leitprinzip 6 und wäre schwer zurückzunehmen: Ein
`deprecated` steht in jeder Ansicht und in jedem Export.

### Abnahme (§24, Stufe 9)

| Kriterium | Ergebnis |
|---|---|
| Im Testcluster entsteht mindestens eine typisierte Kante mit nachvollziehbarer Provenienz | live 31 Kanten: `references` 15, `depends_on` 10, `extends` 5, `implements` 1 — alle mit `gemini:gemini-3.5-flash-lite/relation_extraction@v1` |
| Die Mehrheit der Paare liefert „keine Beziehung" | live 20 von 51 Aufrufen; im Unit-Test bei neutralem Skript alle |
| Ein Wiederholungslauf erzeugt fast ausschließlich Cache-Treffer | erfüllt — Unit-Test: `wieder.cached == wieder.calls`; live gegen Redis für einen Einzelaufruf bestätigt |
| Kein Konzept wird automatisch deprecated | live: 0 Konzepte mit `status = 'deprecated'` |

---

## Stufe 10 — was steht

**Zweck (§24):** Die Fälle einfangen, die Clustering nicht nebeneinanderstellt.

### Erst Code, dann Modell — und das ist keine Reihenfolge, sondern der Kern

Live über den Mock-Korpus, mit `--loose-threshold 2` und **ohne einen einzigen Modellaufruf**:

| Schritt | Ergebnis |
|---|---|
| Stufe 1a, Textabgleich (§15.2a) | 33 Kanten, `confidence: 1.0`, `generated_by: code:text-match` |
| Stufe 1b, Nähe über `proximity_auto_commit` (§15.2b) | 898 Kanten, `generated_by: code:embedding-proximity` |
| Stufe 2, Modell | gar nicht erst erreicht |

931 Kanten für null Token. Genau das meint §15.2a mit „Die Übereinstimmung ist der Beleg; ein
Modell wäre hier reine Verschwendung."

**Auch hier eine Kalibrierungsbeobachtung:** 898 Kanten aus dem Auto-Commit sind für 37 lose
Knoten viel. Die Vorgabe `proximity_auto_commit: 0.85` aus §15.2 ist für ein anderes
Embedding-Modell gedacht; bei `gemini-embedding-2` laufen die Ähnlichkeiten höher. Wer den Lauf
regelmäßig fährt, zieht die Schwelle nach oben — der Wert ist genau dafür ein Config-Eintrag und
ein CLI-Flag.

### Die Musterdateien

`config/patterns/*.yaml` — Regex-Muster für Vorgangsschlüssel, Seiten-IDs und
Architekturentscheidungen. Sie stehen in Dateien und nicht im Code, weil sie zum *Unternehmen*
gehören und nicht zum System: Wie ein Jira-Key aussieht, weiß niemand außerhalb der jeweiligen
Organisation. Ein nicht übersetzbarer Ausdruck ist ein **Startfehler** (§6.5) — er soll auffallen,
bevor er mitten in einer nächtlichen Vernetzung auffällt.

Der Index über alle Treffer wird **einmal** je Lauf gebaut und nicht je losem Knoten neu. Ohne ihn
wäre ausgerechnet der Schritt quadratisch, dessen Vorzug seine Billigkeit ist.

### Aufruf B ist dieselbe Methode wie §14

§15.3 verlangt für die Paarprüfung „identisches Format zu §14.3". Statt es dort noch einmal zu
bauen, ruft `OrphanService` die Methode `RelationService.check_pairs` auf. Ein zweites
Prompt-Format für dieselbe Frage wäre die Stelle, an der die beiden Läufe auseinanderdriften.

### Abnahme (§24, Stufe 10)

| Kriterium | Ergebnis |
|---|---|
| Der isoliert angelegte Knoten wird gefunden | erfüllt — `v_loose_concepts` zählt `member` bewusst nicht mit (§7.7) |
| Mit `--use-llm false` entstehen nur Stufe-1-Kanten | erfüllt — live `calls: 0`, `model_edges: 0`, 931 Code-Kanten |
| Mit `true` mindestens eine Modellkante über der Schwelle | live: `model_edges: 4` |
| Ein Knoten ohne passendes Cluster erzeugt nachvollziehbar ein neues Cluster | erfüllt — mit Provenienz, `verified_by = NULL`, plus `member`-Kante (§15.3) |
| Die Zahl loser Knoten sinkt über aufeinanderfolgende Läufe | live: `loose_before: 1` → `loose_after: 0` |

### Ausdrücklich außen vor

Periodisches Scheduling, so vorgesehen in §24. Die vier Läufe sind als `RunKind` in der Queue
angemeldet und laufen über den Worker; was fehlt, ist allein der Auslöser über die Zeit.

---

## Stufe 11 — was steht

Die vollständige HTTP-API nach §16, ein aus dem OpenAPI-Schema erzeugter TypeScript-Client, alle
sechs Ansichten aus §17.2, der Kurationsdienst samt Undo, Server-Sent Events für den
Lauf-Fortschritt und Playwright-Tests der Kernflüsse.

### Löschen und Verwerfen sind zwei verschiedene Dinge

Der Unterschied zwischen `DELETE /edges/{id}` und `POST /edges/{id}/reject` ist der Kern dieser
Stufe und keine Geschmacksfrage:

- **Löschen** heißt "hier gehört sie nicht hin". Die Kante verschwindet, mehr nicht.
- **Verwerfen** heißt "diese Beziehung gibt es nicht". Die Kante verschwindet **und** ihr Tripel
  wird als Negativ vermerkt.

Nur das zweite bindet einen Folgelauf, und genau das verlangt §24: "der verworfene entsteht im
Folgelauf nicht neu". Ohne den Vermerk fände die Kantenerkennung dasselbe Paar mit derselben
Ähnlichkeit, fragte dasselbe Modell und bekäme dieselbe Antwort — die Kuration wäre nach einem
Lauf verschwunden.

Dafür kam **Migration `0003_edge_rejections`** dazu. Der Schlüssel ist das Kantentripel aus §7.4
und nicht die `edge_id`: Die ID verschwindet mit der Kante, das Tripel ist das, was ein Folgelauf
erneut erzeugen würde. Die Richtung gehört zum Schlüssel — wer `a depends_on b` verwirft, hat über
`b depends_on a` nichts gesagt, und §17.2 bietet dafür ausdrücklich "Richtung umdrehen" an.

Der Vermerk wirkt an zwei Stellen und beide Male **vor** dem Modellaufruf: in
`RelationService._vorfiltern` (§14.5, "Verarbeitung nur neuer/geänderter Paare") und in
`OrphanService._nahe_kante`. Ein verworfenes Paar kostet damit bei keinem Folgelauf einen weiteren
Token.

### Was ein Undo kann — und was nicht

§17.3 verlangt: "Jede Kuration ist rückgängig machbar (Undo über den `change_log`-Eintrag)."
Umgesetzt ist das für **Strukturentscheidungen**: Kanten anlegen und entfernen, Mitgliedschaften,
Bestätigen, Verwerfen, Anlegen eines Konzepts, Statuswechsel.

**Nicht** umkehrbar ist eine inhaltliche Änderung an Titel, Beschreibung oder Fließtext, und der
Endpunkt sagt das mit `409` und einer Begründung, statt es zu versuchen und stillschweigend nur
die Hälfte wiederherzustellen. Der Grund steht im Journal selbst: Es hält Feldnamen fest, keine
Werte (§7.4, §21.1). Den alten Text dort abzulegen hieße, Inhalte an einer zweiten Stelle zu
führen — und aus einem Journal, das heute gefahrlos exportierbar ist, würde eines, das es nicht
mehr ist.

Rückgängig gemacht wird immer ein *bestimmter* Eintrag und nie "die letzte Änderung": In einem
System, das nebenher synchronisiert, clustert und Kanten erkennt, wäre "die letzte" selten die
gemeinte. Dafür trägt `ChangeEntry` seit dieser Stufe die `id` der `BIGSERIAL`-Spalte, und
`append()` gibt den geschriebenen Eintrag zurück — die Oberfläche kann ein Undo damit sofort
anbieten, ohne die Historie erneut zu laden.

### Gesperrt heißt sichtbar gesperrt

§17.3: "Inhaltsfelder quellgespiegelter Konzepte sind sichtbar gesperrt, nicht nur
schreibgeschützt." *Sichtbar* ist der Punkt — die Oberfläche muss es wissen, **bevor** jemand
tippt, und darf es nicht erst an einer abgelehnten Anfrage merken.

Deshalb liefert `GET /concepts/{id}` ein Feld `locked_fields`, und die Oberfläche liest es, statt
die Regel nachzubauen (§17.1). Maßgeblich ist die Herkunft des einzelnen Konzepts und nicht allein
die Taxonomie: Eine lokal angelegte Notiz vom Typ `Note` ist frei, dieselbe ID mit `source_name`
wäre es nicht.

### Der Lauf entsteht vor dem Job

Alle `POST /runs/*` antworten mit `202 Accepted`, einer Run-ID und `Location`. Die Zeile in `runs`
entsteht dabei **zuerst**, der Job ist nur ein Verweis darauf (§16.3). Das ist der Grund, warum die
Oberfläche einen Lauf abonnieren kann, bevor der Worker ihn überhaupt entnommen hat — und warum
"Läufe blockieren die UI nie" (§17.3) eine Eigenschaft des Aufbaus ist und keine
Absichtserklärung.

`Runtime._verbuchter_lauf` nimmt dafür eine `run_id` entgegen: Der Worker führt den *vorhandenen*
Lauf aus, statt einen zweiten anzulegen. Ohne das verfolgte die Oberfläche einen Lauf, der nie
startet, während ein anderer unbemerkt arbeitet. Ein vor dem Start abgebrochener Lauf wird dabei
erkannt und gar nicht erst ausgeführt.

`GET /runs/{id}/events` liefert Server-Sent Events und schickt nur bei *Änderung* — ein Strom, der
jede Sekunde denselben Zustand wiederholt, ist für den Empfänger nicht von Fortschritt zu
unterscheiden. Er endet mit dem Lauf, bei Verbindungsabbruch des Clients und spätestens nach einer
Stunde.

### Zwei neue Dienste

- **`services/catalog.py`** — der Lesepfad: Listen, Detailansichten, Cluster, Warteschlange,
  Zahlen, Läufe, Modellnutzung. Er schreibt nichts, und das ist keine Formalie: Der Lesepfad hängt
  damit an keiner Transaktion, die ein Lauf hält.
- **`services/curation.py`** — der Schreibpfad des Menschen. Jede Operation setzt `curated = true`
  (sonst hätte eine Handbewegung die Lebensdauer eines Laufs, §10.4) und hinterlässt einen
  Journaleintrag mit `actor`.

Cluster sind dabei keine eigene Gattung, sondern Konzepte vom Typ `Cluster` mit `member`-Kanten
(§13.2). Der Cluster-Arbeitsplatz bekommt deshalb keine zweite Datenquelle, sondern denselben
Graphen aus einem anderen Blickwinkel.

### Die Oberfläche enthält keine Fachlogik

§17.1 verlangt es, und es ist überprüfbar: Scopes, Konzepttypen, Kantenarten und die Store-Liste
kommen aus `/api/v1/config/effective`, die Sperren aus `locked_fields`, die Modellrouten aus
`/api/v1/models`. Solange die Konfiguration nicht geladen ist, zeigt die SPA **keine** Ansicht —
nicht aus Vorsicht, sondern weil sie ohne sie nicht wüsste, was es gibt. Eine Vorbelegung wäre
genau die eingebaute Fachregel, die dort ausgeschlossen ist.

Der lokale Zustand steht in der URL (§17.1): Eine Kuration besprechen heißt, jemandem *dieselbe*
Ansicht zu zeigen.

### Der TypeScript-Client ist erzeugt — teilweise

`scripts/generate_client.py` liest das OpenAPI-Schema direkt aus der Anwendung (kein laufender
Server, keine Datenbank, keine Zugangsdaten) und schreibt `ui/src/api/schema.ts`.

Erzeugt werden die **Eingabeformen** und die Liste der Pfade. Die **Antwortformen** nicht: Sie
entstehen in den Diensten als `as_dict()` und stehen deshalb nicht vollständig im Schema; sie von
dort abzuleiten hieße, eine Genauigkeit zu behaupten, die es nicht gibt. Sie stehen von Hand in
`ui/src/api/types.ts` und sagen damit aus, was die Oberfläche tatsächlich benutzt.

### Abweichungen und Ergänzungen

- **`GET /graph/loose`** ist ein Endpunkt mehr als §16.2 nennt. Die Graph-Ansicht führt "nur lose
  Knoten" als eigene Ebene (§17.2), und sie soll dieselbe Definition benutzen wie der Lauf aus §15
  — sonst zeigte die Oberfläche etwas anderes an, als der Lauf bearbeitet.
- **`GET /curation/journal`** ebenso: §17.3 verlangt Nachvollziehbarkeit, und die Betriebsansicht
  braucht dafür den Journalstrom eines Stores, nicht nur den eines Konzepts.
- **`traverse` bekam `kinds` und `stores`.** §16.2 führt beide im Anfragekörper; der Graphdienst
  kannte sie noch nicht. Der Filter wirkt beim *Ausbreiten* und nicht am Ergebnis: Wer nur `member`
  verfolgt, soll auch nur erreichen, was über `member` erreichbar ist — sonst zeigte die gefilterte
  Ansicht Knoten ohne sichtbaren Weg dorthin.
- **`ClusterRepository.include`** als Gegenstück zu `exclude` (§13.4): Wer ein entferntes Mitglied
  von Hand wieder hineinzieht, hat seine Entscheidung geändert, und ein stehen gebliebener Vermerk
  hielte es beim nächsten Lauf wieder heraus.
- **Die UI-Coverage-Schwellen sind aufgeteilt.** Zeilen und Anweisungen auf 90 % wie im
  Python-Teil; Funktionen und Zweige auf 80 %. In JSX zählt v8 jeden Render-Rückruf einer Liste als
  eigene Funktion — eine Ansicht mit sechs Tabellen hat Dutzende "Funktionen", die nichts
  entscheiden. Sie auf 90 % zu treiben hieße, Tests zu schreiben, damit ein Zähler steigt.

### Abnahme (§24, Stufe 11)

| Kriterium | Nachweis |
|---|---|
| Graph von einer persönlichen Notiz über mehrere Hops | Playwright, `kernfluesse.spec.ts` |
| Mitglied per Drag-and-Drop verschoben, überlebt den Clustering-Lauf | Playwright **und** `test_ein_verschobenes_mitglied_ueberlebt_den_naechsten_lauf` |
| Vorschlag bestätigt, einer verworfen — der verworfene entsteht nicht neu | `test_der_folgelauf_schreibt_sie_nicht_wieder`, `test_die_paarpruefung_ueberspringt_verworfene_paare` |
| Sync aus der UI gestartet, Fortschritt live | Playwright über echte `EventSource`; `test_api_laeufe.py::TestFortschrittsstrom` |
| Inhaltsfelder gespiegelter Konzepte erkennbar gesperrt | Playwright; `test_gespiegelte_inhaltsfelder_sind_sichtbar_gesperrt` |

Die Drag-and-Drop-Geste und die Zeichenfläche werden im **Browser** geprüft und nicht in jsdom:
Beides hängt an Fähigkeiten, die jsdom nicht hat — ein Canvas für Cytoscape und echte
Zeigerereignisse. Die Playwright-Läufe fangen die API im Browser ab; ob sie *richtig* antwortet,
prüfen die Python-Tests gegen die Dienste.

### Ausdrücklich außen vor

Mehrbenutzer-Auth und ein Rollenmodell, so vorgesehen in §24. `auth_mode: oidc` antwortet
weiterhin mit `501`.

---

## Stufe 12 — was steht

Die sieben Werkzeuge aus §18.1 als dritte dünne Hülle um denselben Kern, eine nur lesende
Verbindung auf `shared`, `actor = 'agent:<session>'` in jedem Journaleintrag und ein Deckel auf
der Antwortgröße.

### Die Beschreibungen sind die Steuerung

§18.2 nennt sie "die einzige wirksame Stelle, an der sich das Verhalten des Agenten steuern
lässt", und das ist keine Übertreibung: Ein Agent liest keine Architekturdokumente, er liest
Werkzeugbeschreibungen.

Deshalb steht die Reihenfolge dort, wo er sie sieht:

- `graph_overview` steht **zuerst** in der Liste und nennt sich selbst "der erste Aufruf einer
  Sitzung"; seine Antwort trägt ein Feld `next_step`, das auf `graph_traverse` verweist.
- `graph_traverse` sagt, dass es nach dem ersten Anker der häufigste Aufruf ist.
- `graph_search` beginnt mit "**Fallback**" und nennt die Bedingung: nur, wenn weder eine
  bestehende Verbindung noch die Übersicht einen Startpunkt liefert.

Das dritte Abnahmekriterium aus §24 — "eine Sitzung beginnt nachweislich mit `graph_overview`
statt mit `graph_search`" — ist damit so weit erfüllt, wie ein Server es erfüllen kann. Was ein
Agent tatsächlich tut, entscheidet sein Modell; was dieses System dafür tun kann, ist die
Reihenfolge in die Beschreibungen zu schreiben und sie prüfbar zu machen. Genau das prüfen die
Tests.

### Der Agent schreibt nur lokal — und das erzwingt die Datenbank

§17.4: "Ein Mensch darf die geteilte Struktur ordnen; ein Agent nicht."

Durchgesetzt wird das **nicht** im Werkzeugcode. Der MCP-Server bekommt eine
Arbeitseinheiten-Fabrik, die für `shared` ausschließlich die nur lesende Verbindung herausgibt
(`UnitOfWorkFactory(registry, readonly_stores={"shared"})`). Ein Schreibversuch scheitert damit in
PostgreSQL, gleichgültig über welchen Codepfad er kommt und ob dieser Code die Regel kennt.

`concept_upsert` hat folgerichtig **kein** `store`-Argument: Ein Agent kann den Store nicht
wählen. `link_add` ebenso wenig ein `from_store` — eine Kante des Agenten beginnt immer im
persönlichen Store, und die Gegenrichtung existiert nicht (§12.1).

Live gegen PostgreSQL:

```
SHARED-SCHREIBEN abgewiesen: InternalError
(psycopg.errors.ReadOnlySqlTransaction) cannot execute INSERT in a read-only transaction
```

Das ist Guard 5 aus §20.1. Er steht als Test in `tests/guards/test_mcp_readonly.py` und zusätzlich
als Betriebsprüfung in `wg doctor` (`agent-readonly`): Dort wird ein Schreibversuch unternommen und
sofort zurückgerollt. Außerhalb von PostgreSQL meldet die Prüfung eine **Warnung** — dort gibt es
die Zusicherung schlicht nicht, und sie als "in Ordnung" auszuweisen wäre die gefährlichere
Auskunft.

### Der Deckel auf der Antwort

§18.3: "Rückgaben sind auf `mcp.max_response_tokens` gedeckelt; überschreitende Ergebnisse werden
gekürzt und mit `truncated: true` markiert."

Gekürzt wird von **hinten**: Listen verlieren ihre letzten Einträge, Texte ihr Ende. Die
Reihenfolge ist wichtig — die vorderen Einträge einer Trefferliste sind die besseren (§12.3), und
ein Fließtext ist von vorn nach hinten verständlich. Von vorn zu kürzen hieße, das Nützlichste
wegzuwerfen.

Der Deckel rechnet über die Zeichenzahl und nicht mit einem Tokenizer: Der Server weiß nicht,
welches Modell ihn fragt. Eine grobe, aber immer verfügbare Grenze ist hier mehr wert als eine
genaue, die nur für einen Anbieter stimmt.

### Der Zuschnitt: Werkzeuge als Daten

`mcp/tools.py` kennt **kein** MCP-SDK. Es beschreibt Werkzeuge als Daten — Name, Beschreibung,
Schema, Aufruf —, und `mcp/server.py` bindet sie an den Transport. Zwei Folgen:

- Die Werkzeuge sind ohne Server prüfbar; die 27 Tests in `test_mcp_werkzeuge.py` brauchen weder
  Datenbank noch Protokoll.
- Ein Wechsel der SDK-Version ist eine Änderung an einer Datei. Das hat sich zweimal ausgezahlt:
  erst beim Wechsel auf die Server-API der installierten Fassung, dann beim Umstieg auf FastMCP
  und Streamable HTTP (siehe unten) — angepasst werden musste jedes Mal nur die Bindung.

### Fehler sind Auskünfte, keine Störungen

Ein `ToolError` ("das Konzept gibt es nicht", "in den geteilten Store darfst du nicht schreiben")
wird zur Antwort mit `is_error: true` und nicht zu einer Protokollausnahme. Er ist eine Auskunft
an den Agenten, und daraus einen Transportfehler zu machen nähme ihm die Möglichkeit, es anders zu
versuchen.

Alles andere fliegt weiter: Ein Programmfehler gehört ins Log und nicht in eine höfliche Meldung.

### Abnahme (§24, Stufe 12) — live gegen PostgreSQL

Gegen den vollständigen Mock-Bestand (220 Konzepte, 20 Cluster, 448 Kanten):

| Kriterium | Ergebnis |
|---|---|
| Notiz anlegen, auf ein `shared`-Cluster verlinken, über `graph_traverse` sofort wiederfinden | `WIEDERGEFUNDEN: True` — 15 Knoten über zwei Hops |
| Schreibversuch auf `shared` scheitert auf Datenbankebene | `ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction` |
| Eine Sitzung beginnt mit `graph_overview` | `graph_overview` steht an erster Stelle, nennt sich den ersten Aufruf und verweist im `next_step` auf `graph_traverse`; `graph_search` nennt sich Fallback |

Die Kante lag danach im **persönlichen** Store; im geteilten Store gibt es sie nicht (§12.1). Der
Journaleintrag trägt `agent:abnahme-12`.

### Ergänzungen am Bestand

- **`SqlUnitOfWork` kennt `readonly`**, und `UnitOfWorkFactory` nimmt eine Menge von Stores
  entgegen, für die das gilt. Ohne diese eine Stelle müsste jeder Aufrufer sich selbst um die
  richtige Verbindung kümmern — und §20.1 verlangt das Gegenteil.
- **`mcp.max_response_tokens`** als Konfigurationswert (§18.3).
- **`wg mcp`** als Startbefehl; der `mcp`-Container führt ihn aus statt eines Platzhalters.
- **`wg doctor`** prüft die Agenten-Verbindung mit.

### Ein Fehler, den die Live-Prüfung gefunden hat

Die Oberfläche erwartete das Suchergebnis unter `nodes`, die API liefert es unter `hits`. In den
UI-Tests fiel das nicht auf, weil die Nachbildung dieselbe falsche Annahme hatte — der Fehler kam
erst beim Aufruf gegen den laufenden Server zum Vorschein. Behoben in `ui/src/api/types.ts` und
`GraphExplorer`; der Unterschied ist jetzt auch benannt: Ein Treffer hat einen Rang, ein Knoten
eine Hop-Distanz.

### Ausdrücklich außen vor

Schreibzugriff auf `shared` und eine Rechteverwaltung im Server, so vorgesehen in §24. Der
`actor` unterscheidet Sitzungen, er berechtigt sie nicht.

### Nachtrag: drei Fehler, die ein Agent im Betrieb gefunden hat

Ein Agent an der HTTP-Schnittstelle hat drei Dinge aufgedeckt, die keiner der 1448 Tests
abgedeckt hatte — alle drei an Stellen, an denen zwei richtige Regeln aufeinandertreffen.

**`graph_search` stürzte auf `shared` ab.** Nicht die Suche war schuld, sondern die Buchführung:
Eine semantische Suche braucht ein Anfrage-Embedding, das ist ein Modellaufruf, und der gehört
nach §11.6 in `model_calls` — auf `shared` darf der MCP-Server aber nicht schreiben (§18.3).
`psycopg.errors.ReadOnlySqlTransaction`, und zwar erst in der Datenbank. Weichen musste keine der
beiden Regeln: Der Router kennt jetzt einen `accounting_store`, und der MCP-Server setzt ihn auf
`personal`. Umgelenkt wird nur die *Zeile*; `call.store` nennt weiterhin `shared`, sonst wiese die
Abrechnung die Kosten dem falschen Store zu.

**`concept_upsert` mit `type: "note"` scheiterte.** Die Taxonomie heißt `Note` und prüft exakt
(§7.2) — richtig so. Falsch war, dass das Schema nur `"string"` sagte: Der Agent konnte nicht auf
die Schreibweise kommen, ohne sie zu raten. Das Feld führt die Typen dieser Installation jetzt als
`enum` auf, gebildet aus der Konfiguration. `Cluster` bleibt draußen, obwohl die Taxonomie ihn für
`personal` zulässt: Eine Themengruppe entsteht aus dem Clustering-Lauf, von Hand angelegt hätte
sie keine Mitglieder.

**`concept_get` lieferte leere Kennfelder.** `{"id": "", "store": "", "scope": "", "type": ""}` bei
vorhandenem `body` — die Deckelung aus §18.3. Sie lief über *alle* Zeichenketten und zog von jeder
den **gesamten** Überschuss ab; die kurzen Felder standen vorn und waren sofort leer, der lange
Text blieb. Der Agent bekam einen Fließtext, den er nicht zuordnen und über den er nicht
weitergehen konnte. Gekürzt wird jetzt der jeweils längste Text, und die Kennfelder bleiben ganz
unangetastet — ohne `id` gibt es kein `graph_traverse`.

**Dazu eine vierte Sache, die kein Absturz war.** `granularity: "cluster"` gab immer `hits: []`
zurück. Die Schwelle stand auf 0,75, gemessen ergibt sich aber:

| Anfrage | beste Ähnlichkeit zum Zentroid |
|---|---|
| „Wie verwalte ich Benutzerrechte und Token?" | 0,799 |
| „Zugangskontrolle und Berechtigungsmanagement" (wörtlicher Clustertitel) | 0,729 |
| „Rezept für Apfelkuchen mit Zimt" | 0,535 |

Die Schwelle lag **innerhalb** des Trefferbandes: Selbst der wörtliche Titel erreichte sein eigenes
Zentroid nicht. Das ist kein Zufall — ein Zentroid ist der Mittelwert vieler Vektoren und damit
flacher als jeder einzelne; eine Schwelle, die für Dokumente stimmt, ist für Zentroide zu hoch.
Jetzt 0,70, mit Abstand nach oben wie unten. Und eine leere Cluster-Suche trägt einen
`next_step`: Sie sah aus wie „dazu gibt es nichts" und hieß nur „kein Cluster liegt nah genug".

### Nachtrag: zwei Fehler beim Durchsehen der Antworten

Beim Aufschreiben, was jedes der sieben Werkzeuge zurückgibt, kamen zwei weitere Sachen heraus.

**`graph_overview` schnitt still bei 20 ab.** Es benutzte `SEARCH_LIMIT` — die Grenze der *Suche*
— und warf den Cursor der Katalogabfrage in ein `_`. Eine Suche liefert die besten Treffer, und
zwanzig davon sind genug; die Übersicht beantwortet aber „worum geht es in diesem Graphen", und
dort ist eine abgeschnittene Liste keine kürzere Antwort, sondern eine falsche. Der Agent hielt
für den ganzen Graphen, was nur dessen Anfang war. Aufgefallen ist es nur deshalb nicht, weil der
Entwicklungsbestand genau **19** Cluster hat — einen unter der Grenze. Jetzt eine eigene Vorgabe
von 50, dazu `limit` und `cursor` als Argumente und `next_cursor` in der Antwort; der Hinweis
steht im `next_step`, weil ein Agent Antworttexte liest und keine Schemata. Ist die Liste
vollständig, fehlt beides — sonst blätterte er im Kreis, weil er das Ende nicht erkennt.

**Das Feld `score` hatte drei Bedeutungen.** Bei `mode: cluster` eine Kosinusähnlichkeit (0,73
heißt „nah dran"), bei `mode: hybrid` einen RRF-Wert (0,016 ist dort ein *guter* Platz), bei einer
Traversierung die gewichtete Formel aus §12.3. Vergleichbar machen lassen sie sich nicht — ein
RRF-Wert *ist* keine Ähnlichkeit —, benennen schon: Jede Antwort trägt jetzt `score_kind` und
einen Satz `score_hint` dazu. Beides einmal je Antwort und nicht je Knoten, denn die Skala gilt
für das ganze Ergebnis.

### Nachtrag: gleichzeitige Modellaufrufe

`max_concurrency` je Anbieter in `models.yaml`, Vorgabe 1. Der Anlass ist der Vertex-Zuschnitt:
Dort nimmt ein Embedding-Aufruf genau einen Text entgegen, 120 Seiten sind also 120 Round-Trips
nacheinander. Gemessen gegen die echte Gemini-API, 24 Texte zu je einem Aufruf: **9,1 s bei 1,
1,2 s bei 8.**

Threads und nicht asyncio: Der ganze Weg darunter ist synchron, ein Wechsel färbte bis in die
Repositories durch, und gewonnen wäre nichts — was hier wartet, ist das Netz, und dabei gibt ein
Thread den GIL frei. Zwei Stellen brauchten Aufmerksamkeit. Der lose Budgetzähler wird jetzt unter
einer Sperre erhöht: `+=` ist in Python nicht unteilbar, und ein verlorener Aufruf im Zähler wäre
ein Loch im Wächter aus §11.6. Und die Reihenfolge der Vektoren muss die der Texte bleiben —
kämen die Bündel in der Reihenfolge ihrer Antworten zurück, bekäme jedes Konzept das Embedding
eines anderen, und der Graph wäre falsch, ohne dass irgendetwas fehlschlägt. Ein Test vergleicht
deshalb das parallele Ergebnis Vektor für Vektor mit dem seriellen.

### Nachtrag: FastMCP und Streamable HTTP

Der Server läuft jetzt auf `MCPServer` aus dem SDK `mcp` 2.x — das ist FastMCP, die Klasse trägt
seit dem Hauptversionswechsel diesen Namen. Vorgabe-Transport ist **Streamable HTTP** auf Port
8800 unter `/mcp`; der Container gibt ihn nach außen frei. `stdio` bleibt daneben bestehen
(`wg mcp --transport stdio`) für einen Agenten, der den Server als Unterprozess startet.

Drei Entscheidungen dabei:

- **Der Endpunkt kennt keine Authentifizierung** — ausdrücklich so gewollt. Damit liegt die
  Absicherung vollständig im Netz. `WG_MCP_HOST_BIND` bestimmt, an welche Hostadresse der Port
  gebunden wird; auf einem im Netz erreichbaren Rechner gehört dort `127.0.0.1` hin. Der geteilte
  Store bleibt unabhängig davon unbeschreibbar — das ist eine Eigenschaft der Verbindung, nicht
  des Endpunkts. Beim Start an einer anderen Adresse als Loopback wird gewarnt.
- **Die Eingabeschemata bleiben Daten.** FastMCP leitet ein Schema sonst aus der Funktions-
  signatur ab. Hier wird `ToolSpec.input_schema` aus §18.1 unverändert veröffentlicht und für die
  Prüfung der Argumente ein Pydantic-Modell daraus abgeleitet — eine Richtung, nicht zwei Quellen.
  Der Preis: `Tool` wird von Hand gebaut statt über `Tool.from_function`, und dabei wird eine
  API des SDK benutzt, die die Doku nicht als Weg beschreibt. Abgesichert ist das durch einen
  Test, der das veröffentlichte Schema Wort für Wort mit dem aus §18.1 vergleicht; ein SDK-Wechsel,
  der die Stelle verschiebt, fällt damit sofort auf.
- **Der HTTP-Transport ist geprüft, stdio nicht.** `streamable_http_app()` liefert eine gewöhnliche
  Starlette-Anwendung; die Tests fahren mit dem **echten** MCP-Client über eine ASGI-Anbindung
  dagegen — Handshake, Sitzung, `tools/list`, `tools/call`, ohne Port. Der Lebenszyklus muss dabei
  von Hand laufen, sonst steht die Sitzungsverwaltung nicht; im Container erledigt das uvicorn.
  Bei stdio bleibt es beim alten Grund: Dort werden zwei Ströme verbunden, geprüft wird, was
  darin fließt.

Der `mcp`-Container hat damit wieder einen Healthcheck. Er prüft, dass der Port steht, und nicht
mehr: Ein MCP-Aufruf verlangt eine ausgehandelte Sitzung, und die bei jedem Durchlauf zu eröffnen
hieße, im Minutentakt Agentensitzungen zu beginnen.

---

## Entwicklung

### Plattformunabhängigkeit

Es wird an keiner Stelle auf Windows- oder PowerShell-spezifische Logik gesetzt. Konkret:

- **Kein `Makefile`.** Stattdessen `scripts/dev.py` — Python läuft überall, `make` unter Windows
  in der Regel nicht. Es gibt keine `.sh`/`.ps1`-Paare, die auseinanderdriften könnten.
- **Pfade** ausschließlich über `pathlib`.
- **Unterprozesse** ohne Shell (`shell=False`), damit Zitierung auf allen Systemen gleich ist.
- **Zeilenenden** über `.gitattributes` (`* text=auto eol=lf`) einheitlich im Repository.
- **Konsolenausgabe** ohne Zeichen außerhalb der Windows-Standard-Codepage — `wg doctor`
  benutzt `[ ok ]`/`[warn]`/`[fail]` statt Unicode-Symbolen.
- **Laufzeit** ohnehin in Linux-Containern.

### Befehle

```bash
uv run python scripts/dev.py setup           # Abhängigkeiten (Python + UI)
uv run python scripts/dev.py test            # beide Testsuiten mit Coverage
uv run python scripts/dev.py test --only python
uv run python scripts/dev.py lint            # ruff, mypy, import-linter, tsc
uv run python scripts/dev.py format
uv run python scripts/dev.py check           # lint + test, vor jedem Commit
uv run python scripts/dev.py up --profile minimal
uv run python scripts/dev.py down --volumes
uv run python scripts/dev.py logs api
uv run python scripts/dev.py doctor          # wg doctor im api-Container
uv run python scripts/dev.py migrate         # wg migrate im api-Container
uv run python scripts/dev.py migrate --check # nur berichten, was aussteht
```

### Arbeiten gegen die Mock-Quellen

In den Profilen `dev` und `test` läuft der Dienst `mock-sources` mit; auf dem Host ist er unter
`http://localhost:8090` erreichbar (`WG_MOCK_HOST_PORT`). Die Seed-Daten kommen als Bind-Mount aus
`./fixtures` — eine geänderte Fixture wirkt sofort, ohne neuen Build.

```bash
docker compose exec api wg sources list           # Adapter, Fähigkeiten, Zustand
docker compose exec api wg sources list --json    # dasselbe maschinenlesbar
curl -X POST http://localhost:8090/_control/scenario/incremental_update
curl -X POST http://localhost:8090/_control/reset
curl http://localhost:8090/_control/state
```

### Läufe

```bash
docker compose exec worker wg sync --source confluence-eng           # ein Lauf, synchron
docker compose exec worker wg sync --all                             # über alle Quellen
docker compose exec worker wg sync --source confluence-eng --full    # Cursor ignorieren
docker compose exec worker wg sync --source confluence-eng --dry-run # alles tun, nichts behalten
docker compose exec api    wg runs list                              # die letzten Läufe
docker compose exec api    wg runs list --json --limit 5
docker compose exec api    wg runs show <run-id>                     # Parameter und Statistik
```

`wg sync` arbeitet synchron im aufrufenden Prozess und braucht keinen Broker. Der Dienst `worker`
läuft daneben als `wg worker` und nimmt Jobs aus der Redis-Queue entgegen — der Weg, den später
`POST /runs/sync` benutzt (§16.3). Ein `--dry-run` schreibt wirklich alles und rollt am Ende
zurück; er hinterlässt deshalb auch keine Zeile in `runs`.

Ein Lauf wird in dem Store verbucht, in den er schreibt. `wg runs list` zeigt deshalb per Default
den Store `shared`; für die andere Seite `--store personal`.

### Brücken und Abfragen

```bash
docker compose exec api wg concepts add project:finance --title "Finanzintegration"     --body "Grundlage ist [[confluence:184320]]."          # Brücken-Konzept in 'personal'
docker compose exec api wg concepts add note:x --type Note --title "Notiz" --link confluence:184320
docker compose exec api wg concepts show confluence:184320 --store shared   # beide Richtungen
docker compose exec api wg graph traverse --start confluence:184320 --store shared --hops 2
docker compose exec api wg graph search "Partitionierung" --store shared
```

`wg concepts add` gilt als menschliche Kuration (`user:cli`) und wird von keinem Lauf
überschrieben (§10.4). Der Store folgt aus dem Scope, nie aus einer Angabe (§20.1) — ein `Project`
liegt deshalb immer in `personal`.

### Model-Router und semantische Läufe

```bash
docker compose exec api wg models describe                  # welches Modell greift wofür (§11.2)
docker compose exec api wg models describe embedding --json
docker compose exec api wg models usage --store shared      # Aufrufe, Cache, Token, Kosten (§11.6)

docker compose exec api wg embed  --scope engineering                # §13.1
docker compose exec api wg embed  --scope engineering --rebuild      # nach einem Modellwechsel
docker compose exec api wg cluster --scope engineering               # §13.2, zweimal für §13.3
docker compose exec api wg relations --scope engineering --dry-run   # §14, ohne zu schreiben
docker compose exec api wg link-orphans --scope engineering --no-use-llm   # §15, nur Stufe 1
docker compose exec api wg graph search "…" --granularity cluster    # §12.4 Stufe 1
```

**Der billigste Weg, alles einmal durchlaufen zu lassen, ohne ein Token zu verbrauchen:**

```bash
docker compose exec -e WG_BUDGET_MAX_MODEL_CALLS_PER_RUN=0 api wg embed --scope engineering
```

Der Wächter greift **vor** jedem Aufruf; der Lauf endet erfolgreich mit `budget_exceeded: true`
und einer vollständigen Statistik darüber, was er getan hätte (§11.6).

`wg models describe` ruft kein Modell auf. Es beantwortet genau die Frage, die ein Modellwechsel
aufwirft — „wirkt die Änderung in `models.yaml`?" —, und zwar ohne einen einzigen Token.

`wg concepts show` rekonstruiert die eingehenden Kanten aus den Stores, die Brücken schlagen
dürfen. Der geteilte Store selbst führt keine einzige Kante über die Grenze; er weiß nicht, dass
es persönliche Konzepte gibt (§12.1).

In `.env` bleiben `WG_SOURCE_*__BASE_URL` **leer**, solange gegen die Mocks entwickelt wird: Docker
Compose liest diese Datei für seine eigene Variablenersetzung, und ein dort gesetztes
`http://localhost:8090/...` zeigte im Container auf den Container selbst statt auf den Dienst
`mock-sources`. Ohne Wert greift der Compose-Default.

Werkzeuge gegen den `personal`-Store laufen grundsätzlich **im Container**: Sein Netz ist
`internal: true` (§5.2), er ist vom Host aus nicht erreichbar, und das ist beabsichtigt.

### Tests und Coverage

Die Vorgabe liegt bei über 90 % und wird erzwungen, nicht nur gemessen:

- Python: `[tool.coverage.report] fail_under = 90` in `pyproject.toml`.
- UI: `test.coverage.thresholds` in `ui/vite.config.ts`.

Ein Lauf unterhalb der Schwelle schlägt fehl. Ausgenommen ist `cli.py` — eine reine Hülle, die
über die CLI-Tests funktional geprüft wird, deren Zeilenabdeckung aber nichts über die
Korrektheit der darunterliegenden Logik aussagt.

Testebenen nach §22.1: `tests/unit`, `tests/contract`, `tests/integration`, `tests/guards`.
Unter `tests/support` liegt eine speicherresidente Umsetzung der Persistenz-Ports. Sie ist kein
Zugeständnis an bequemere Tests, sondern der Beweis, dass die Ports tragen: Liefe der
`ConceptService` nicht ohne Datenbank, wären die Tests in `test_concept_service.py` nicht
schreibbar. Daneben liegen dort seit Stufe 3 der Dummy-Adapter für das vierte Abnahmekriterium
und die Anbindung an den Mock-Server im selben Prozess.

Die Contract-Tests brauchen kein Docker: Sie sprechen die ASGI-Anwendung des Mock-Servers direkt
an. Was ihnen fehlt — dass Netz, Port und Bind-Mount stimmen —, prüft der Integrationstest gegen
den wirklich gestarteten Container.

Die Tests unter `tests/integration` und `tests/guards` brauchen eine echte PostgreSQL-Instanz mit
`pgvector`. Ohne sie **überspringen sie sich selbst**, damit die Suite auf einem Rechner ohne
Docker durchläuft; die Schwelle von 90 % wird auch dann eingehalten. Für einen vollständigen Lauf:

```bash
docker compose --profile test up -d
uv run python scripts/dev.py test
```

Jeder Test legt sich zwei frische, leere Datenbanken an und räumt sie danach wieder ab. Beide
liegen auf derselben Instanz — für das Deployment wäre das falsch, für diese Tests ist es richtig:
Was die Migration unterscheidet, ist der *Name* des Stores, nicht sein Host. Eine abweichende
Instanz lässt sich über `WG_TEST_POSTGRES_DSN` angeben.

### Umgang mit Modell-Token

Der Model-Router kommt erst mit Stufe 7. Vorbereitet ist bereits der Budget-Wächter aus §11.6 als
Konfiguration (`budget.max_model_calls_per_run`, `budget.max_estimated_cost_per_run_eur`,
`budget.on_exceed`). Er ist die harte Obergrenze je Lauf und der Schutz davor, dass ein
fehlkonfigurierter Lauf unbemerkt Token verbraucht. Der Gemini-Schlüssel steht in `.env` unter
`WG_PROVIDER_GEMINI__API_KEY`; `.env` ist git-ignoriert.

### Secrets

`.env` und `secrets/` sind ab dem ersten Commit git-ignoriert. In `config/*.yaml` stehen
ausschließlich `${WG_...}`-Platzhalter. Secrets erscheinen weder im Log noch unter
`/api/v1/config/effective` noch in `wg doctor`.
