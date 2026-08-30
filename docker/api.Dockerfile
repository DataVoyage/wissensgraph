# Image für api, worker, mcp und die CLI (§5.1).
#
# Alle vier Dienste laufen aus demselben Image und unterscheiden sich nur im Startbefehl. Das
# hält die Abhängigkeiten identisch — ein Unterschied zwischen dem, was die API sieht, und dem,
# was der Worker sieht, kann so gar nicht erst entstehen.

FROM python:3.12-slim-bookworm AS base

# uv aus dem offiziellen Image kopieren statt per Skript installieren: reproduzierbar und ohne
# Netzwerkzugriff beim Bauen der Anwendungsschicht.
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# ---------------------------------------------------------------------------
# Abhängigkeiten zuerst, Quellcode danach: Eine Codeänderung macht den teuren
# Abhängigkeits-Layer nicht ungültig.
# ---------------------------------------------------------------------------
# README.md ist in pyproject.toml als 'readme' eingetragen und wird beim Bauen des Pakets
# gelesen — ohne die Datei scheitert 'uv sync'.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Nicht als root laufen. Die Bind-Mounts für config/ und secrets/ sind read-only (§5.3), auf
# ./data schreibt ausschließlich PostgreSQL in seinem eigenen Container.
RUN useradd --create-home --uid 10001 wg && chown -R wg:wg /app
USER wg

EXPOSE 8080

# Der Healthcheck spricht denselben Endpunkt an, auf den auch worker und mcp warten (§5.5).
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

# 'wg serve' führt erst die Migrationen aus und startet dann den Server — die Reihenfolge aus
# §5.5. Sie steht bewusst in Python und nicht als verkettetes Shell-Kommando: So gilt sie auf
# jeder Plattform gleich und ist testbar.
CMD ["wg", "serve"]
