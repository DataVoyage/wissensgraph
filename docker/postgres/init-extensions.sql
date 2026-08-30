-- Erweiterungen, die beide Stores brauchen (§7.3).
--
-- Läuft einmalig beim ersten Start eines Datenbank-Containers über
-- /docker-entrypoint-initdb.d. Die Tabellen selbst legt Alembic an (Stufe 1) — hier stehen nur
-- die Erweiterungen, weil ihr Anlegen Superuser-Rechte braucht, die der Anwendungsbenutzer
-- später nicht mehr haben soll.

-- Vektorspalten und HNSW-Index für Embeddings und Cluster-Zentroide.
CREATE EXTENSION IF NOT EXISTS vector;

-- Trigramm-Suche für den lexikalischen Fallback, wenn kein Embedding-Modell verfügbar ist
-- (§12.4). Ohne diese Erweiterung degradiert die Suche nicht, sondern fällt ganz aus.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Erzeugung von UUIDs für Kanten-IDs und generierte Konzepte (§7.4, §7.5).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
