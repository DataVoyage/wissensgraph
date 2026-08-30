# Wissensgraph

Ein Wissensgraph, auf den Mensch (Web-UI) und Agent (MCP-Server) gemeinsam zugreifen. Rohdaten
aus angebundenen Quellen (Confluence, Jira, …) werden inkrementell synchronisiert und automatisch
zu thematischen Cluster-Konzepten zusammengefasst. Der Graph trennt einen lokalen `personal`-Store
von einem geteilten `shared`-Store.

Die vollständige Architektur- und Implementierungsspezifikation steht in
[`docs/architektur-spec-wissensgraph.md`](docs/architektur-spec-wissensgraph.md). Dieses Repository
setzt sie schrittweise entlang des dort definierten Stufenplans (§24) um — siehe
[`docs/STATUS.md`](docs/STATUS.md) für den aktuellen Umsetzungsstand.

## Schnellstart (lokale Entwicklung)

Voraussetzung: [Docker](https://www.docker.com/) und [uv](https://docs.astral.sh/uv/). Alles
Weitere läuft in Containern — es ist plattformunabhängig nutzbar (Windows, macOS, Linux), es wird
an keiner Stelle auf betriebssystemspezifische Skripte gesetzt.

```bash
cp .env.example .env        # Werte eintragen, .env bleibt lokal (git-ignoriert)
uv run python scripts/dev.py up --profile minimal
uv run python scripts/dev.py doctor
```

Siehe [`scripts/dev.py`](scripts/dev.py) für alle verfügbaren Befehle (Ersatz für ein
plattformabhängiges `Makefile`) und [`docs/STATUS.md`](docs/STATUS.md) für Details.

## Tests

```bash
uv sync --group dev
uv run pytest
```

Die Coverage-Vorgabe (> 90 %) ist in `pyproject.toml` (`[tool.coverage.report] fail_under = 90`)
hinterlegt; ein Testlauf unter der Schwelle schlägt fehl.
