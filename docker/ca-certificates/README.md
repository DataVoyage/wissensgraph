# Eigene Zertifizierungsstellen

Wer hinter einem Unternehmensproxy baut, der TLS aufbricht, legt hier die Zertifikate der eigenen
Zertifizierungsstellen ab. **Mehr ist nicht zu tun.** Beim nächsten `docker compose build` werden
sie in den Vertrauensspeicher der Images aufgenommen; ohne Dateien in diesem Verzeichnis ändert
sich nichts.

```
docker/ca-certificates/
├── README.md          ← diese Datei, bitte liegen lassen
├── firma-root.crt
└── firma-issuing.crt  ← mehrere sind ausdrücklich vorgesehen
```

## Regeln für die Dateien

| Regel | Warum |
|---|---|
| Endung `.crt` | `update-ca-certificates` liest ausschließlich diese Endung. Eine Datei `firma.pem` wird **stillschweigend übergangen** — benenne sie um. |
| Inhalt im PEM-Format | Also `-----BEGIN CERTIFICATE-----`. Eine DER-Datei mit `.crt`-Endung wird abgelehnt; umwandeln mit `openssl x509 -inform der -in firma.der -out firma.crt`. |
| Ein Zertifikat je Datei | Eine Kette in einer Datei nimmt `update-ca-certificates` nur teilweise an. Bei einer zweistufigen PKI also zwei Dateien: Root und Issuing. |
| Nur Zertifikate, **niemals** Schlüssel | Ein Zertifikat ist öffentlich. Ein privater Schlüssel gehört nach `secrets/` und niemals in ein Image (§20.2). |

## Was damit geschieht

Die Zertifikate werden an drei Stellen bekannt gemacht, weil drei verschiedene Programme drei
verschiedene Vertrauensspeicher benutzen — einer allein genügt nicht:

1. **Der Speicher des Betriebssystems** (`update-ca-certificates`). Ihn benutzen `psycopg`,
   `curl` und alles, was direkt über OpenSSL geht.
2. **Das Bündel von `certifi`** im Anwendungsimage. Ihn benutzen `httpx`, das Gemini-SDK und
   praktisch jede Python-Bibliothek, die HTTP spricht — sie kennen den Systemspeicher nicht.
3. **`NODE_EXTRA_CA_CERTS`** im UI-Build. Node hat wiederum seinen eigenen eingebauten Speicher.

Sie greifen **früh im Build**, noch vor `uv sync` und `npm ci`. Das ist Absicht: Wenn die
TLS-Inspektion schon beim Herunterladen der Abhängigkeiten zuschlägt, hilft ein Zertifikat, das
erst danach installiert wird, nicht mehr.

## Warum die Zertifikate nicht im Repository liegen

`.gitignore` schließt jede Zertifikatsdatei in diesem Verzeichnis aus. Das Repository ist
öffentlich, und die Ausstellerkette eines Unternehmens gehört nicht hinein — sie ist zwar kein
Geheimnis, verrät aber die interne PKI. Auf einem neuen Rechner werden die Dateien deshalb aus
der internen Ablage hierher kopiert; ein Test wacht darüber, dass sie nie versehentlich
mitcommittet werden.
