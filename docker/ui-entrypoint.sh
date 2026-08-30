#!/bin/sh
# Erzeugt die Laufzeitkonfiguration der SPA (§17.1).
#
# Läuft im nginx-Container (Linux) vor dem Start des Servers. Damit ist die Basis-URL der API
# eine Umgebungsvariable und keine Bauzeit-Entscheidung: derselbe Image-Build läuft in
# Entwicklung, Test und Betrieb.
set -eu

: "${WG_UI_API_BASE_URL:=}"

cat > /usr/share/nginx/html/config.js <<EOF
window.__WG_CONFIG__ = { apiBaseUrl: "${WG_UI_API_BASE_URL}" };
EOF

echo "config.js erzeugt: apiBaseUrl=${WG_UI_API_BASE_URL:-(gleicher Ursprung)}"
