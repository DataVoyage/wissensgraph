# Web-UI: Node-Build -> nginx (§5.1).
#
# Der Build-Container wird verworfen; ausgeliefert werden nur statische Dateien. Die Basis-URL
# der API steckt bewusst NICHT im Build, sondern wird beim Start als /config.js erzeugt (§17.1) —
# ein Umgebungswechsel ist damit ein Neustart, kein neuer Build.
#
# Build-Kontext ist das Repository-Wurzelverzeichnis, damit sowohl ui/ als auch docker/
# erreichbar sind.

FROM node:22-alpine AS build

WORKDIR /build

# Abhängigkeiten zuerst: Eine Codeänderung macht den npm-Layer nicht ungültig.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /build/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# Erzeugt /config.js bei jedem Start aus WG_UI_API_BASE_URL. Das nginx-Image führt Skripte in
# diesem Verzeichnis vor dem Start des Servers aus.
COPY docker/ui-entrypoint.sh /docker-entrypoint.d/40-wg-config.sh
RUN chmod +x /docker-entrypoint.d/40-wg-config.sh

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
