"""Zentrale Defaults — die einzige Stelle im Code, an der Konfigurationsliterale stehen.

Leitprinzip 12 (§3) verlangt: "Jeder Wert, der sich zwischen Umgebungen, Installationen oder
Läufen unterscheiden kann, kommt aus ENV oder Config. Im Code stehen nur Defaults, und die
stehen an genau einer Stelle."

Dieses Modul ist diese eine Stelle. Wer anderswo im Code ein Literal für eine URL, eine
Schwelle, einen Scope-Namen, ein Modell oder eine Umgebung schreibt, verletzt §6.1 Regel 1.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# ENV-Konvention (§6.4)
# ---------------------------------------------------------------------------

#: Präfix aller Umgebungsvariablen des Systems.
ENV_PREFIX: Final = "WG_"

#: Trennzeichen für verschachtelte ENV-Schlüssel, z. B. ``WG_PROVIDER_VERTEX__PROJECT``.
ENV_NESTED_DELIMITER: Final = "__"

#: Maskierung, mit der Secrets in Logs und unter ``/api/v1/config/effective`` ersetzt
#: werden (§20.2).
SECRET_MASK: Final = "***"

# ---------------------------------------------------------------------------
# Laufzeitumgebung und Pfade
# ---------------------------------------------------------------------------

ENV: Final = "dev"
CONFIG_DIR: Final = "/app/config"
CORE_CONFIG_FILENAME: Final = "wissensgraph.yaml"
MODELS_CONFIG_FILENAME: Final = "models.yaml"
SOURCES_CONFIG_FILENAME: Final = "sources.yaml"

#: Umgebungsvariable, die den Pfad der Quellkonfiguration überschreibt (§6.4).
SOURCES_FILE_ENV: Final = "WG_SOURCES_FILE"
LOGGING_CONFIG_FILENAME: Final = "logging.yaml"

# ---------------------------------------------------------------------------
# Logging (§21.1)
# ---------------------------------------------------------------------------

LOG_LEVEL: Final = "INFO"
LOG_FORMAT: Final = "json"

# ---------------------------------------------------------------------------
# Datenbank (§6.4, §7.3)
# ---------------------------------------------------------------------------

DB_POOL_SIZE: Final = 5

#: Sekunden, die ein Verbindungsaufbau höchstens dauern darf. Der Wert begrenzt vor allem
#: ``/readyz``: Eine Bereitschaftsprüfung, die auf einen TCP-Timeout des Betriebssystems wartet,
#: ist wertlos — sie soll schnell "nicht bereit" melden statt langsam gar nichts.
DB_CONNECT_TIMEOUT_SECONDS: Final = 5

#: Store-Namen. Werden auch als Spaltenwerte in ``concepts.store`` und ``edges.*_store`` benutzt.
STORE_SHARED: Final = "shared"
STORE_PERSONAL: Final = "personal"

# ---------------------------------------------------------------------------
# Migrationen (§5.5, §7.3, §7.4)
# ---------------------------------------------------------------------------

#: Namensraum, aus dem der Schlüssel des PostgreSQL-Advisory-Locks abgeleitet wird. §5.5 verlangt,
#: dass Migrationen nie parallel laufen; der Lock ist die Absicherung dagegen, dass zwei
#: api-Container gleichzeitig hochfahren.
MIGRATION_LOCK_NAMESPACE: Final = "wissensgraph.migrations"

#: Sekunden, die auf den Migrations-Lock gewartet wird. Läuft die Zeit ab, bricht der Lauf mit
#: einer klaren Meldung ab, statt unbegrenzt zu hängen — ein Container, der beim Start blockiert,
#: ist schwerer zu diagnostizieren als einer, der mit Grund abbricht.
MIGRATION_LOCK_TIMEOUT_SECONDS: Final = 60

#: Präfix der Alembic-Versionstabelle. §7.3 verlangt getrennte Versionstabellen je Store; mit dem
#: Store im Tabellennamen gilt das auch dann, wenn beide Schemata je in derselben Datenbank
#: landen sollten.
MIGRATION_VERSION_TABLE_PREFIX: Final = "alembic_version_"

#: Erweiterungen, die jede Store-Datenbank braucht (§7.3). Die Migration legt sie selbst an; sie
#: setzt damit nichts voraus, was ein Init-Skript des Container-Images getan haben müsste.
REQUIRED_EXTENSIONS: Final = ("vector", "pg_trgm", "uuid-ossp")

#: Parameter des HNSW-Index auf ``concept_embeddings.embedding`` (§7.4).
HNSW_M: Final = 16
HNSW_EF_CONSTRUCTION: Final = 64

#: Obergrenze der Vektordimension. pgvector kann bis 2000 Dimensionen indizieren; ein größerer
#: Wert legt die Tabelle zwar an, lässt den HNSW-Index aber scheitern. Der Startfehler ist
#: verständlicher als ein Indexfehler mitten in der Migration.
EMBEDDING_DIM_MAX: Final = 2000

# ---------------------------------------------------------------------------
# Domänenkern: IDs, Hash, Referenzen (§7.1, §7.5, §10.2, §10.3)
# ---------------------------------------------------------------------------

#: Trennzeichen zwischen Präfix und lokalem Teil einer Konzept-ID (§7.5).
ID_SEPARATOR: Final = ":"

#: Präfixe der im Code erzeugten IDs. Quellpräfixe stehen dagegen in ``sources.yaml`` (§7.5) —
#: sie gehören zur Quelle, nicht zum Kern.
ID_PREFIX_CLUSTER: Final = "cluster"
ID_PREFIX_NOTE: Final = "note"
ID_PREFIX_PROJECT: Final = "project"

#: Erlaubte Form eines Präfixes: kleingeschrieben, beginnt mit einem Buchstaben. Die Enge ist
#: Absicht — das Präfix erscheint in URLs, Logs und Dateinamen von Exporten. Es steht hier und
#: nicht in der Domäne, weil auch ``sources.yaml`` dagegen geprüft wird: ``id_prefix`` einer
#: Quelle und Präfix einer Konzept-ID sind dieselbe Konvention (§7.5, §8.4).
ID_PREFIX_PATTERN: Final = r"^[a-z][a-z0-9_-]*$"

#: Erlaubte Form des lokalen Teils. Verboten sind Leerraum (eine ID mit Leerzeichen lässt sich in
#: ``[[id]]``-Referenzen nicht sauber abgrenzen) und eckige Klammern (sie sind die Syntax der
#: Referenz selbst).
ID_LOCAL_PATTERN: Final = r"^[^\s\[\]]+$"

#: Hash-Verfahren der Änderungserkennung (§10.3).
CONTENT_HASH_ALGORITHM: Final = "sha256"

#: Trennzeichen zwischen den Feldern, die in den Content-Hash eingehen. ASCII 30 (Record
#: Separator) kommt in keinem sinnvollen Inhalt vor. Ohne ein Trennzeichen wären
#: ``title='ab', description=''`` und ``title='a', description='b'`` derselbe Hash — eine
#: Änderung bliebe unbemerkt.
CONTENT_HASH_FIELD_SEPARATOR: Final = "\x1e"

#: Muster einer Referenz im ``body`` (§7.1: "Referenzen auf andere Konzepte als ``[[id]]``").
#: Zeilenumbrüche sind ausgeschlossen, damit zwei unvollständige Klammerpaare in
#: aufeinanderfolgenden Zeilen nicht zu einer Riesenreferenz verschmelzen.
REFERENCE_PATTERN: Final = r"\[\[([^\[\]\n]+)\]\]"

#: Erzeuger-Kennungen generierter Kanten. Sie stehen in ``edges.generated_by`` und entscheiden,
#: welche Kanten ein Lauf ersetzen darf (§10.4).
GENERATED_BY_BODY_REFERENCE: Final = "code:body-reference"
GENERATED_BY_SOURCE_REFERENCE: Final = "code:source-reference"

#: Kantenart, die aus einer Referenz entsteht (§8.5).
EDGE_KIND_REFERENCES: Final = "references"

#: Voreingestellter Status eines Konzepts (§7.4).
CONCEPT_STATUS_DEFAULT: Final = "stable"

#: Akteur eines Sync-Laufs im Änderungsjournal (§7.4).
ACTOR_SYNC: Final = "system:sync"

# ---------------------------------------------------------------------------
# Quell-Adapter-Framework (§8, §9)
# ---------------------------------------------------------------------------

#: Entry-Point-Gruppe, unter der ein installiertes Paket eine Adapter-Factory anmeldet (§8.3).
#: Der Name ist Teil der öffentlichen Schnittstelle des Systems: Wer ihn ändert, macht jeden
#: extern gepflegten Adapter unauffindbar.
ADAPTER_ENTRY_POINT_GROUP: Final = "wissensgraph.adapters"

#: Adapterschlüssel der mitgelieferten Umsetzungen. Sie sind fest eingebaut, damit ein frisch
#: ausgechecktes Repository ohne Installationsschritt lauffähig ist; ein gleichnamiger Entry
#: Point hat trotzdem Vorrang (§8.3).
ADAPTER_CONFLUENCE: Final = "confluence"
ADAPTER_JIRA: Final = "jira"
ADAPTER_FIXTURE: Final = "fixture-source"

#: Trennzeichen zwischen Modul und Klasse in ``class: "paket.modul:Klasse"`` (§8.3).
ADAPTER_CLASS_SEPARATOR: Final = ":"

#: Voreinstellungen einer Quellverbindung (§8.4). Jede davon ist je Quelle überschreibbar.
SOURCE_TIMEOUT_SECONDS: Final = 30.0
SOURCE_RATE_LIMIT_PER_SECOND: Final = 5.0
SOURCE_RETRIES: Final = 3
SOURCE_PAGE_SIZE: Final = 50

#: Backoff zwischen zwei Versuchen: erste Wartezeit, Verdopplungsfaktor, Obergrenze. §22.3
#: verlangt, dass eine 429-Antwort zu Backoff führt und nicht zum Abbruch.
SOURCE_BACKOFF_INITIAL_SECONDS: Final = 0.5
SOURCE_BACKOFF_FACTOR: Final = 2.0
SOURCE_BACKOFF_MAX_SECONDS: Final = 30.0

#: Antwortcodes, die als vorübergehend gelten und einen erneuten Versuch rechtfertigen. 429 ist
#: das Rate-Limit, die 5xx sind Serverfehler; 4xx im Übrigen wiederholt kein Versuch, weil eine
#: falsche Anfrage beim zweiten Mal genauso falsch ist.
SOURCE_RETRY_STATUS_CODES: Final = (429, 500, 502, 503, 504)

#: Kopfzeile, mit der ein Server die Wartezeit vorgibt. Sie schlägt den berechneten Backoff.
SOURCE_RETRY_AFTER_HEADER: Final = "Retry-After"

#: Feldnamen, die eine ``mapping:``-Sektion in ``sources.yaml`` belegen darf (§8.4). Sie
#: entsprechen den Inhaltsfeldern des ``SourceDocument``.
SOURCE_MAPPING_FIELDS: Final = ("title", "description", "body", "resource", "tags", "updated_at")

# ---------------------------------------------------------------------------
# Mock-Quellserver (§9)
# ---------------------------------------------------------------------------

MOCK_HOST: Final = "0.0.0.0"
MOCK_PORT: Final = 8090

#: Verzeichnis der Seed-Daten im Container (§9.2). Auf dem Host liegt es als ``./fixtures``.
MOCK_FIXTURES_DIR: Final = "/app/fixtures"

#: Präfix der Steuerungs-API (§9.3). Es liegt bewusst unter einem eigenen Pfad, damit kein
#: nachgebildeter Quell-Endpunkt versehentlich damit kollidiert.
MOCK_CONTROL_PREFIX: Final = "/_control"

# ---------------------------------------------------------------------------
# Sync-Orchestrierung (§10, §16.3)
# ---------------------------------------------------------------------------

#: Namensraum des Advisory-Locks je Quelle (§10.5). Getrennt vom Migrations-Namensraum, damit
#: ein laufender Sync keine Migration blockiert und umgekehrt.
SYNC_LOCK_NAMESPACE: Final = "wissensgraph.sync"

#: Nach so vielen Dokumenten schreibt ein Lauf seinen Zwischenstand in ``runs.stats``. Klein genug,
#: dass die UI bei einem großen Bestand etwas sieht; groß genug, dass die Zwischenstände den Lauf
#: nicht dominieren.
SYNC_PROGRESS_INTERVAL: Final = 100

#: Schlüssel in ``runs.params``. Sie stehen hier und nicht als Literal in der Abfrage, weil
#: ``active_for_source`` (§10.5) danach sucht: Ein Tippfehler auf einer der beiden Seiten machte
#: die Nebenläufigkeitsprüfung wirkungslos, ohne dass etwas fehlschlüge.
RUN_PARAM_SOURCE: Final = "source"
RUN_PARAM_FULL: Final = "full"
RUN_PARAM_DRY_RUN: Final = "dry_run"

#: Wie viele Läufe ``wg runs list`` ohne weitere Angabe zeigt.
RUNS_LIST_LIMIT: Final = 20

# ---------------------------------------------------------------------------
# Job-Queue und Worker (§5.1, §16.3)
# ---------------------------------------------------------------------------

#: Schlüssel der Redis-Liste, über die Jobs laufen.
QUEUE_KEY: Final = "wg:jobs"

#: Wie lange ein Worker auf einen Job wartet, bevor er einmal durchatmet. Die Frist ist der Grund,
#: warum er sich sauber beenden lässt: Zwischen zwei Wartezeiten prüft er sein Abbruchsignal.
QUEUE_RESERVE_TIMEOUT_SECONDS: Final = 5.0

# ---------------------------------------------------------------------------
# HTTP-API (§16, §20.3)
# ---------------------------------------------------------------------------

API_HOST: Final = "0.0.0.0"
API_PORT: Final = 8080
API_AUTH_MODE: Final = "token"
API_CORS_ORIGINS: Final = "http://localhost:5173"

#: Nur an diese Adressen darf ``auth_mode=none`` gebunden werden (§20.3).
API_LOOPBACK_HOSTS: Final = ("127.0.0.1", "::1", "localhost")

# ---------------------------------------------------------------------------
# MCP-Server (§18)
# ---------------------------------------------------------------------------

MCP_TRANSPORT: Final = "stdio"
MCP_PORT: Final = 8081

# ---------------------------------------------------------------------------
# Model-Router (§11)
# ---------------------------------------------------------------------------

PERSONAL_ALLOW_REMOTE_MODELS: Final = False
MODEL_TIMEOUT_SECONDS: Final = 60
MODEL_MAX_RETRIES: Final = 3
MODEL_CACHE_ENABLED: Final = True
MODEL_CACHE_TTL_HOURS: Final = 168

# ---------------------------------------------------------------------------
# Läufe: Clustering (§6.3, §13)
# ---------------------------------------------------------------------------

CLUSTERING_NEIGHBORS_K: Final = 8
CLUSTERING_MIN_CLUSTER_SIZE: Final = 3
CLUSTERING_MAX_CLUSTER_SIZE: Final = 25
CLUSTERING_STABILITY_RUNS: Final = 2
CLUSTERING_RELATED_CLUSTER_TOP_N: Final = 3
CLUSTERING_RELABEL_ON_MEMBER_CHANGE_PCT: Final = 20

# ---------------------------------------------------------------------------
# Läufe: Verwaiste Knoten (§15.4)
# ---------------------------------------------------------------------------

ORPHANS_LOOSE_THRESHOLD: Final = 1
ORPHANS_PROXIMITY_TOP_N: Final = 30
ORPHANS_PROXIMITY_AUTO_COMMIT: Final = 0.85
ORPHANS_PROXIMITY_CANDIDATE_BAND: Final = 0.60
ORPHANS_USE_LLM: Final = True
ORPHANS_CLUSTER_SUGGESTION_LIMIT: Final = 2
ORPHANS_CLUSTER_PREVIEW_MEMBERS: Final = 15
ORPHANS_MIN_CONFIDENCE: Final = 0.60

# ---------------------------------------------------------------------------
# Traversierung und Ranking (§12.3)
# ---------------------------------------------------------------------------

TRAVERSAL_DEFAULT_HOPS: Final = 2
TRAVERSAL_MAX_HOPS: Final = 5
TRAVERSAL_MAX_NODES: Final = 400
RANKING_HOP_WEIGHT: Final = 0.5
RANKING_DENSITY_WEIGHT: Final = 0.3
RANKING_RECENCY_WEIGHT: Final = 0.2
RANKING_RECENCY_HALF_LIFE_DAYS: Final = 90

# ---------------------------------------------------------------------------
# Budget-Wächter (§11.6) — schützt vor unbeabsichtigtem Token-Verbrauch
# ---------------------------------------------------------------------------

BUDGET_MAX_MODEL_CALLS_PER_RUN: Final = 2000
BUDGET_MAX_ESTIMATED_COST_PER_RUN_EUR: Final = 5.0
BUDGET_ON_EXCEED: Final = "abort"
