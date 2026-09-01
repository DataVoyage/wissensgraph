# Web-UI: Node-Build -> nginx (§5.1).
#
# Der Build-Container wird verworfen; ausgeliefert werden nur statische Dateien. Die Basis-URL
# der API steckt bewusst NICHT im Build, sondern wird beim Start als /config.js erzeugt (§17.1) —
# ein Umgebungswechsel ist damit ein Neustart, kein neuer Build.
#
# Build-Kontext ist das Repository-Wurzelverzeichnis, damit sowohl ui/ als auch docker/
# erreichbar sind.
#
# Basis-Images und Paketquelle sind wie beim Anwendungsimage umschaltbar: 'WG_DOCKER_REGISTRY'
# und 'NPM_CONFIG_REGISTRY' zeigen in einer abgeschlossenen Umgebung auf die eigenen Spiegel.

ARG WG_DOCKER_REGISTRY=

FROM ${WG_DOCKER_REGISTRY}node:22-alpine AS build

WORKDIR /build

# Eigene Zertifizierungsstellen — dieselbe Ablage wie beim Anwendungsimage, opt-in durch Dateien.
# Siehe docker/ca-certificates/README.md. Sie greifen vor 'npm ci': Wenn die TLS-Inspektion schon
# beim Herunterladen der Pakete zuschlägt, käme ein späteres Zertifikat zu spät.
#
# 'NODE_EXTRA_CA_CERTS' ist nötig, weil Node einen eigenen eingebauten Vertrauensspeicher hat und
# '/etc/ssl/certs' nicht liest. Die Variable *ergänzt* die eingebauten Wurzeln, sie ersetzt sie
# nicht — der öffentliche Weg bleibt unverändert gültig.
#
# Sie zeigt auf eine eigene Datei und nicht auf das Systembündel, und zwar aus einem kleinen,
# aber lästigen Grund: Node warnt bei jedem Aufruf, wenn die genannte Datei fehlt. Ohne
# hinterlegte Zertifikate ist '/etc/ssl/certs/ca-certificates.crt' in diesem Basis-Image nicht
# garantiert vorhanden — die Datei unten wird dagegen immer angelegt und ist dann eben leer.
COPY docker/ca-certificates/ /usr/local/share/ca-certificates/
RUN set -eu; \
    : > /etc/wg-extra-ca.crt; \
    if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
        apk add --no-cache ca-certificates; \
        update-ca-certificates; \
        cat /usr/local/share/ca-certificates/*.crt > /etc/wg-extra-ca.crt; \
        echo "Eigene Zertifizierungsstellen aufgenommen:"; \
        ls -1 /usr/local/share/ca-certificates/*.crt; \
    else \
        echo "Keine eigenen Zertifizierungsstellen hinterlegt — Standardvertrauen."; \
    fi
ENV NODE_EXTRA_CA_CERTS=/etc/wg-extra-ca.crt

# Der npm-Spiegel. 'NPM_CONFIG_REGISTRY' ist die Variable von npm selbst — auch hier gibt es
# keinen eigenen Übersetzungsschritt.
ARG NPM_CONFIG_REGISTRY=
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}

# Abhängigkeiten zuerst: Eine Codeänderung macht den npm-Layer nicht ungültig.
COPY ui/package.json ui/package-lock.json ./

# Zugangsdaten als BuildKit-Secret statt als ARG, damit sie nicht in der Image-Historie landen
# (§20.2). Die Datei ist optional; ohne sie baut der öffentliche Weg unverändert.
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc \
    npm ci

COPY ui/ ./
RUN npm run build

FROM ${WG_DOCKER_REGISTRY}nginx:1.27-alpine AS runtime

COPY --from=build /build/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# Erzeugt /config.js bei jedem Start aus WG_UI_API_BASE_URL. Das nginx-Image führt Skripte in
# diesem Verzeichnis vor dem Start des Servers aus.
COPY docker/ui-entrypoint.sh /docker-entrypoint.d/40-wg-config.sh
RUN chmod +x /docker-entrypoint.d/40-wg-config.sh

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
