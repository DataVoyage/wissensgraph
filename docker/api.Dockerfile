# Image für api, worker, mcp und die CLI (§5.1).
#
# Alle vier Dienste laufen aus demselben Image und unterscheiden sich nur im Startbefehl. Das
# hält die Abhängigkeiten identisch — ein Unterschied zwischen dem, was die API sieht, und dem,
# was der Worker sieht, kann so gar nicht erst entstehen.
#
# **Herkunft der Basis-Images und der Pakete ist eine Bauzeit-Entscheidung.** In einer Umgebung
# ohne freien Internetzugang zeigen 'WG_DOCKER_REGISTRY' und 'UV_DEFAULT_INDEX' auf die eigene
# Registry und den eigenen Paketindex (Artifactory, Nexus). Ohne Angabe gilt jeweils die
# öffentliche Quelle — die Vorgabe bleibt damit die, die ohne Einrichtung funktioniert.

# Globale Argumente: Sie gelten vor dem ersten FROM und müssen in jeder Stufe, die sie benutzt,
# erneut deklariert werden. Das ist eine Eigenheit von Docker und kein Versehen.
ARG WG_DOCKER_REGISTRY=
ARG WG_UV_IMAGE=ghcr.io/astral-sh/uv:0.9.21

# uv aus dem offiziellen Image kopieren statt per Skript installieren: reproduzierbar und ohne
# Netzwerkzugriff beim Bauen der Anwendungsschicht. Als eigene Stufe und nicht als 'COPY --from'
# mit Variable, weil eine benannte Stufe in jeder Docker-Version gleich aufgelöst wird.
#
# Eigener Schalter, weil dieses Image nicht auf Docker Hub liegt: In einem Artifactory sind
# 'docker.io' und 'ghcr.io' zwei getrennte Remote-Repositories, ein gemeinsames Präfix träfe
# also nur eines von beiden.
FROM ${WG_UV_IMAGE} AS uv

FROM ${WG_DOCKER_REGISTRY}python:3.12-slim-bookworm AS base

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# ---------------------------------------------------------------------------
# Eigene Zertifizierungsstellen — opt-in durch Dateien, nicht durch Schalter
# ---------------------------------------------------------------------------
# In einem Unternehmensnetz mit aufbrechender TLS-Inspektion sieht der Container nicht das
# Zertifikat der Gegenstelle, sondern eines der internen Zertifizierungsstelle. Ohne sie im
# Vertrauensspeicher scheitert jede TLS-Verbindung mit einem Fehler, der wie ein Netzproblem
# aussieht und keines ist.
#
# Der Weg hinein ist eine Datei und kein Bauargument: Wer .crt-Dateien nach
# 'docker/ca-certificates/' legt, hat damit alles getan; wer keine hinlegt, merkt von dieser
# Stufe nichts. Ein zusätzliches '--build-arg' wäre eine zweite Handlung für dieselbe
# Entscheidung — und eine, die man beim nächsten Build vergisst. Mehrere Zertifikate sind
# ausdrücklich vorgesehen (Root und Issuing sind der Normalfall).
#
# Die Stufe steht **vor** 'uv sync'. Wenn die Inspektion schon beim Herunterladen der
# Abhängigkeiten zuschlägt, hilft ein Zertifikat, das erst danach installiert wird, nicht mehr.
COPY docker/ca-certificates/ /usr/local/share/ca-certificates/
RUN set -eu; \
    if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
        update-ca-certificates; \
        echo "Eigene Zertifizierungsstellen aufgenommen:"; \
        ls -1 /usr/local/share/ca-certificates/*.crt; \
    else \
        echo "Keine eigenen Zertifizierungsstellen hinterlegt — Standardvertrauen."; \
    fi

# ---------------------------------------------------------------------------
# Paketquelle (§5.3)
# ---------------------------------------------------------------------------
# 'UV_DEFAULT_INDEX' ersetzt PyPI, 'UV_INDEX' stellt weitere Indizes daneben. Beide sind
# uv-eigene Variablen — es gibt hier keinen Übersetzungsschritt, der bei einem uv-Update
# nachgezogen werden müsste.
#
# ACHTUNG, und das ist nachgemessen und nicht vermutet: Diese Variablen steuern die *Auflösung*,
# nicht die *Installation*. 'uv sync --frozen' lädt von den absoluten Adressen, die in uv.lock
# stehen — bei der mitgelieferten Sperrdatei also von files.pythonhosted.org, ganz gleich, was
# hier gesetzt ist. Wer wirklich nur den eigenen Index erreichen darf, muss uv.lock einmal gegen
# ihn erzeugen:
#
#     uv run python scripts/dev.py lock --index https://artifactory.firma.de/api/pypi/pypi/simple
#
# Danach stehen die Adressen des eigenen Index in der Sperrdatei, und der Build kommt ohne
# Zugang zum öffentlichen Netz aus. Die Variablen hier bleiben trotzdem richtig: Sie gelten für
# jede Auflösung, die im Bild stattfindet, und machen die Herkunft am Bauort sichtbar.
#
# 'UV_NATIVE_TLS' lässt uv den Zertifikatsspeicher des Betriebssystems benutzen. Das ist der
# Schalter für Umgebungen mit aufbrechendem TLS-Proxy: Ohne ihn kennt uv die interne
# Zertifizierungsstelle nicht und bricht mit einem Zertifikatsfehler ab, der wie ein Netzproblem
# aussieht.
ARG UV_DEFAULT_INDEX=
ARG UV_INDEX=
ARG UV_NATIVE_TLS=false
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_INDEX=${UV_INDEX} \
    UV_NATIVE_TLS=${UV_NATIVE_TLS}

# ---------------------------------------------------------------------------
# Abhängigkeiten zuerst, Quellcode danach: Eine Codeänderung macht den teuren
# Abhängigkeits-Layer nicht ungültig.
# ---------------------------------------------------------------------------
# README.md ist in pyproject.toml als 'readme' eingetragen und wird beim Bauen des Pakets
# gelesen — ohne die Datei scheitert 'uv sync'.
COPY pyproject.toml uv.lock README.md ./

# Zugangsdaten für den Paketindex kommen als BuildKit-Secret und nicht als ARG: Ein ARG steht in
# der Image-Historie und wäre damit für jeden lesbar, der das Image hat (§20.2). Die Datei ist
# optional — ohne sie baut der öffentliche Weg unverändert.
RUN --mount=type=secret,id=netrc,target=/root/.netrc \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN --mount=type=secret,id=netrc,target=/root/.netrc \
    uv sync --frozen --no-dev

# Dieselben Zertifikate noch einmal — für Python.
#
# Der Systemspeicher von oben genügt nicht, und das ist der Punkt, an dem eine naheliegende
# Lösung scheitert: 'httpx', das Gemini-SDK und praktisch jede Python-Bibliothek, die HTTP
# spricht, benutzen das Bündel von 'certifi' und nicht '/etc/ssl/certs'. Ein Zertifikat, das nur
# im Systemspeicher steht, ist für sie unsichtbar. Angehängt statt ersetzt: Die öffentlichen
# Wurzeln bleiben gültig, die interne kommt dazu.
#
# Erst hier, weil 'certifi' vorher nicht installiert ist.
RUN set -eu; \
    if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
        buendel="$(python -c 'import certifi; print(certifi.where())')"; \
        cat /usr/local/share/ca-certificates/*.crt >> "$buendel"; \
        echo "certifi-Bündel ergänzt: $buendel"; \
    fi

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
