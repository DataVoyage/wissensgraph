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
