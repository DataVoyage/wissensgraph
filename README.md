# Wissensgraph

Ein Wissensgraph, auf den Mensch (Web-UI) und Agent (MCP-Server) gemeinsam zugreifen. Rohdaten aus
angebundenen Quellen (Confluence, Jira, …) werden inkrementell synchronisiert, mit Embeddings
versehen, zu thematischen Clustern zusammengefasst und über typisierte Kanten verbunden. Der Graph
trennt einen lokalen `personal`-Store von einem geteilten `shared`-Store.

| Dokument | Wofür |
|---|---|
| dieses README | Betrieb: aufsetzen, konfigurieren, bedienen, Fehler suchen. |
| [`agent.md`](agent.md) | **Zum Mitgeben an einen Agenten.** Wie er den Graphen über MCP benutzt: Aufrufreihenfolge, alle Werkzeuge mit ihren Antwortformen, Abläufe, Anti-Muster. |
| [`docs/architektur-spec-wissensgraph.md`](docs/architektur-spec-wissensgraph.md) | Die vollständige Architektur- und Implementierungsspezifikation — das *Warum*. |
| [`docs/STATUS.md`](docs/STATUS.md) | Umsetzungsstand entlang des Stufenplans (§24), mit den Entscheidungen und ihren Gründen. |
| [`docs/konzept-ui.md`](docs/konzept-ui.md) | Das Konzept für den UI-Neubau: Anwendergruppen, Arbeitsbereiche, Graphmotor-Wechsel, Umsetzungsstufen. |

Paragraphenzeichen (§11.5, §17.3, …) verweisen **immer** auf die Spezifikation; auf Stellen
in diesem README wird als „Abschnitt 4.7“ oder „Kapitel 14“ verwiesen.

**Umgesetzt sind die Stufen 0–13**; Stufe 14 (Föderation) ist dort als Ausblick geführt und nicht
Teil dieser Umsetzung. Die echten Quellsysteme sind angebunden — solange keine Zugangsdaten
hinterlegt sind, läuft das System gegen den mitgelieferten Mock-Quellserver, der dieselbe
HTTP-Schnittstelle spricht wie Confluence und Jira (Kapitel 15).

---

## Inhalt

