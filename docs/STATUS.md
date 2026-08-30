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
| 3 | Adapter-Framework und Mock-Quellen | offen |
| 4 | Sync-Orchestrierung | offen |
| 5 | Store-Trennung und Brücken | offen |
| 6 | Kernspace-Auflösung und Referenzdichte | offen |
| 7 | Model-Router (Gemini als erster Provider) | offen |
| 8 | Embeddings und Clustering | offen |
| 9 | Semantische Kantenerkennung | offen |
| 10 | Verwaiste-Knoten-Vernetzung | offen |
| 11 | HTTP-API und Web-UI | offen |
| 12 | MCP-Retrieval-Layer | offen |
| 13 | Anbindung der echten Quellen | offen |

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
   Confluence-Seite in `shared` zeigt — ist Gegenstand der Brückenlogik in Stufe 5. Bis dahin
   entsteht eine solche Kante mit `resolved = false`, was genau der Zustand ist, den §8.5 dafür
   vorsieht: Das Ziel ist hier noch nicht auffindbar.

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
schreibbar.

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
