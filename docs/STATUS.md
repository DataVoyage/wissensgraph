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
| 1 | Datenmodell und Migrationen | offen |
| 2 | Domänenkern: Konzepte, Kanten, Upsert | offen |
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
```

### Tests und Coverage

Die Vorgabe liegt bei über 90 % und wird erzwungen, nicht nur gemessen:

- Python: `[tool.coverage.report] fail_under = 90` in `pyproject.toml`.
- UI: `test.coverage.thresholds` in `ui/vite.config.ts`.

Ein Lauf unterhalb der Schwelle schlägt fehl. Ausgenommen ist `cli.py` — eine reine Hülle, die
über die CLI-Tests funktional geprüft wird, deren Zeilenabdeckung aber nichts über die
Korrektheit der darunterliegenden Logik aussagt.

Testebenen nach §22.1: `tests/unit`, `tests/contract`, `tests/integration`, `tests/guards`.
Die letzten drei füllen sich mit den Stufen, die sie brauchen.

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