1. [Die sechs Leitprinzipien in Kurzform](#1-die-sechs-leitprinzipien-in-kurzform)
2. [Was das System aus welchen Bausteinen tut](#2-was-das-system-aus-welchen-bausteinen-tut)
3. [Voraussetzungen](#3-voraussetzungen)
4. [Setup Schritt für Schritt](#4-setup-schritt-für-schritt)
5. [Konfiguration](#5-konfiguration) — inkl. [Vertex AI im Betrieb](#54-vertex-ai-im-betrieb)
6. [Die Pipeline — was jeder Lauf tut](#6-die-pipeline--was-jeder-lauf-tut)
7. [Die Web-UI](#7-die-web-ui)
8. [Die HTTP-API](#8-die-http-api)
9. [Der MCP-Server für Agenten](#9-der-mcp-server-für-agenten)
10. [CLI-Referenz](#10-cli-referenz)
11. [Tokenverbrauch im Griff behalten](#11-tokenverbrauch-im-griff-behalten)
12. [Entwicklung und Tests](#12-entwicklung-und-tests)
13. [Fehlersuche](#13-fehlersuche)
14. [Eigene Registry, eigener Paketindex, Proxy](#14-eigene-registry-eigener-paketindex-proxy)
15. [Umstieg auf die echten Quellen](#15-umstieg-auf-die-echten-quellen)
16. [Sicherheitshinweise](#16-sicherheitshinweise)

---

## 1. Die sechs Leitprinzipien in Kurzform

Sie erklären viele Entscheidungen, die sonst willkürlich wirken:

| # | Prinzip | Woran es im Betrieb sichtbar wird |
|---|---|---|
| 1 | **Die Quelle bleibt die Wahrheit.** | Aus einer Quelle gespiegelte Felder sind in der UI gesperrt; das Konzept trägt `source_name` und `external_id`. |
| 2 | **Der `personal`-Store verlässt den Rechner nicht.** | Eigenes Docker-Netz ohne Ausgang, kein Host-Port, und Modellaufrufe nur an Anbieter mit `local: true`. |
| 3 | **Struktur entsteht automatisch, Korrektur bleibt beim Menschen.** | Läufe erzeugen Vorschläge, die Kurationsansicht bestätigt oder verwirft sie. |
| 4 | **Konfiguration statt Code.** | Scopes, Konzepttypen, Kantenarten, Modelle und Quellen stehen in `config/*.yaml`. Ein Modellwechsel ist kein Deployment. |
| 5 | **Jede Änderung ist nachvollziehbar.** | Jede Schreiboperation schreibt einen Journaleintrag mit Akteur, Zeit und Lauf-ID. |
| 6 | **Automatisch erzeugt ≠ bestätigt.** | Kanten tragen `generated_by`, `curated`, `verified_by`; die Graph-Ansicht zeichnet Unbestätigtes gestrichelt. |

---

## 2. Was das System aus welchen Bausteinen tut

### Funktionsumfang

Was das System kann, in einer Übersicht — die Einzelheiten in den genannten Kapiteln.

| Bereich | Umfang | Wo |
|---|---|---|
| **Quellen anbinden** | Confluence und Jira über eigene Adapter, dazu ein Mock-Server, der beide nachspielt. Inkrementell und cursor-basiert; Rate-Limits und Paginierung inbegriffen. Gespiegelte Inhalte bleiben schreibgeschützt. | 6.1, 15 |
| **Wissen aufbereiten** | Embeddings je Konzept, thematische Cluster mit erzeugten Titeln, semantische Kanten zwischen Konzepten, Vernetzung loser Knoten. Jeder Schritt ein eigener Lauf, jeder Lauf abbrechbar und nachvollziehbar. | 6 |
| **Graph abfragen** | Traversierung über mehrere Hops und Store-Grenzen hinweg, Kernspace-Ranking aus Nähe, Dichte und Aktualität, zweistufige Suche (erst Themen, dann Dokumente) mit Rückfall auf Volltext, gefilterte Kartenansicht über den Gesamtbestand. | 7.1, 8 |
| **Kuratieren** | Modellvorschläge bestätigen oder verwerfen, Cluster anlegen, umbenennen, verschmelzen, aufteilen, Mitglieder verschieben, Status und Tags setzen. Jede Änderung im Journal und rücknehmbar. | 7 |
| **Persönlicher Bereich** | Notizen und Projekte in einer eigenen Datenbank, die den Rechner nicht verlässt, mit Brücken in den geteilten Bestand. | 7 |
| **Agentenzugriff** | MCP-Server mit acht Werkzeugen über Streamable HTTP oder stdio. Der Agent liest den geteilten Bestand und schreibt ausschließlich in den persönlichen — erzwungen in der Datenbank. | 9, [`agent.md`](agent.md) |
| **Betrieb** | Läufe starten und live verfolgen, Quellen-Health, Modellnutzung mit Kostenschätzung, Bestandszahlen, aufgelöste Konfiguration mit maskierten Secrets. | 7, 11 |
| **Modelle** | Anbieterunabhängig über LangChain, Routing je Aufgabe, Fallback-Ketten, Antwort-Cache, Budgetgrenze je Lauf. Lokale Anbieter für den persönlichen Store. | 5.4, 11 |
| **Abgeschlossene Netze** | Eigene Registry, eigener Paketindex, Proxy, und optional eigene CA-Zertifikate für Umgebungen mit TLS-Inspektion. | 4.7, 14 |

Was das System **nicht** tut: Es verändert keine Quellinhalte, es schreibt nichts ohne
Journaleintrag, und es lässt keinen Agenten die geteilte Struktur ordnen.

### Dienste (`docker-compose.yml`)

| Dienst | Image | Aufgabe |
|---|---|---|
| `db-shared` | `pgvector/pgvector:pg16` | Der geteilte Store. Host-Port `5433`. |
| `db-personal` | `pgvector/pgvector:pg16` | Der persönliche Store. **Kein Host-Port**, Netz `internal: true` — Leitprinzip 2 als Eigenschaft des Deployments. |
| `broker` | `redis:7-alpine` | Job-Queue für Hintergrundläufe **und** der Antwort-Cache des Model-Routers. |
| `api` | `wissensgraph-app:local` | HTTP-API auf Port `8080`. Der einzige Dienst, der migriert (§5.5). |
| `worker` | dasselbe Image | Arbeitet Läufe aus der Queue ab (`wg worker`). |
| `mcp` | dasselbe Image | MCP-Server über Streamable HTTP (`wg mcp`), Port 8800, Pfad `/mcp`. Ohne Authentifizierung. |
| `mock-sources` | dasselbe Image | Spielt Confluence und Jira nach, Port `8090`. Gemockt wird das *Quellsystem*, nicht der Adapter — die echten Adapter laufen unverändert dagegen, inklusive Paginierung und Rate-Limits. |
| `ui` | `wissensgraph-ui:local` | nginx mit der gebauten SPA, Port `5173`. |

### Netze

```
wg-personal (internal, kein Ausgang) ── db-personal, api, worker, mcp
wg-shared                            ── db-shared, broker, mock-sources, api, worker, mcp
wg-edge                              ── ui, api, mcp
```

Die UI erreicht ausschließlich `api`. An `db-personal` kommt nur heran, wer im Netz `wg-personal`
liegt — deshalb laufen Werkzeuge gegen den persönlichen Store **im Container**, nicht auf dem Host.

### Profile

| Profil | Dienste | Wofür |
|---|---|---|
| `dev` | alle inkl. `mock-sources` | Lokale Entwicklung gegen Mocks. **Das ist der Normalfall.** |
| `live` | alle außer `mock-sources` | Betrieb gegen echte Quellsysteme. |
| `minimal` | `db-*`, `api`, `ui` | UI-Arbeit ohne Hintergrundläufe. |
| `test` | `db-*`, `mock-sources` | Integrationstests in CI. |

---

## 3. Voraussetzungen

* **Docker** mit Compose v2 (`docker compose`, nicht `docker-compose`).
* **[uv](https://docs.astral.sh/uv/)** für die Python-Werkzeuge auf dem Host (CLI, Tests).
* **Node.js ≥ 20** — nur, wenn an der UI entwickelt oder die UI-Tests laufen sollen.
* Ein **Gemini-API-Schlüssel** (Google AI Studio) für die Entwicklungsumgebung.
* Optional: **Ollama** auf dem Host, wenn persönliche Konzepte Embeddings bekommen sollen.

Es wird an keiner Stelle auf betriebssystemspezifische Skripte gesetzt — kein `Makefile`, keine
`.sh`, keine `.ps1`. Alle Projektbefehle laufen über `scripts/dev.py`, alle Dienste in Containern.
Das Setup funktioniert unverändert unter Windows, macOS und Linux.

---

## 4. Setup Schritt für Schritt

### 4.1 Abhängigkeiten installieren

```bash
uv run python scripts/dev.py setup
```

Das macht `uv sync --group dev` und `npm install` in `ui/`. Wer die UI nicht anfasst, kann
`uv sync --group dev` allein aufrufen.

### 4.2 `.env` anlegen

```bash
cp .env.example .env
```

Diese Datei ist git-ignoriert und verlässt den Rechner nicht. **Pflichtwerte**, ohne die der Stack
nicht startet:

```dotenv
WG_EMBEDDING_DIM=768
WG_API_TOKEN=<ein-selbst-gewaehltes-geheimnis>
WG_PROVIDER_GEMINI__API_KEY=<Ihr-Gemini-Schluessel>
```

`WG_EMBEDDING_DIM` geht als `vector(n)` in die Migration ein. Eine Änderung nach der ersten
Migration wirkt **nicht** rückwirkend; `wg doctor` meldet den Widerspruch, aufzulösen ist er nur
über eine neue Migration und einen Neuaufbau der Embeddings. Der Wert muss zu `tasks.embedding.
primary.dim` in `config/models.yaml` passen — sonst verweigert der Router den Start (§11.7).

Die Quell-Variablen `WG_SOURCE_*__BASE_URL` bleiben **bewusst leer**, solange gegen die Mocks
gearbeitet wird. Docker Compose liest `.env` für seine eigene Variablenersetzung; ein hier
gesetzter `localhost`-Wert landet im Container und zeigt dort auf den Container selbst statt auf
den Dienst `mock-sources`. Ohne Wert greift der Compose-Default `http://mock-sources:8090/…`.

### 4.3 Stack starten

```bash
uv run python scripts/dev.py up --profile dev
```

Entspricht `docker compose --profile dev up -d --build`. Der `api`-Dienst migriert beide Stores
beim Start selbst; ein separater Migrationsschritt ist für den Erstlauf nicht nötig.

### 4.4 Nachsehen, ob alles gesund ist

```bash
uv run python scripts/dev.py doctor
```

Das führt `wg doctor` **im `api`-Container** aus — dem einzigen Ort, von dem aus beide Stores
erreichbar sind. Geprüft werden unter anderem:

| Prüfung | Was sie feststellt |
|---|---|
| `konfiguration` | Alle Platzhalter aufgelöst, Scopes und Stores widerspruchsfrei. |
| `personal_lokal` | Der DSN des `personal`-Stores zeigt auf einen lokalen Host (§6.5). |
| `modell_policy` | Persönliche Inhalte gehen nur an Anbieter mit `local: true` (§11.5). |
| `modelle` | Jede Task hat ein Profil, jeder Provider seine Zugangsdaten, die Dimension passt. |
| `api_absicherung` | `auth_mode: none` nur bei Bindung an `127.0.0.1`. |
| `store:*` | Beide Datenbanken erreichbar, `pgvector` vorhanden. |
| `schema:*` | Keine ausstehenden Migrationen, `vector(n)` passt zu `WG_EMBEDDING_DIM`. |
| `quelle:*` | Jede aktivierte Quelle antwortet. |
| `store_trennung` | Kein Store enthält Konzepte eines fremden Scopes. |
| `agent_readonly` | Der MCP-Zugang auf `shared` schreibt nachweislich nicht (Guard 5, §20.1). |
| `proxy` | Ein gesetzter Proxy schneidet den internen Verkehr nicht ab (§5.2, siehe 14). |
| `broker` | Redis erreichbar. |

`wg doctor` schreibt nichts und ist damit jederzeit gefahrlos.

### 4.5 Daten hineinbekommen

```bash
docker compose exec api wg sync --all          # Quellen -> Konzepte
docker compose exec api wg embed  --scope engineering
docker compose exec api wg cluster --scope engineering
docker compose exec api wg cluster --scope engineering   # zweiter Lauf: Mitglieder (siehe 6.3)
docker compose exec api wg relations --scope engineering
docker compose exec api wg link-orphans --scope engineering
```

Danach steht die UI unter **<http://localhost:5173>** bereit. Beim ersten Aufruf fragt sie nach
dem Bearer-Token — das ist der Wert aus `WG_API_TOKEN`.

### 4.6 Zugriff aus dem WLAN (optional)

Die SPA lädt ihre API-Adresse zur Laufzeit aus `/config.js`. `localhost` würde auf dem besuchenden
Gerät auf dieses Gerät zeigen, deshalb muss dort die Host-IP stehen — und die API muss den neuen
Ursprung in CORS erlauben (ein Platzhalter `*` ist nicht zulässig, §20.3):

```dotenv
WG_UI_API_BASE_URL=http://192.168.178.21:8080
WG_API_CORS_ORIGINS=http://localhost:5173,http://192.168.178.21:5173
```

Danach `docker compose --profile dev up -d api ui`. **Anschließend zurückstellen:** In dieser
Konfiguration ist die API im gesamten WLAN erreichbar und nur durch den Bearer-Token geschützt.

### 4.7 Hinter einer TLS-Inspektion (optional)

In Unternehmensnetzen bricht ein Proxy häufig TLS auf. Der Container sieht dann nicht das
Zertifikat der Gegenstelle, sondern eines der internen Zertifizierungsstelle — und bricht jede
Verbindung mit einem Zertifikatsfehler ab, der wie ein Netzproblem aussieht und keines ist.

Der Weg hinein ist eine Datei, kein Schalter:

```bash
cp firma-root.crt firma-issuing.crt docker/ca-certificates/
docker compose build
```

Mehr ist nicht zu tun. Mehrere Zertifikate sind ausdrücklich vorgesehen — Root und Issuing sind
der Normalfall. Wer nichts hinlegt, merkt von dem Mechanismus nichts; die Vorgabe bleibt der
öffentliche Weg.

Vier Dinge, die man wissen muss:

* **Endung `.crt`, Inhalt PEM, ein Zertifikat je Datei.** Eine `.pem`-Datei wird
  *stillschweigend* übergangen, eine DER-Datei abgelehnt. Details in
  `docker/ca-certificates/README.md`.
* **Die Zertifikate greifen früh im Build**, vor `uv sync` und `npm ci`. Wenn die Inspektion schon
  beim Herunterladen der Abhängigkeiten zuschlägt, käme ein späteres Zertifikat zu spät.
* **Drei Vertrauensspeicher, nicht einer.** Der Systemspeicher allein genügt nicht: `httpx`, das
  Gemini-SDK und praktisch jede Python-Bibliothek lesen das Bündel von `certifi`, Node liest
  `NODE_EXTRA_CA_CERTS`. Alle drei werden bedient, die öffentlichen Wurzeln bleiben gültig.
* **Nichts davon landet im Repository.** `.gitignore` schließt die Dateien aus, und ein Test
  wacht darüber — die Ausstellerkette eines Unternehmens gehört nicht in ein öffentliches
  Repository.

Für `uv` gibt es zusätzlich `UV_NATIVE_TLS=true` als Bauargument; es lässt uv den
Betriebssystemspeicher benutzen statt seines eigenen.

### 4.8 Stack stoppen

```bash
uv run python scripts/dev.py down --profile dev
uv run python scripts/dev.py down --profile dev --volumes   # inkl. Datenbankinhalt
```

---

## 5. Konfiguration

### 5.1 Präzedenz (§6.2)

```
Code-Defaults  <  config/*.yaml  <  .env-Datei  <  Prozess-ENV  <  CLI-Flag / API-Parameter
```

Die `.env` gilt für **alle drei** Config-Dateien und darüber hinaus: Ihre Werte landen in der
Prozessumgebung, ohne bereits gesetzte zu überschreiben. Damit sehen sie auch
`config/models.yaml`, `config/sources.yaml` und die SDKs der Anbieter, die ihre eigenen Namen
lesen (`GOOGLE_APPLICATION_CREDENTIALS`, `HTTP_PROXY`, `SSL_CERT_FILE`).

Ohne `WG_CONFIG_DIR` wird zuerst `./config` neben dem Arbeitsverzeichnis gesucht, danach
`/app/config`. Im Container sind das dieselbe Stelle — dort ist `/app` das Arbeitsverzeichnis —,
auf dem Host funktioniert damit `uv run wg …` ohne weitere Angabe.

Drei Regeln gelten ausnahmslos:

1. **Kein Secret in `config/*.yaml`, nie im Image, nie im Repository.** Zugangsdaten stehen dort
   ausschließlich als `${WG_…}`-Platzhalter.
2. **Ein nicht auflösbarer Platzhalter ohne Rückfallwert ist ein Startfehler**, kein leerer String.
3. **Secrets werden maskiert** — in Logs und in `/api/v1/config/effective`, unabhängig vom
   Log-Level.

### 5.2 Die drei Config-Dateien

**`config/wissensgraph.yaml`** — der Kern:

| Abschnitt | Bedeutung |
|---|---|
| `stores` | DSN je Store; `allow_remote: false` beim `personal`-Store erzwingt Leitprinzip 2. |
| `scopes` | Welcher Scope in welchen Store schreibt. **Der Scope entscheidet, nicht die Quelle.** |
| `concept_types` | Die Taxonomie als Konfiguration. Eine neue Quelle bringt einen Typ mit — das Datenbankschema ändert sich dafür nicht. `source_mirrored: true` sperrt die gespiegelten Felder in der UI. |
| `edge_kinds` | `structural` (`member`, `related`) vs. `semantic` (`depends_on`, `extends`, `supersedes`, `references`, `contradicts`, `implements`). Die Unterscheidung steuert Traversierung und die Definition eines losen Knotens. |
| `clustering` | `neighbors_k`, `min_cluster_size`, `max_cluster_size`, `stability_runs`, `related_cluster_top_n`, `relabel_on_member_change_pct`. |
| `orphans` | Schwellen der Verwaisten-Vernetzung (siehe 6.5). |
| `traversal` | `default_hops`, `max_hops`, `max_nodes` und die Ranking-Gewichte. |
| `budget` | Die harte Obergrenze je Lauf. |
| `api`, `mcp`, `logging`, `database` | Betriebsparameter. |

**`config/models.yaml`** — der Model-Router. Kein Modellname und kein Anbieter steht sonst
irgendwo im System; ein Modellwechsel ist eine Änderung in dieser Datei.

* `providers`: `gemini` (Google AI Studio), `vertex`, `ollama` (`local: true`), `openai_compat`.
  Der Umstieg von der Developer-API auf Vertex ist ein geänderter `provider:`-Eintrag plus drei
  Umgebungsvariablen — kein Codepfad ändert sich. Der vollständige Weg steht in
  [§5.4 Vertex AI im Betrieb](#54-vertex-ai-im-betrieb).
* `defaults`: `timeout_seconds`, `max_retries`, `backoff`, `cache`, `cache_ttl_hours`.
* `tasks`: `embedding`, `cluster_labeling`, `relation_extraction`, `cluster_matching`,
  `summarization`, `query_expansion`. Jede Task nennt Provider, Modell und Parameter getrennt.
  Bei `relation_extraction` und `cluster_matching` ist `temperature: 0` Pflichtwert und keine
  Einstellung — beide erzeugen Kanten, und ein Zufallsanteil darin wäre nicht überprüfbar.
* `policies`: `personal` erlaubt ausschließlich `ollama`, `on_violation: abort`.

> **Abweichung von der Spezifikation:** Jeder Modellzugriff läuft über **LangChain** als
> einheitliches Interface und als Router. Das ist eine bewusste Entscheidung; die Spezifikation
> beschreibt an dieser Stelle eine eigene Abstraktion.

**`config/sources.yaml`** — Quell-Adapter und Abbildungsregeln: `connection` (Basis-URL, Token,
Rate-Limit, Seitengröße), `target` (Store, Scope, Standardtyp), `selection` (Spaces, Labels, JQL),
`mapping` (JSONPath-Ausdrücke auf `title`, `description`, `body`, `resource`, `tags`), `schedule`
(vorbereitet, aber deaktiviert — Läufe werden von Hand ausgelöst).

### 5.3 Die wichtigsten Umgebungsvariablen

| Variable | Bedeutung |
|---|---|
| `WG_ENV` | `dev` / `test` / `prod`. |
| `WG_LOG_LEVEL`, `WG_LOG_FORMAT` | `json` für den Betrieb, `console` ist lokal lesbarer. |
| `WG_DB_SHARED_DSN`, `WG_DB_PERSONAL_DSN` | Gelten für Prozesse **auf dem Host**; im Container setzt Compose sie auf die Servicenamen. |
| `WG_DB_SHARED_READONLY_DSN` | Optionale, eigene nur-lesende Datenbankrolle für den MCP-Zugang. Ohne Angabe wird dieselbe Rolle mit erzwungenem `default_transaction_read_only` benutzt — schwächer, aber ohne Einrichtung vorhanden. |
| `WG_EMBEDDING_DIM` | Siehe 4.2. Pflicht. |
| `WG_API_AUTH_MODE`, `WG_API_TOKEN`, `WG_API_CORS_ORIGINS` | `none` ist nur bei Bindung an `127.0.0.1` erlaubt. |
| `WG_UI_API_BASE_URL` | Die Adresse, unter der der **Browser** die API erreicht. |
| `WG_BUDGET_MAX_MODEL_CALLS_PER_RUN` | Harte Obergrenze. `0` schaltet jeden Modellaufruf ab. |
| `WG_BUDGET_MAX_COST_PER_RUN_EUR`, `WG_BUDGET_ON_EXCEED` | `abort` oder `warn`. |
| `WG_PERSONAL_ALLOW_REMOTE_MODELS` | Weicht Leitprinzip 2 bewusst auf und wird protokolliert. Standard `false`. |
| `WG_PROVIDER_GEMINI__API_KEY` | Der Schlüssel der Entwicklungsumgebung. |
| `WG_PROVIDER_VERTEX__PROJECT`, `__LOCATION`, `__CREDENTIALS_FILE` | Vertex AI im Betrieb — siehe 5.4. |
| `WG_PROVIDER_OLLAMA__BASE_URL` | Standard `http://host.docker.internal:11434/v1` — der plattformunabhängige Weg aus dem Container zum Host. |

Was tatsächlich gilt, zeigt jederzeit:

```bash
docker compose exec api wg config show          # lesbar
docker compose exec api wg config show --json   # maschinenlesbar
curl -H "Authorization: Bearer $WG_API_TOKEN" http://localhost:8080/api/v1/config/effective
```

Secrets erscheinen dort als `***`.

### 5.4 Vertex AI im Betrieb

Die Entwicklungsumgebung spricht die Gemini-Developer-API über einen API-Schlüssel an, der Betrieb
spricht dieselben Modelle über Vertex AI. Weil es dieselbe Modellfamilie ist, verspricht §11.7 für
den Wechsel "keinen erforderlichen Schritt" — die Modellnamen bleiben, der Vektorraum bleibt, und
**es entstehen keine neuen Embeddings**.

Drei Angaben, sonst nichts:

```dotenv
WG_PROVIDER_VERTEX__PROJECT=mein-gcp-projekt
WG_PROVIDER_VERTEX__LOCATION=eu
WG_PROVIDER_VERTEX__CREDENTIALS_FILE=./secrets/vertex-sa.json
```

Dazu in `config/models.yaml` je Aufgabe `provider: gemini` auf `provider: vertex` ändern. Wer alle
Aufgaben umstellt, ändert sechs Zeilen; wer erst eine erproben will, ändert eine.

**Der Standort bestimmt den Endpunkt — und damit den Ort der Verarbeitung.**

| `location` | Endpunkt | Art |
|---|---|---|
| `europe-west4` | `europe-west4-aiplatform.googleapis.com` | einzelne Region |
| `eu` | `aiplatform.eu.rep.googleapis.com` | **Mehrregion** |
| `us` | `aiplatform.us.rep.googleapis.com` | Mehrregion |
| `global` | `aiplatform.googleapis.com` | weltweit |

Alle vier Formen sind gültig. Genau das macht sie gefährlich: Ein Tippfehler meldet sich nicht,
sondern landet still an einem anderen Ort — für ein System, dessen zweites Leitprinzip die
Datenhaltung betrifft, ist das kein Schönheitsfehler. `wg doctor` und `wg models describe` geben
den **aufgelösten Endpunkt** deshalb aus, statt ihn nur zu prüfen:

```
[OK] vertex:vertex   Projekt 'mein-gcp-projekt', Standort 'eu' ->
                     aiplatform.eu.rep.googleapis.com; Anmeldung über Dienstkonto-Schlüssel.
```

**Anmeldung.** Zwei Wege, beide unterstützt:

* **Dienstkonto-Schlüssel** (`credentials_file`). Der Pfad zeigt auf die unveränderte JSON-Datei,
  die Google beim Anlegen ausgibt. `./secrets/vertex-sa.json` trifft auf dem Host **und** im
  Container dieselbe Datei, weil Compose `./secrets` nach `/app/secrets` einbindet und `/app` dort
  das Arbeitsverzeichnis ist. Das Verzeichnis `secrets/` ist git-ignoriert.
* **Standard-Anmeldung der Umgebung** — `credentials_file` einfach leer lassen. Auf
  Google-Infrastruktur (Workload Identity) ist das der bessere Weg, weil dann überhaupt kein
  Schlüssel auf einer Platte liegt.

Der benötigte OAuth-Scope (`cloud-platform`) wird beim Laden des Schlüssels **mitgegeben**. Das
klingt nach einem Detail und ist keines: Ein ohne Scope geladener Schlüssel ist ein gültiges
Objekt, das erst bei der ersten Tokenanforderung scheitert — also im ersten echten Lauf und nicht
beim Start, und dort sieht der Fehler aus wie ein Netzproblem. Die Google-Bibliothek ergänzt den
Scope nur auf ihrem eigenen Weg über die Standard-Anmeldung, nicht bei übergebenen Zugangsdaten.

Das Dienstkonto braucht die Rolle **`roles/aiplatform.user`** auf dem Projekt.

**Embeddings gehen über Vertex einzeln hinaus.** Die Schnittstelle sagt es unmissverständlich —
*"The embedContent API for this model only supports one content at a time"* —, während dieselben
Modelle über die Gemini-Developer-API ganze Bündel entgegennehmen. Der Router deckelt die
Bündelgröße deshalb selbst auf 1, sobald der Anbieter vom Typ `vertex` ist; in `models.yaml` ist
dafür nichts zu ändern, und `wg models describe` zeigt die **wirksame** Größe:

```
[OK] embedding    vertex:gemini-embedding-2 (extern)
         Dimension 768, Bündel zu 1
```

**Dagegen hilft `max_concurrency`.** Bündeln lässt sich auf der Gegenseite nichts — gleichzeitig
schicken schon. Der Wert steht je Anbieter in `models.yaml` und ist über ENV steuerbar:

```yaml
providers:
  vertex:
    max_concurrency: ${WG_PROVIDER_VERTEX__MAX_CONCURRENCY:-1}
```

Vorgabe ist `1`, also der bisherige Ablauf nacheinander. Gemessen gegen die echte Gemini-API, 24
Texte zu je einem Aufruf:

| `max_concurrency` | Dauer |
|---|---|
| 1 | 9,1 s |
| 8 | 1,2 s |

Der Wert gehört zum **Anbieter** und nicht zur Aufgabe: Was ihn begrenzt, ist dessen Ratenlimit,
und das gilt für alle Aufgaben zusammen. Zwei Dinge sind beim Erhöhen zu bedenken. Über dem
Ratenlimit tauscht man Wartezeit gegen 429er und Wiederholungen — schneller wird es dadurch
nicht. Und jeder gleichzeitige Aufruf schreibt seine Zeile in `model_calls` und braucht dafür
kurz eine Datenbankverbindung: `WG_DB_POOL_SIZE` sollte mindestens so groß sein wie der höchste
`max_concurrency`, sonst warten die Aufrufe aufeinander statt auf den Anbieter.

Umgesetzt ist es mit Threads und nicht mit asyncio. Der ganze Weg darunter — LangChain,
SQLAlchemy, psycopg — ist synchron; ein Wechsel auf async färbte von der Router-Schnittstelle bis
in die Repositories durch, und gewonnen wäre nichts: Was hier wartet, ist das Netz, und dabei gibt
ein Thread den GIL frei.

Zwei Folgen für den Betrieb bleiben: Ein Embedding-Lauf braucht einen Modellaufruf **je Konzept**
statt je 64. Der Budgetwächter zählt Aufrufe — bei mehr als
`WG_BUDGET_MAX_MODEL_CALLS_PER_RUN` Konzepten (Vorgabe 2000) endet der Lauf mit einem sauberen
Teilergebnis, und der nächste macht dort weiter, weil `wg embed` nur einbettet, was sich geändert
hat. Wer den Bestand in einem Zug einbetten will, setzt die Grenze für diesen Lauf hoch.

Sollte ein späteres Vertex-Modell Bündel annehmen, ist das ein Wert in `models.yaml` und keine
Codeänderung:

```yaml
providers:
  vertex:
    max_embedding_batch: 16
```

**Vor dem ersten Lauf:**

```bash
docker compose exec api wg doctor              # Endpunkt und Anmeldung prüfen
docker compose exec api wg models describe     # welche Aufgabe wohin geht
```

---

## 6. Die Pipeline — was jeder Lauf tut

Die fünf Läufe bauen aufeinander auf. Jeder ist **idempotent** und schreibt einen `runs`-Eintrag
mit Statistik, Fehlern und Modellverbrauch. Ein Lauf entsteht *vor* dem Job: `POST /runs/*` legt
erst die Zeile an, der Job ist nur eine Referenz — ein vor dem Start abgebrochener Lauf wird
deshalb gar nicht erst ausgeführt.

### 6.1 `sync` — Quellen spiegeln

Holt Seiten und Vorgänge über die Adapter, bildet sie nach `config/sources.yaml` ab und legt sie
als Konzepte im Zielstore an. Inkrementell über einen gespeicherten Cursor; `--full` ignoriert ihn.
Verschwundene Objekte werden zu **Grabsteinen** statt gelöscht — Kanten darauf bleiben sichtbar.
`[[id]]` im Fließtext wird zur Kante.

```bash
docker compose exec api wg sync --all
docker compose exec api wg sync --source confluence-eng --full
docker compose exec api wg sync --all --dry-run    # alles ausführen, am Ende verwerfen
```

### 6.2 `embed` — Vektoren berechnen

Bettet alle Konzepte eines Scopes ein, deren `content_hash` vom gespeicherten `source_hash`
abweicht. Ein zweiter Lauf über einen unveränderten Bestand kostet **keinen einzigen Token**.
`--rebuild` erzwingt den Neuaufbau nach einem Modellwechsel.

Im `personal`-Scope ohne laufenden lokalen Modellserver meldet der Lauf `skipped_policy` und
schreibt nichts. Das ist kein Fehler, sondern der Preis von Leitprinzip 2 — der persönliche
Bereich funktioniert dann über Kanten und lexikalische Suche.

### 6.3 `cluster` — Themen bilden

Bildet aus den Embeddings Cluster und lässt sie vom Modell benennen (Task `cluster_labeling`).

**Wichtig:** Eine Mitgliedschaft wird erst geschrieben, wenn sie `clustering.stability_runs` Läufe
überlebt hat. Bei der Vorgabe `2` legt der **erste** Lauf also Cluster an, ohne Mitglieder zu
verknüpfen — erst der zweite verbindet sie. Ein einzelner Lauf, der „nichts" tut, ist hier das
erwartete Verhalten und kein Fehlschlag.

### 6.4 `relations` — typisierte Beziehungen erkennen

Fragt das Modell für Paare innerhalb stabiler Cluster nach der Beziehungsart. „Keine Beziehung" ist
die erwartete Mehrheitsantwort; ein Lauf mit wenigen neuen Kanten ist der Regelfall.

Vorher greift eine Vorfilterung, die Paare ohne Aussicht auf ein Ergebnis aussortiert — und dort
werden auch **verworfene** Tripel geprüft. Eine per „Verwerfen" abgelehnte Beziehung kostet damit
in keinem Folgelauf noch ein Token.

`--dry-run` stellt die Fragen, schreibt aber nichts.

### 6.5 `link-orphans` — lose Knoten vernetzen

Zwei Stufen, in dieser Reihenfolge:

1. **Ohne Modell:** Textmuster (`--text-match-patterns`) und Vektornähe. Über
   `proximity_auto_commit` (Vorgabe `0.85`) wird eine Kante direkt gesetzt, zwischen
   `proximity_candidate_band` und dieser Schwelle entsteht ein Vorschlag für die Kuration.
2. **Mit Modell** (Task `cluster_matching`), nur für den Rest — abschaltbar mit `--no-use-llm`.

Jeder Parameter aus §15.4 ist als CLI-Flag überschreibbar; ohne Angabe gilt der Wert aus
`config/wissensgraph.yaml`. `--dry-run` berichtet, ohne zu schreiben.

```bash
# Der token-freie Durchlauf: nur Stufe 1, hohe Schwelle
docker compose exec api wg link-orphans --scope engineering --no-use-llm --proximity-auto-commit 0.92
```

---

## 7. Die Web-UI

<http://localhost:5173>. Die Navigation links ordnet die Ansichten in **drei Arbeitsbereiche**
nach Anwendergruppen (§17.5): *Erkunden* (Graph, Dokumente, Persönlich), *Analysieren*
(Kuration, Cluster) und *Verwalten* (Betrieb) — die Bereiche ordnen, sie sind keine Rechte.
Rechts sitzt der **Inspektor** mit dem jeweils Selektierten: einklappbar und in der Breite
ziehbar; die Aufteilung merkt sich der Browser, geteilt wird über die URL nur, *was* man
ansieht. Der Store steht in der Kopfzeile; das Bearer-Token wird beim ersten Aufruf abgefragt.

In der Kopfzeile sitzt außerdem die **globale Suche** (`/` fokussiert sie von überall):
zweistufig nach §12.4, und jeder Treffer lässt sich **lesen** oder **im Graphen ansehen** —
der Einstieg „Was haben wir zu X?" braucht kein Wissen über Ansichten. Gelesen wird im
Inspektor gerendert (Markdown über einen eigenen, XSS-freien Renderer — er baut React-Knoten,
kein HTML); eigene Notizen tragen dort ein „bearbeiten".

| Ansicht | Was sie tut |
|---|---|
| **Graph** | Die Zentrale, in zwei Modi (siehe unten): **Karte** über den gefilterten Bestand und **Traversierung** Hop für Hop. Mit Live-Physik, Filterleiste und Legende. |
| **Dokumente** | Filtern und Blättern über alle Konzepte, mit Detailansicht, Provenienz, Kanten und Journal. |
| **Cluster** | Cluster anlegen, umbenennen, verschmelzen, aufteilen; Mitglieder hinzufügen und entfernen. |
| **Kuration** | Die offenen Aufgaben nach Confidence sortiert: bestätigen, löschen, verwerfen. Vollständig über die Tastatur bedienbar (`j`/`k`/`⏎`/`x`/`s`). |
| **Automatisierung** | Jeder Aufbaulauf (Embeddings, Clustering, Relationen, Waisen-Anbindung) als geführtes Formular, vorbelegt aus der Konfiguration, Abweichungen angeschrieben. **Probelauf zuerst**: Der scharfe Knopf existiert erst nach der Vorschau und schickt dieselben Parameter (§19). |
| **Qualität** | Arbeitet die Automatisierung gut? Anteil loser Knoten je Store, Alter und Größe der Kurationswarteschlange, Cluster ohne kuratierten Titel — mit Absprung zu Kuration und Arbeitsplatz. |
| **Persönlich** | Notizen und Projekte im `personal`-Store, inklusive Brücken in den `shared`-Store. |
| **Quellen & Sync** | Quellen mit Health und letztem Lauf; Sync je Quelle mit `full` und `dry_run` — `wg sync` vollständig in der UI. |
| **Läufe** | Historie mit Status, Probelauf-Kennzeichnung, Live-Verfolgung (Server-Sent Events) und Abbrechen. |
| **Modelle & Kosten** | Nutzung je Task und Modell mit Kostenschätzung, dazu die aufgelösten Routen — `wg models describe/usage` als Ansicht. |
| **Diagnose** | `wg doctor` auf Knopfdruck (`GET /doctor`): alle Prüfungen mit Ampel, dazu die aufgelöste Konfiguration mit maskierten Secrets. Die Schemamigration bleibt bewusst an der Konsole. |

### 7.1 Die zwei Graph-Modi

Beides ist Ansicht 1 und keine zwei Ansichten — derselbe Graph, einmal aus der Vogelperspektive
und einmal zu Fuß. Der Modus steht als `mode` in der URL.

| Modus | Endpunkt | Die Frage dahinter |
|---|---|---|
| **Karte** (Vorgabe) | `GET /graph/map` | „Was liegt hier überhaupt, wenn ich es so einschränke?" Der gefilterte Bestand ohne Ausgangspunkt. Knotengröße = Grad **im Ausschnitt**. |
| **Traversierung** | `GET /graph/neighbors/{id}` | „Woran hängt *das* hier?" Doppelklick klappt einen Hop auf, der Ausschnitt wächst. Knotengröße = Score aus §12.3. |

Die Karte lädt **keinen** Gesamtgraphen: Sie holt eine gedeckelte, gefilterte Seite und weist den
Rest als Rest aus — steht `truncated`, erscheint ein „mehr laden" und die Kopfzeile schreibt
`(Ausschnitt)` neben die Knotenzahl. Der Unterschied zum Aufklappen ist nicht die Datenmenge,
sondern die Frage: Wer eine Sammlung noch nicht kennt, hat keinen Startknoten, den er nennen
könnte.

### 7.2 Physik und Steuerung

Das Vorgabe-Layout ist **Physik**: Der Graph sortiert sich sichtbar ein, und wer einen Knoten
anfasst und zieht, verformt ihn, statt einen Punkt zu verschieben. Die Simulation läuft dabei
**nicht dauerhaft** — sie kommt zur Ruhe und startet wieder, wenn jemand einen Knoten anfasst.
Der Grund steht in Abschnitt 7.5.

Gerechnet wird die Simulation (ForceAtlas2) in einem **Web Worker**: Sie konkurriert nie mit
Zoom, Auswahl oder Panels um den UI-Thread. Das ist der Kern des Motortauschs von Cytoscape auf
sigma.js — die Zahlen dazu in Abschnitt 7.5.

Über die Leiste am oberen Rand:

* **Layout** — `Physik` (live, das kraftbasierte Layout aus §17.2), `konzentrisch` (Ringe nach
  Gewicht — in der Traversierung ist das die Nähe zum Start), `hierarchisch` (Ebenen entlang
  `member`). Die beiden letzten laufen animiert ein statt zu springen.
* **Regler** — Abstoßung, Kantenlänge, Zusammenhalt. Sie wirken nur auf die Live-Simulation und
  sind bei den Einmal-Layouts sichtbar gesperrt; ein Regler ohne Wirkung wäre eine Lüge über das
  Bedienelement.
* **Titel** — Beschriftungen ein und aus. Unabhängig davon erscheinen sie ohnehin erst, wenn die
  Zoomstufe sie lesbar macht: Zweihundert Titel über einem herausgezoomten Graphen sind kein Text,
  sondern Rauschen.
* **Alles zeigen** — den ganzen Ausschnitt ins Bild rücken.

Ein Klick auf einen Knoten hebt seine Nachbarschaft hervor und blendet den Rest ab — nicht aus:
Ein ausgeblendeter Knoten ließe den Graphen zerfallen, ein blasser zeigt, dass da noch mehr ist.
Wer „weniger Bewegung" im Betriebssystem eingestellt hat, bekommt dieselbe Anordnung ohne
Animation.

### 7.3 Die visuelle Kodierung

Sie ist die Kernaussage der Ansicht und keine Geschmacksfrage (§17.2); die Legende in der linken
Spalte schreibt sie aus.

| Merkmal | Kodierung |
|---|---|
| Store | Knotenform — Kreis für `shared`, Raute für `personal` |
| Typ | Knotenfarbe, vergeben entlang der konfigurierten Taxonomie |
| Gewicht | Knotengröße (Grad in der Karte, Score in der Traversierung) |
| Cluster | schwarz — Behälter, nicht Inhalt |
| Kantenart | Linienstärke: strukturell (`member`) kräftig, semantisch fein |
| Provenienz | Linienfarbe: von Hand fast schwarz, aus der Quelle grau, **Modellvorschlag rot** |
| unbestätigt erzeugt | voll deckend; Geprüftes tritt halbtransparent zurück (Leitprinzip 6) |

Die Strichelung der früheren Fassung ist mit dem Motortausch entfallen — WebGL kennt keine
gestrichelten Linien, und die Deckkraft trägt dieselbe Botschaft: Was auf einen Menschen wartet,
steht vorn.

**Die Farben sind Grau, Weiß und Rot** — das Kaufland-CI. Rot ist dabei knapp bemessen und
deshalb aussagekräftig: Es steht für die Marke, für genau eine Hauptaktion je Fläche und für
alles, was auf einen Menschen wartet. Konzepttypen bekommen deshalb nie Rot, und eine
`member`-Kante aus dem Clustering wird nicht rot hinterlegt, obwohl sie unbestätigt ist — ein
großes Cluster brächte sonst zwanzig rote Zeilen ins Detailpanel, und danach hieße Rot dort
nichts mehr.

### 7.4 Eigenschaften, die im Betrieb spürbar sind

* **Die UI enthält keine Geschäftslogik.** Scopes, Konzepttypen, Kantenarten und die Storeliste
  kommen aus `/api/v1/config/effective`, gesperrte Felder aus dem `locked_fields` des jeweiligen
  Konzepts, die Modellrouten aus `/api/v1/models`. Solange die Konfiguration nicht geladen ist,
  rendert die SPA **keine** Ansicht — eine halb geratene Oberfläche wäre schlimmer als gar keine.
* **Löschen ≠ Verwerfen.** *Löschen* heißt „hier gehört sie nicht hin" und entfernt nur die Kante.
  *Verwerfen* heißt „diese Beziehung gibt es nicht", entfernt die Kante **und** vermerkt das
  Tripel — nur so entsteht sie im Folgelauf nicht neu.
* **Undo betrifft Struktur, nicht Inhalt.** Kanten, Mitgliedschaften, Bestätigungen, Anlegen und
  Statuswechsel lassen sich zurücknehmen. Eine inhaltliche Änderung an Titel oder Text antwortet
  mit `409`: Das Journal hält Feldnamen fest, keine Werte — es *kann* einen alten Text nicht
  wiederherstellen und sagt das offen, statt die Hälfte wiederherzustellen.

Der lokale Zustand steht in der URL, Ansichten sind damit teilbar — und zwar **einschließlich
der Filter**: Ansicht, Modus, Store, Scope, Typ, Kantenarten, „nur unbestätigte", „nur lose",
Grabsteine und der ausgewählte Knoten. Wer einen Ausschnitt bespricht, schickt einen Link und
keine Klickanleitung.

### 7.5 Wie viel der Graph verträgt

Der Graphmotor ist **sigma.js auf WebGL mit graphology**; die Simulation (ForceAtlas2) rechnet
in einem Web Worker. Die Zahlen der Cytoscape-Fassung — 1 fps bei 2.000 Knoten im Dauerbetrieb,
Physik oberhalb von 400 Knoten abgeschaltet, Mausrad nach 7,9 s — waren der Anlass für den
Tausch; sie sind Geschichte.

Gemessen mit dem Lasttest (`ui/e2e/lasttest.spec.ts`, `WG_LASTTEST=1`, echtes GPU-Rendering)
an 5.000 synthetischen Knoten mit 7.375 Kanten:

| Messung | Wert |
|---|---|
| Bildrate, **während** die Simulation läuft | **137 fps** |
| Mausrad-Zoomschritt bis zum nächsten Bild | **1 ms** |
| Motorwechsel nach Knotenzahl | entfällt — ein Motor, jede Größe |
| Ziehen eines Knotens | löst die Physik auf jeder Größe aus; der Graph verformt sich unter der Hand |

Oberhalb von 500 Knoten rechnet ForceAtlas2 mit Barnes-Hut-Näherung (n·log n statt n²); die
Simulation sortiert sich, hält nach wenigen Sekunden an und läuft wieder an, wenn jemand einen
Knoten anfasst — ein Graph, der ewig zappelt, beantwortet nichts.

Die Karte selbst zeigt zunächst 300 Knoten und wächst über „mehr laden" bis 2.000 — das ist
heute die Obergrenze **der API-Seite** (`/graph/map`, Nutzlast 2,1 MB bei 2.000 Knoten), nicht
mehr die des Motors. Wer mehr als 2.000 Knoten gleichzeitig sehen will, sieht in Wahrheit
nichts mehr; der Weg dorthin ist der Filter, nicht die Menge.

---

## 8. Die HTTP-API

Basis `http://localhost:8080`, Präfix `/api/v1`, Authentifizierung per `Authorization: Bearer …`.
Interaktive Dokumentation: <http://localhost:8080/api/v1/docs>, das Schema unter
`/api/v1/openapi.json`.

**Lesen**

```
GET  /api/v1/concepts                          Filtern und blättern
GET  /api/v1/concepts/{id}                     Kanten, Cluster, Provenienz, locked_fields
GET  /api/v1/concepts/{id}/history             Änderungsjournal (mit "undoable")
GET  /api/v1/concepts/{id}/similar             Vektor-Nachbarn
GET  /api/v1/graph/overview                    Cluster-Übersicht — der Einstiegspunkt
POST /api/v1/graph/traverse                    Knoten + Kanten + Scores über mehrere Hops
GET  /api/v1/graph/neighbors/{id}              Ein Hop, für inkrementelles Aufklappen
GET  /api/v1/graph/map                         Gefilterter Ausschnitt des Bestands (Kartenansicht)
POST /api/v1/graph/search                      Zweistufig: erst Cluster, dann Dokumente
GET  /api/v1/graph/loose                       Lose Knoten eines Stores
GET  /api/v1/clusters, /api/v1/clusters/{id}
GET  /api/v1/stats, /api/v1/sources, /api/v1/models, /api/v1/models/usage
GET  /api/v1/doctor                       # dieselben Prüfungen wie wg doctor, als JSON
GET  /api/v1/config/effective                  Secrets maskiert
GET  /healthz, /readyz
```

**Schreiben und kuratieren**

```
POST   /api/v1/concepts                        Nur im personal-Store
PATCH  /api/v1/concepts/{id}                   Gesperrte Felder -> 409
POST   /api/v1/edges                           Kuratierte Kante; nimmt einen Negativvermerk zurück
DELETE /api/v1/edges/{id}                      Entfernen ohne Negativvermerk
POST   /api/v1/edges/{id}/verify               Bestätigen
POST   /api/v1/edges/{id}/reject               Entfernen MIT Negativvermerk
GET    /api/v1/curation/queue                  Offene Aufgaben nach Confidence
GET    /api/v1/curation/journal                Die jüngsten Einträge (nie aus dem Cache)
POST   /api/v1/curation/undo                   Struktur ja, Inhalt 409
POST   /api/v1/clusters, /merge, /{id}/split, /{id}/members, PATCH /{id}
```

**Läufe**

```
POST /api/v1/runs/sync | embed | cluster | relations | link-orphans     -> 202
GET  /api/v1/runs, /api/v1/runs/{id}
GET  /api/v1/runs/{id}/events                  Server-Sent Events für den Fortschritt
POST /api/v1/runs/{id}/cancel
```

Der TypeScript-Client der UI wird aus dem OpenAPI-Schema erzeugt:

```bash
uv run python scripts/dev.py client    # -> ui/src/api/schema.ts
```

Erzeugt werden nur die **Eingabeformen**. Die Antwortformen stehen von Hand in
`ui/src/api/types.ts` und werden von der Python-Seite gegen die echten Antworten geprüft
(`tests/unit/test_api_antwortform.py`) — der Grund steht in 13.4.

---

## 9. Der MCP-Server für Agenten

> **Für den Agenten selbst gibt es [`agent.md`](agent.md)** — eine vollständige Anleitung zum
> Mitgeben: Reihenfolge der Aufrufe, alle Werkzeuge mit ihren Antwortformen, die Bedeutung von
> `score_kind`, `truncated` und der Provenienz, dazu Abläufe und Anti-Muster. Dieses Kapitel hier
> beschreibt den *Betrieb* des Servers, jenes den *Gebrauch*.

Acht Werkzeuge, zwei Transporte. Gebaut ist der Server auf **FastMCP** — im SDK `mcp` 2.x heißt
die Klasse `MCPServer`; es ist dieselbe, die früher `FastMCP` hieß.

**Über HTTP (Vorgabe).** Der Container öffnet Port 8800; ein Agent trägt nur die URL ein:

```
http://localhost:8800/mcp
```

```jsonc
// Beispiel für eine Agenten-Konfiguration
{
  "mcpServers": {
    "wissensgraph": { "url": "http://localhost:8800/mcp" }
  }
}
```

> **Der Endpunkt kennt keine Authentifizierung.** Das ist so gewollt und deshalb hier fett: Wer
> ihn erreicht, darf im persönlichen Store schreiben. Die Absicherung liegt vollständig im Netz.
> Auf einem Rechner, der im Netz erreichbar ist, gehört in die `.env`
> `WG_MCP_HOST_BIND=127.0.0.1` — dann ist der Port nur lokal offen. Der geteilte Store bleibt in
> jedem Fall unbeschreibbar, dafür sorgt die Verbindung (siehe unten).

**Über stdio.** Für einen Agenten, der den Server selbst als Unterprozess startet — ohne Port und
ohne laufenden Container:

```bash
docker compose exec -T mcp wg mcp --transport stdio --session <kennung>
```

Die Sitzungskennung erscheint im Journal als `agent:<kennung>` — jede Änderung eines Agenten ist
damit von einer Änderung eines Menschen unterscheidbar.

| Werkzeug | Zweck |
|---|---|
| `graph_schema` | Die Regeln dieser Installation: Stores, Scopes, Konzepttypen, Kantenarten, Grenzen und die eigenen Schreibrechte. Statisch — ein Aufruf je Sitzung. Nimmt dem Agenten das Raten ab. |
| `graph_overview` | „Der erste Aufruf einer Sitzung." Cluster-Übersicht; die Antwort trägt einen `next_step`. |
| `graph_traverse` | Vom Knoten aus über Hops, optional auf Kantenarten eingeschränkt. |
| `graph_search` | **Fallback**, wenn die Übersicht nicht weiterhilft. |
| `concept_get` | Ein Konzept mit Kanten und Provenienz. |
| `concept_upsert` | Anlegen oder Fortschreiben — **ohne `store`-Argument**. |
| `link_add` | Kante setzen — **ohne `from_store`-Argument**. |
| `cluster_project` | Bildet die Themengruppen im persönlichen Store neu — nach mehreren neuen Notizen zu einem Projekt. Rührt `shared` nicht an. |

**Der Agent muss nichts raten.** Die Taxonomie ist Konfiguration (§7.2) und wird exakt geprüft,
Groß- und Kleinschreibung eingeschlossen — `note` ist nicht `Note`. Ein Agent hat keinen Weg, das
zu erraten, also wird es ihm an drei Stellen gesagt:

1. **Im Eingabeschema.** `store`, `scope`, `kind`, `kinds[]`, `granularity` und `type` sind
   `enum`, gefüllt aus der Konfiguration dieser Installation. `hops` trägt sein `maximum` und
   nennt in der Beschreibung, dass größere Werte gekappt werden — sonst hält ein Agent, der 6
   anfragt und 3 bekommt, das Ergebnis für vollständig. Beim Schreiben sind die Scopes zusätzlich
   auf den persönlichen Store eingeschränkt.
2. **Über `graph_schema`.** Für die Fragen *vor* dem Einsetzen eines Werts: welcher Scope zu
   welchem Store gehört, welche Kantenart strukturell und welche semantisch ist, wo die Grenzen
   liegen und was *dieser Aufrufer* schreiben darf.
3. **In der Ablehnung.** Ein unzulässiger Wert wird nicht nur zurückgewiesen, sondern beantwortet
   mit den möglichen Werten und einem Verweis auf `graph_schema`. Diese Schicht greift auch dann
   noch, wenn ein Client das Schema nicht durchsetzt.

Drei Eigenschaften sind Absicht und keine Lücke:

* **Die Grenze liegt in der Verbindung, nicht im Code.** Der MCP-Server bekommt für `shared` nur
  die nur-lesende Engine. Ein Schreibversuch scheitert in PostgreSQL selbst, nicht an einer
  Prüfung im Anwendungscode — eine Prüfung wäre nur so gut wie der Codepfad, der sie aufruft.
  Dass `concept_upsert` und `link_add` gar kein Store-Argument annehmen, ist die zweite Hälfte
  derselben Aussage: Der Agent schreibt in seinen eigenen Bereich, sonst nirgends.
* **Antworten sind gedeckelt.** Listen und Texte werden von **hinten** gekürzt (vordere Einträge
  ranken besser) und die Antwort mit `truncated: true` markiert. Geschätzt wird über Zeichen, weil
  der Server das Modell des Aufrufers nicht kennt.
* **Die Eingabeschemata stehen im Code als Daten und nicht in Funktionssignaturen.** FastMCP kann
  ein Schema aus der Signatur ableiten; hier tut es das nicht. Die Beschreibungen sind sorgfältig
  formulierte Anweisungen an einen Agenten („**Dies ist der erste Aufruf einer Sitzung.**"), und
  aus Annotationen zurückgewonnen stünden sie ein zweites Mal da. Veröffentlicht wird das Schema
  aus §18.1 wörtlich; für die Prüfung der Argumente wird ein Modell daraus abgeleitet.

---

## 10. CLI-Referenz

Das Kommando heißt `wg`. Auf dem Host `uv run wg …`, im Container `docker compose exec api wg …`.

> **Gegen den `personal`-Store immer im Container.** Er hat keinen Host-Port und liegt in einem
> Netz ohne Ausgang; vom Host aus ist er nicht erreichbar.

| Befehl | Wirkung |
|---|---|
| `wg doctor` | Alle Prüfungen aus 4.4. Schreibt nichts. |
| `wg config show [--json]` | Die aufgelöste Konfiguration, Secrets maskiert. |
| `wg migrate [--store S] [--revision R] [--check] [--sql] [--downgrade R]` | `--check` berichtet nur, `--sql` gibt das SQL aus (Trockenlauf), `--downgrade` löscht Tabellen samt Inhalt. |
| `wg serve [--skip-migrations]` | Die HTTP-API. |
| `wg worker [--once]` | Arbeitet Jobs ab. |
| `wg mcp [--session ID] [--transport http\|stdio] [--host H] [--port P]` | Der MCP-Server. Ohne Angabe HTTP auf 8800. |
| `wg mock-sources [--host H] [--port P] [--fixtures DIR]` | Der Mock-Quellserver. |
| `wg sources list [--json]` | Quellen mit Health und letztem Lauf. |
| `wg sync [--source N \| --all] [--full] [--dry-run] [--json]` | Siehe 6.1. |
| `wg embed --scope S [--rebuild] [--json]` | Siehe 6.2. |
| `wg cluster --scope S [--dry-run] [--json]` | Siehe 6.3. `--dry-run` gruppiert und zählt, schreibt aber nichts. |
| `wg relations --scope S [--dry-run] [--json]` | Siehe 6.4. |
| `wg link-orphans --scope S [viele Flags] [--dry-run] [--json]` | Siehe 6.5. |
| `wg runs list [--store S] [--limit N] [--json]` | Lauf-Historie. |
| `wg runs show <id>` | Ein Lauf mit Statistik und Fehlern. |
| `wg concepts add <id> [--scope S] [--type T] [--body TEXT] [--link ID]` | Konzept anlegen; `[[id]]` im Body wird zur Kante. |
| `wg concepts show <id>` | Konzept mit Kanten und Provenienz. |
| `wg graph traverse [--start ID] [--hops N] [--tombstones]` | Traversierung auf der Kommandozeile. |
| `wg graph search <begriff> [--mode auto\|cluster\|document]` | Zweistufige Suche. |
| `wg models describe [task]` | Welche Task auf welchem Modell landet und warum. |
| `wg models usage` | Aufrufe, Token und Kostenschätzung je Lauf und Task. |
| `wg version` | Version. |

Alle Läufe verstehen `--json` und schreiben dann ausschließlich maschinenlesbar auf stdout.

### Projektbefehle (`scripts/dev.py`)

```bash
uv run python scripts/dev.py setup                 # Abhängigkeiten Backend + UI
uv run python scripts/dev.py up   --profile dev    # Stack starten
uv run python scripts/dev.py down --profile dev [--volumes]
uv run python scripts/dev.py logs [dienst]         # folgt den Logs
uv run python scripts/dev.py doctor                # wg doctor im api-Container
uv run python scripts/dev.py migrate [--check]     # wg migrate im api-Container
uv run python scripts/dev.py test [--only python|ui]
uv run python scripts/dev.py e2e                   # Playwright
uv run python scripts/dev.py lint                  # ruff, mypy, lint-imports, tsc
uv run python scripts/dev.py format
uv run python scripts/dev.py check                 # lint + test — der Durchlauf vor einem Commit
uv run python scripts/dev.py client                # TypeScript-Client neu erzeugen
uv run python scripts/dev.py lock [--index URL]    # uv.lock neu erzeugen (siehe 14)
```

---

## 11. Tokenverbrauch im Griff behalten

Fünf Mechanismen, vom härtesten zum weichsten:

1. **Der Budgetwächter greift vor jedem Aufruf.** `WG_BUDGET_MAX_MODEL_CALLS_PER_RUN=0` schaltet
   jeden Modellaufruf ab und ist damit der sicherste Weg, eine Pipeline einmal vollständig
   durchlaufen zu lassen, ohne ein einziges Token zu verbrauchen. Wird die Grenze erreicht, endet
   der Lauf mit `budget_exceeded: true` und einem **sauberen Teilergebnis** — was bis dahin
   erkannt wurde, ist geschrieben.
2. **Der Antwort-Cache in Redis**, `cache_ttl_hours: 168`. Ein Wiederholungslauf über unveränderte
   Inhalte trifft den Cache und kostet nichts.
3. **`content_hash` gegen `source_hash`.** `wg embed` bettet nur ein, was sich geändert hat.
4. **Vorfilterung vor `relation_extraction`.** Aussichtslose Paare und **verworfene Tripel**
   erreichen das Modell gar nicht erst.
5. **`--dry-run` und `--no-use-llm`.** Der erste stellt die Fragen ohne zu schreiben, der zweite
   überspringt die modellgestützte Stufe der Verwaisten-Vernetzung vollständig.

Was tatsächlich verbraucht wurde, steht in `wg models usage` und in der Betriebsansicht der UI —
aufgeschlüsselt nach Lauf und Task, mit Kostenschätzung.

---

## 12. Entwicklung und Tests

```bash
uv run python scripts/dev.py check     # der vollständige Durchlauf
uv run pytest                          # nur Python
npx vitest run --coverage              # nur UI (in ui/) — läuft in echtem Chromium, nicht jsdom
npx playwright test                    # Kernflüsse im Browser (in ui/)
```

Für Playwright einmalig `npx playwright install chromium` — ein Standardlauf, der still ein paar
hundert Megabyte zieht, wäre eine Überraschung an der falschen Stelle.

**Coverage-Schwellen:** Python `fail_under = 90` (`pyproject.toml`). UI: Zeilen und Statements
ebenfalls 90, Funktionen und Branches 80 — v8 zählt jede JSX-Render-Callback als eigene Funktion,
weshalb dieselbe Zahl dort etwas anderes bedeutet.

**Testarten:** `unit` (schnell, ohne Infrastruktur), `integration` (brauchen eine erreichbare
PostgreSQL-Instanz mit pgvector über `WG_TEST_POSTGRES_DSN`; ohne sie überspringen sie sich selbst)
und `guard` — die Prüfungen aus §20.1, die die Sicherheitszusagen des Systems belegen statt sie
nur zu behaupten.

**Schichtentrennung:** `uv run lint-imports` prüft neun Verträge (§4.2). Die Domäne kennt keine
Infrastruktur, die Werkzeugdefinitionen des MCP-Servers kennen kein SDK.

---

## 13. Fehlersuche

### 13.1 Der Stack startet nicht

`WG_EMBEDDING_DIM muss gesetzt sein` — Compose bricht bewusst ab, statt mit einem geratenen Wert
ein Schema anzulegen, das später nicht mehr passt. Wert in `.env` setzen.

Ein nicht auflösbarer `${WG_…}`-Platzhalter ohne Rückfallwert ist ebenfalls ein Startfehler. Die
Meldung nennt Datei und Schlüssel.

### 13.2 Die UI zeigt eine weiße Seite

Fast immer ein alter Bundle im Browser-Cache nach einem Neubau: **Strg+Shift+R**. Wenn das nicht
hilft, die Browser-Konsole öffnen — die SPA rendert absichtlich keine Ansicht, solange
`/api/v1/config/effective` nicht geantwortet hat. Kommt dort ein CORS-Fehler, steht der Ursprung
nicht in `WG_API_CORS_ORIGINS` (siehe 4.6).

### 13.3 Ein Clustering-Lauf schreibt keine Mitglieder

Erwartetes Verhalten beim ersten Lauf, siehe 6.3. Zweimal laufen lassen.

### 13.4 Persönliche Konzepte bekommen keine Embeddings

`skipped_policy` im Lauf bedeutet: Es läuft kein als `local: true` deklarierter Modellserver. Das
ist Leitprinzip 2 bei der Arbeit. Abhilfe: Ollama auf dem Host starten und
`WG_PROVIDER_OLLAMA__BASE_URL` prüfen. `WG_PERSONAL_ALLOW_REMOTE_MODELS=true` weicht die Regel auf
— dann verlassen persönliche Inhalte den Rechner, und das wird protokolliert.

### 13.5 UI und API sind sich uneinig über ein Feld

Das ist zweimal vorgekommen und hat eine gemeinsame Ursache: Die UI-Tests bilden die API nach, und
die Nachbildung hatte dieselbe falsche Annahme wie der Code. Zwei Beschreibungen desselben
Vertrags bestätigen einander, ohne dass eine davon stimmt.

Die Gegenmaßnahme ist `tests/unit/test_api_antwortform.py`: Er prüft die **echte** Antwort gegen
die Feldnamen, auf die sich `ui/src/api/types.ts` verlässt. Wer ein Antwortfeld ändert, ändert es
dort mit — sonst schlägt der Test fehl.

### 13.6 Der Build bricht mit einem Zertifikatsfehler ab

Meldungen wie `certificate verify failed`, `unable to get local issuer certificate` oder
`SELF_SIGNED_CERT_IN_CHAIN` beim `docker compose build` oder zur Laufzeit sind fast nie ein
Netzproblem, sondern eine TLS-Inspektion: Der Container sieht das Zertifikat des Proxys statt das
der Gegenstelle.

Abhilfe: die eigenen CA-Zertifikate nach `docker/ca-certificates/` legen und neu bauen (Abschnitt 4.7).
Zwei häufige Stolpersteine — die Endung muss `.crt` sein (eine `.pem`-Datei wird *stillschweigend*
übergangen), und der Inhalt muss PEM sein, nicht DER.

Tritt der Fehler nur bei `uv` auf, hilft zusätzlich `UV_NATIVE_TLS=true` (Kapitel 14).

### 13.7 Nützliche Kommandos

```bash
docker compose ps
uv run python scripts/dev.py logs api
docker compose exec api wg doctor
docker compose exec api wg runs list --limit 20
docker compose exec api wg models usage
docker compose exec db-shared psql -U wg -d wg_shared -c '\dt'
```

---

## 14. Eigene Registry, eigener Paketindex, Proxy

In einer abgeschlossenen Umgebung gibt es keinen Weg zu Docker Hub, ghcr.io, PyPI oder npm. Alle
vier Herkünfte sind deshalb umschaltbar — und **alle Vorgaben bleiben die öffentlichen**, damit
das Setup ohne jede Einrichtung funktioniert.

### Basis-Images aus der eigenen Registry

```dotenv
WG_DOCKER_REGISTRY=artifactory.firma.de/docker-remote/
WG_UV_IMAGE=artifactory.firma.de/ghcr-remote/astral-sh/uv:0.9.21
```

`WG_DOCKER_REGISTRY` wird jedem Image von Docker Hub vorangestellt — `python`, `node`, `nginx`,
`redis`, `pgvector`. **Mit Schrägstrich am Ende.**

Das uv-Image hat einen eigenen Schalter, weil es nicht auf Docker Hub liegt: In einem Artifactory
sind `docker.io` und `ghcr.io` zwei getrennte Remote-Repositories, ein gemeinsames Präfix träfe
also nur eines von beiden.

Die selbst gebauten Images sind die andere Richtung — wohin sie gehören, nicht woher fremde
kommen:

```dotenv
WG_IMAGE_PREFIX=artifactory.firma.de/wissensgraph/
WG_IMAGE_TAG=1.4.0
```

### Pakete aus dem eigenen Index

```dotenv
UV_DEFAULT_INDEX=https://artifactory.firma.de/api/pypi/pypi/simple
NPM_CONFIG_REGISTRY=https://artifactory.firma.de/api/npm/npm/
UV_NATIVE_TLS=true
```

`UV_NATIVE_TLS=true` lässt uv den Zertifikatsspeicher des Betriebssystems benutzen. Das ist der
Schalter für Umgebungen mit aufbrechendem TLS-Proxy — ohne ihn kennt uv die interne
Zertifizierungsstelle nicht und bricht mit einem Zertifikatsfehler ab, der wie ein Netzproblem
aussieht.

### Eigene Zertifizierungsstellen

`UV_NATIVE_TLS` löst nur die eine Hälfte: Es sagt uv, es solle den Systemspeicher benutzen — nicht,
was darin steht. Die andere Hälfte sind die Zertifikate selbst. Sie kommen als Dateien nach
`docker/ca-certificates/`, mehrere sind ausdrücklich vorgesehen, und mehr ist nicht zu tun:

```bash
cp firma-root.crt firma-issuing.crt docker/ca-certificates/
docker compose build
```

Bedient werden **drei** Vertrauensspeicher, weil drei verschiedene Programme verschiedene lesen —
der Systemspeicher (`psycopg`, `curl`), das Bündel von `certifi` (`httpx`, das Gemini-SDK) und
`NODE_EXTRA_CA_CERTS` (Node im UI-Build). Der Systemspeicher allein genügt also **nicht**. Die
Einzelheiten und die Regeln für die Dateien stehen in Abschnitt 4.7 und in
`docker/ca-certificates/README.md`.

> **Der Index allein genügt nicht — und das ist die eine Sache, die man nicht raten kann.**
>
> `UV_DEFAULT_INDEX` steuert die **Auflösung**, nicht die **Installation**. In `uv.lock` stehen
> absolute Adressen der Artefakte (`files.pythonhosted.org/...`), und `uv sync --frozen` lädt von
> genau dort — unabhängig davon, welcher Index gesetzt ist. Ein Build mit gesetztem Index läuft
> deshalb erfolgreich durch und hängt trotzdem weiter am öffentlichen Netz. Nachgemessen, nicht
> vermutet.
>
> Wer wirklich nur den eigenen Index erreichen darf, erzeugt die Sperrdatei einmal gegen ihn:
>
> ```bash
> uv run python scripts/dev.py lock --index https://artifactory.firma.de/api/pypi/pypi/simple
> ```
>
> Danach stehen die eigenen Adressen darin und der Bau kommt ohne öffentliches Netz aus. Das ist
> kein Mangel von uv, sondern der Preis der Reproduzierbarkeit: Eine Sperrdatei, die ihre Herkunft
> offenließe, sperrte nichts. Für npm gilt dasselbe — `package-lock.json` hält ebenfalls absolute
> Adressen fest und muss gegen den eigenen Spiegel erzeugt werden.

### Proxy — und warum `NO_PROXY` hier nicht Ihre Aufgabe ist

```dotenv
HTTP_PROXY=http://proxy.firma.de:3128
HTTPS_PROXY=http://proxy.firma.de:3128
WG_NO_PROXY=.firma.de
```

Der Proxy gilt für alle Dienste. **`NO_PROXY` setzen Sie nicht selbst** — die internen Namen
stehen fest in `docker-compose.yml`, und `WG_NO_PROXY` kommt *hinzu*, statt sie zu ersetzen.

Der Grund ist der Fehler, den man sonst bekommt und den man nicht wiedererkennt: Jede Bibliothek,
die ihre Umgebung liest — httpx tut das —, schickt bei gesetztem `HTTP_PROXY` **auch** den Aufruf
an den Nachbarcontainer dorthin. Der Proxy kennt `mock-sources` nicht, kann den Namen nicht
auflösen und antwortet mit einem Fehler, der wie ein Ausfall von `mock-sources` aussieht. Wer ihn
zum ersten Mal sieht, sucht beim Nachbarn.

`host.docker.internal` steht ebenfalls fest darin, und das ist keine Bequemlichkeit: Dort läuft
der lokale Modellserver. Ginge sein Verkehr über den Proxy, verließen **persönliche Inhalte den
Rechner** — ein Anbieter mit `local: true` wäre dann keiner mehr (Leitprinzip 2).

Weil ein Proxy auch von außerhalb von Compose gesetzt werden kann — über einen Orchestrator etwa
—, prüft `wg doctor` zusätzlich die *tatsächliche* Umgebung. Er leitet die betroffenen Hosts aus
den DSNs, dem Broker, den Quellen und den lokalen Modellanbietern ab und nennt die fehlenden
zum Einsetzen:

```
[fail] proxy: Ein Proxy ist gesetzt, aber broker, mock-sources fehlt/fehlen in NO_PROXY.
              Aufrufe an diese Hosts gehen an den Proxy, der die internen Namen nicht auflösen
              kann — der Fehler sieht dann wie ein Ausfall des Nachbarn aus.
              In NO_PROXY aufnehmen: broker,mock-sources
```

### Zugangsdaten

Sie gehören **nicht** in `.env` und nicht in ein Build-Argument: Ein Argument steht in der
Image-Historie und ist für jeden lesbar, der das Image hat (§20.2). Der Weg sind BuildKit-Secrets:

```bash
docker buildx build --secret id=netrc,src=$HOME/.netrc -f docker/api.Dockerfile .
docker buildx build --secret id=npmrc,src=$HOME/.npmrc -f docker/ui.Dockerfile .
```

Beide sind optional — fehlen sie, baut der öffentliche Weg unverändert.

### Was das absichert

`tests/unit/test_container_herkunft.py` prüft, dass keine Compose-Zeile und keine `FROM`-Zeile ein
festes Image trägt, dass jeder bauende Dienst die Argumente durchreicht und dass kein Zugangsdatum
als Build-Argument auftaucht. Das ist eine Eigenschaft, die sonst still verloren geht: Wer später
einen Dienst mit `image: redis:7` ergänzt, merkt in der Entwicklung nichts davon — dort ist der
öffentliche Weg ja offen.

---

## 15. Umstieg auf die echten Quellen

Der Umstieg ist Konfiguration, kein Codepfad. Was sich ändert, sind Adressen, Zugangsdaten und
die Auswahl — die Adapter, der Kern und die Pipeline sehen keinen Unterschied. Das ist der Zweck
der Mock-Architektur, und der Mock-Server bildet inzwischen auch die beiden Betriebsarten nach,
an denen eine Anbindung sonst erst im Ernstfall scheitert: die Standardinstallation und das
API-Gateway davor.

### 15.1 Zugangsdaten in die `.env`

```dotenv
# Confluence hinter einem API-Gateway
WG_SOURCE_CONFLUENCE__BASE_URL=https://live.api.example/sit/atlassian/itdoc/v1
WG_SOURCE_CONFLUENCE__WEB_URL=https://itdoc.example
WG_SOURCE_CONFLUENCE__TOKEN=<Confluence Personal Access Token>
WG_SOURCE_CONFLUENCE__API_KEY=<Gateway-Schlüssel>

# Jira Data Center
WG_SOURCE_JIRA__BASE_URL=https://jira.example
WG_SOURCE_JIRA__WEB_URL=https://jira.example
WG_SOURCE_JIRA__TOKEN=<Jira Personal Access Token>
```

Drei Dinge daran sind leicht zu übersehen und kosten sonst eine Stunde Fehlersuche:

**`WEB_URL` ist nicht `BASE_URL`.** Die erste ist die Adresse, unter der ein Mensch die Instanz
aufruft, die zweite die der API. Hinter einem Gateway sind das verschiedene Hosts. Alle Links im
erzeugten Text — und das Feld `resource` jedes Konzepts — benutzen die `WEB_URL`; ohne sie zeigen
sie auf die API und ein Leser kommt nicht weit. Ohne Angabe wird die `BASE_URL` benutzt, was bei
einer Standardinstallation richtig ist.

**Der Gateway-Schlüssel tritt *neben* das Token, nicht an seine Stelle.** Fehlt er, blockt das
Gateway mit 401 oder 403, bevor Confluence überhaupt gefragt wird. Der Fehler sieht dann wie ein
Auth-Problem des Quellsystems aus und wird an der falschen Stelle gesucht. Er gehört als
`extra_headers` in den Quellblock:

```yaml
    connection:
      api_prefix: ""                       # das Gateway bietet die API ohne /rest/api an
      extra_headers:
        x-apikey: ${WG_SOURCE_CONFLUENCE__API_KEY}
```

**`api_prefix` entscheidet über den Pfad.** Eine Standardinstallation antwortet unter
`/rest/api`, ein Gateway ohne dieses Präfix, weil seine `base_url` bereits auf die API zeigt.
Jira Data Center kennt **kein** `/rest/api/3` — der Vorgabewert ist deshalb `/rest/api/2`.

### 15.2 Scopes und Quellblöcke

Ein Scope je Fachbereich, nicht je technischer Quelle: Ein Confluence-Space und das Jira-Projekt
daneben gehören inhaltlich zusammen, und der Scope ist die Einheit, über die `embed`, `cluster`
und `relations` laufen. Die vier Scopes stehen in `config/wissensgraph.yaml`, die zugehörigen
Quellblöcke auskommentiert in `config/sources.yaml`.

`target.scope` gilt **je Block**. Vier Confluence-Spaces in vier Scopes brauchen also vier
Blöcke — und trotzdem *einen* Nummernkreis:

```yaml
  - name: confluence-flwoperativesysteme
    id_prefix: confluence
    shared_id_prefix: true
```

Warum das so sein muss: Verlinkt eine Seite aus dem einen Space eine aus dem anderen, kennt der
Adapter nur deren Seiten-ID. Aus welchem Space sie stammt und welcher Block sie einmal holen
wird, weiß er nicht. Mit einem Präfix je Block ließe sich dieser Verweis gar nicht aufschreiben.
Deshalb teilen sich alle Confluence-Blöcke das Präfix `confluence`, und weil ein geteiltes Präfix
sonst der klassische Weg ist, sich gegenseitig zu überschreiben, muss **jeder beteiligte Block**
es mit `shared_id_prefix: true` erklären. Fehlt die Erklärung bei einem, bricht der Start ab und
nennt ihn beim Namen.

### 15.3 Was aus dem Rohformat wird

Confluence liefert XHTML mit eigenen Namensräumen, Jira liefert Wiki-Markup. Beides wird beim
Sync in Markdown umgewandelt, bevor es als `body` in die Datenbank geht:

| Quelle | wird zu |
|---|---|
| `<ac:structured-macro ac:name="code">` mit `language` | Codeblock mit Sprachangabe |
| `info` / `tip` / `note` / `warning`, `panel` | Zitatblock mit Beschriftung |
| `<ac:image><ri:attachment>` | Bild bei Bildendung, sonst `📎`-Link |
| `<ac:link><ri:page>` | Markdown-Link **plus** Kandidat für eine Kante |
| `{code:python}…{code}`, `{quote}`, `bq.` | Codeblock, Zitatblock |
| `h1.`–`h6.`, `#`/`*`-Listen, `\|\|`-Tabellen | Überschriften, Listen, Tabellen |
| `*fett*`, `_kursiv_`, `-weg-`, `{{code}}` | `**fett**`, `*kursiv*`, `~~weg~~`, `` `code` `` |

Warum das nicht kosmetisch ist: Ein Embedding über rohes Storage-Format misst Auszeichnung statt
Inhalt, und `# Ursache eingrenzen` ist in Jira der erste Punkt einer nummerierten Liste, in
Markdown aber eine Überschrift. Der ungewandelte Text ergibt keinen kaputten, sondern einen
*falschen* Graphen — und nichts daran schlägt fehl.

> **Finger weg von `mapping.body`.** Die `mapping:`-Sektion schlägt die Vorgaben des Adapters
> (§8.4). Ein Eintrag `body: $.body.storage.value` ersetzt das fertige Markdown wieder durch das
> Rohformat und macht die ganze Umwandlung wirkungslos — lautlos. Dasselbe gilt für `tags` (der
> Adapter liest beide Antwortformen von Confluence) und `resource` (er baut eine absolute
> Adresse). Ein `mapping` für diese Felder gehört nur dorthin, wo eine Instanz wirklich abweicht.

### 15.4 Verweise werden zu Kanten — ohne den Text zu verändern

Im Markdown bleibt an jeder erkannten Verlinkung ein **gewöhnlicher, klickbarer Link** auf die
Quellseite stehen. Er funktioniert auch dann, wenn das Ziel nie synchronisiert wird. Die
Konzept-ID geht getrennt davon an den Kern.

Vier Confluence-Linkformen werden erkannt: die ausgeschriebene Seiten-ID
(`?pageId=123`, `/pages/123/Titel`), der Kurzlink (`/x/AwCd`, lokal dekodiert), und der Weg über
Space und Titel (`/display/ENG/Titel`, `<ri:page>`) — nur der letzte kostet eine Suche, und die
wird je Space-und-Titel-Paar einmal pro Lauf gemacht und gemerkt. Bei Jira ist der
Vorgangsschlüssel bereits die ID; dort ist keine Suche nötig.

**Einen eigenen Auflösungsschritt braucht es dafür nicht.** Der Graph kennt das Problem schon:
Zeigt eine Referenz auf ein Konzept, das es noch nicht gibt, entsteht die Kante trotzdem — mit
`resolved = false`. Jeder folgende Sync-Lauf prüft solche Kanten erneut und löst sie auf, sobald
das Ziel da ist. Ein zusätzliches Kommando oder eine Tabelle für offene Kandidaten wäre eine
zweite Buchführung über dieselbe Sache.

Strukturierte Beziehungen aus Jira laufen nicht über den Text, sondern kommen direkt aus den
Feldern:

| Jira | Kantenart | Anmerkung |
|---|---|---|
| `fields.subtasks` | `member` | Vorgang → Unteraufgabe |
| `fields.parent` | `related` | siehe unten |
| `is blocked by` | `depends_on` | notiert von der blockierten Seite |
| `blocks` | — | die Kehrrichtung, keine zweite Kante |
| `relates to`, Duplikate, Klone | `related` | |
| Remote-Link auf eine Confluence-Seite | `references` | nur mit `remote_links: true` |

Alle tragen `generated_by: code:source-reference` und sind damit als Tatsache aus der Quelle
gekennzeichnet — nicht als Modellvermutung (Leitprinzip 6).

Zum Elternvorgang: Die richtige Richtung wäre `member` vom Epic zum Vorgang. Eine Kante entsteht
aber immer *bei dem Objekt, das gerade synchronisiert wird*, und ein Kind kann keine Kante
schreiben, die bei seinem Epic beginnt. Ein `member` mit vertauschten Enden wäre keine Notlösung,
sondern falsch: Die Katalogschicht liest `from_id` als Behälter, das Kind erschiene als Cluster
seines eigenen Epics. Deshalb `related` — die Verbindung bleibt, ohne eine Enthaltensein-Aussage.

`remote_links: true` kostet **eine zusätzliche Anfrage je Vorgang**. Bei zehntausend Vorgängen
ist das der Unterschied zwischen Minuten und Stunden; deshalb ist es eine Entscheidung und keine
Voreinstellung.

### 15.5 Der Ablauf beim ersten Mal

```bash
# 1. Konfiguration und Erreichbarkeit prüfen
docker compose exec api wg doctor
docker compose exec api wg sources list

# 2. Trockenlauf je neuer Quelle — er durchläuft den echten Schreibpfad und rollt zurück
docker compose exec api wg sync --source confluence-flwoperativesysteme --dry-run

# 3. Confluence zuerst, dann Jira. Jira-Remote-Links zeigen auf Confluence-Seiten;
#    in dieser Reihenfolge sind mehr davon beim ersten Lauf schon auflösbar.
docker compose exec api wg sync --source confluence-flwoperativesysteme
docker compose exec api wg sync --source jira-flwoperativesysteme

# 4. Je Scope die Pipeline (§6). 'cluster' zweimal: erst Cluster, dann Mitglieder.
for scope in flwoperativesysteme klfleischwerke data-analytics iot-platform; do
  docker compose exec api wg embed --scope $scope
  docker compose exec api wg cluster --scope $scope
  docker compose exec api wg cluster --scope $scope
  docker compose exec api wg relations --scope $scope
  docker compose exec api wg link-orphans --scope $scope
done
```

Die Reihenfolge ist keine Empfehlung, sondern eine Abhängigkeit: `cluster` braucht Embeddings,
`link-orphans` braucht Cluster. Ein Scope ohne Jira-Quelle durchläuft die Schleife unverändert —
`embed` und `cluster` beziehen sich auf den Scope, nicht auf eine Quelle.

Für den Betrieb ohne Mock-Server: `--profile live` statt `--profile dev`. Zum Testen dürfen
Mock- und echte Quellen nebeneinander in `sources.yaml` stehen; `wg sync --source <name>` wählt
gezielt aus.

### 15.6 Proxy: beide Richtungen müssen stimmen

Im Unternehmensnetz ist das die häufigste Ursache für einen Fehlschlag, der nach etwas anderem
aussieht. `live.api.example` und `jira.example` sind **externe** Hosts und müssen **über** den
Proxy laufen. `mock-sources`, `broker` und die Datenbanken sind Nachbarcontainer und müssen
**am Proxy vorbei** — sonst versucht der Proxy, einen Compose-Dienstnamen aufzulösen, und der
Fehler sieht wie ein Ausfall des Nachbarn aus.

`wg doctor` prüft beide Richtungen und nennt die fehlenden Hosts konkret. Welcher Host als intern
gilt, wird aus der Form des Namens abgeleitet — ein Compose-Dienst heißt `broker` und hat keinen
Punkt, ein Host im Netz heißt `jira.example` und hat einen. Wo die Faustregel danebenliegt, etwa
bei einem internen Dienst unter seinem FQDN, entscheidet die Konfiguration:

```yaml
    connection:
      internal: true        # dieser Host muss ohne Proxy erreichbar sein
```

Alles Weitere zum Proxy steht in §14.

### 15.7 Abweichungen von der Zulieferspezifikation

Wer das Dokument `implementierung-jira-confluence-anbindung.md` kennt: An vier Stellen ist es
anders umgesetzt, jeweils weil der bestehende Code die Sache schon löst oder anders schneidet.

| Dokument | Umsetzung | Grund |
|---|---|---|
| Neues Kommando `wg link-references` und Tabelle `pending_source_references` | keines von beidem | Kanten mit `resolved = false` leisten dasselbe und werden bei jedem Lauf erneut geprüft (§8.5). Eine zweite Buchführung über dieselbe Sache. |
| `generated_by: adapter:jira` | `code:source-reference` | §8.5 legt die Kennung fest; woher ein Konzept stammt, steht bereits in `source_name`. Die Unterscheidung „aus der Quelle" gegen „vom Modell" trägt sie unverändert. |
| Epic → Issue als `member` | `related` | Eine Kante entsteht beim synchronisierten Objekt; das Kind kann keine schreiben, die beim Epic beginnt. Siehe §15.4. |
| `rate_limit: 10`, `sources:` als Mapping | `rate_limit_per_second`, Liste mit `name:` | Die bestehenden Feldnamen und die bestehende Struktur (§8.4). |

Ebenfalls aus dem Dokument übernommen und **nicht** umgesetzt: der Space `SIMPLSIT`
(Security-Inhalte brauchen eine eigene Sensitivitätsbetrachtung), Brücken-Konzepte zwischen
`personal` und `shared`, und die Zeitsteuerung — sie ist im Schema vorbereitet und abgeschaltet.

---

## 16. Sicherheitshinweise

* **`.env` ist git-ignoriert und muss es bleiben.** Dort stehen der Gemini-Schlüssel, der
  API-Token und die Quell-Tokens. Vor jedem Push den gestagten Diff auf Secrets ansehen — das
  Repository ist öffentlich.
* **`WG_API_AUTH_MODE=none` nur bei Bindung an `127.0.0.1`.** `wg doctor` prüft das.
* **Kein CORS-Platzhalter.** `WG_API_CORS_ORIGINS` nennt Ursprünge einzeln.
* **Der `personal`-Store ist die scharfe Grenze.** Kein Host-Port, ein Netz ohne Ausgang, und
  Modellaufrufe nur an Anbieter mit `local: true`. Wer `WG_PERSONAL_ALLOW_REMOTE_MODELS=true`
  setzt, hebt diese Zusage auf.
* **Für den Betrieb eine eigene nur-lesende Datenbankrolle anlegen** und als
  `WG_DB_SHARED_READONLY_DSN` eintragen. Der Rückfall über `default_transaction_read_only` benutzt
  dieselbe Rolle — wer die Einstellung kennt, kann sie zurücksetzen. Gegen einen Irrtum im Code
  hilft er, gegen einen kompromittierten Prozess nicht.
