"""Tabellenbeschreibungen für Abfragen (§7.4).

Diese Definitionen erzeugen **kein** Schema. Das tut allein die Migration aus Stufe 1, und zwar
als SQL — ein zweiter Erzeuger desselben Schemas wäre genau die Doppelung, die auseinanderdriftet.
Hier steht nur, was zum Formulieren von Abfragen nötig ist: Namen, Typen und Nullbarkeit.

Damit diese Beschreibung und die Migration nicht auseinanderlaufen, gleicht ein Guard-Test sie
gegen eine wirklich migrierte Datenbank ab (``tests/guards/test_schema_abgleich.py``). Ein
hinzugefügter Spaltenname in der Migration, der hier fehlt, fällt dort auf.

Der Metadaten-Container ist bewusst ohne Schema-Präfix: Beide Stores haben identische Tabellen in
je eigenen Datenbanken, unterschieden wird über die Verbindung, nie über einen Namensraum.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def _timestamp(name: str, *, nullable: bool = True) -> Column[datetime]:
    """Eine Zeitspalte mit Zeitzone (``TIMESTAMPTZ``).

    Ohne Zeitzone wäre jeder Vergleich zwischen einem in Deutschland erfassten und einem im
    Container in UTC geschriebenen Zeitpunkt stillschweigend falsch.
    """
    return Column(name, DateTime(timezone=True), nullable=nullable)


concepts = Table(
    "concepts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("title", Text),
    Column("description", Text),
    Column("body", Text),
    Column("resource", Text),
    Column("tags", JSONB, nullable=False),
    Column("audience", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    _timestamp("stale_after"),
    Column("content_hash", Text),
    Column("source_name", Text),
    Column("external_id", Text),
    _timestamp("source_updated_at"),
    Column("generated_by", Text),
    _timestamp("generated_at"),
    Column("verified_by", Text),
    _timestamp("verified_at"),
    Column("curated", Boolean, nullable=False),
    _timestamp("created_at", nullable=False),
    _timestamp("updated_at", nullable=False),
)

edges = Table(
    "edges",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("from_store", Text, nullable=False),
    Column("from_id", Text, nullable=False),
    Column("to_store", Text, nullable=False),
    Column("to_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("weight", Float),
    Column("confidence", Float),
    Column("reasoning", Text),
    Column("resolved", Boolean, nullable=False),
    Column("generated_by", Text),
    _timestamp("generated_at"),
    Column("verified_by", Text),
    _timestamp("verified_at"),
    Column("curated", Boolean, nullable=False),
    _timestamp("created_at", nullable=False),
)

change_log = Table(
    "change_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("concept_id", Text),
    Column("edge_id", UUID(as_uuid=True)),
    _timestamp("changed_at", nullable=False),
    Column("change_type", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("run_id", UUID(as_uuid=True)),
    Column("detail", JSONB),
)

runs = Table(
    "runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    _timestamp("started_at"),
    _timestamp("finished_at"),
    Column("progress", Float, nullable=False),
    Column("stats", JSONB, nullable=False),
    Column("error", Text),
)

source_cursors = Table(
    "source_cursors",
    metadata,
    Column("source_name", Text, primary_key=True),
    Column("cursor", JSONB, nullable=False),
    _timestamp("last_full_sync"),
    _timestamp("updated_at", nullable=False),
)

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True)),
    Column("task", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("store", Text),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("latency_ms", Integer),
    Column("cost_estimate", Numeric(10, 5)),
    Column("cache_hit", Boolean, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", Text, nullable=False),
    _timestamp("created_at", nullable=False),
)

#: Die Vektordimension steht hier bewusst **nicht**. Sie kommt aus ``WG_EMBEDDING_DIM`` und geht
#: als ``vector(n)`` in die Migration ein (§7.3); eine zweite Angabe an dieser Stelle wäre eine
#: zweite Wahrheit über dieselbe Zahl. Für das Formulieren von Abfragen genügt der Typ ohne Länge.
concept_embeddings = Table(
    "concept_embeddings",
    metadata,
    Column("concept_id", Text, primary_key=True),
    Column("model_key", Text, primary_key=True),
    Column("dim", Integer, nullable=False),
    Column("embedding", Vector(), nullable=False),
    Column("source_hash", Text, nullable=False),
    _timestamp("created_at", nullable=False),
)

cluster_centroids = Table(
    "cluster_centroids",
    metadata,
    Column("cluster_id", Text, primary_key=True),
    Column("model_key", Text, nullable=False),
    Column("embedding", Vector(), nullable=False),
    Column("member_count", Integer, nullable=False),
    _timestamp("updated_at", nullable=False),
)

cluster_assignment_candidates = Table(
    "cluster_assignment_candidates",
    metadata,
    Column("concept_id", Text, primary_key=True),
    Column("cluster_id", Text, primary_key=True),
    Column("score", Float, nullable=False),
    Column("seen_count", Integer, nullable=False),
    Column("first_seen_run", UUID(as_uuid=True), nullable=False),
    Column("last_seen_run", UUID(as_uuid=True), nullable=False),
    _timestamp("last_seen_at", nullable=False),
    # §13.4: "Mitglied wurde von Hand entfernt -> wird nicht erneut zugeordnet; Ausschluss in
    # cluster_assignment_candidates vermerkt." Die Spalte kam mit Stufe 8 dazu, weil die
    # Kandidatentabelle der einzige Ort ist, an dem eine *nicht* bestehende Mitgliedschaft
    # festgehalten werden kann — eine gelöschte Kante hinterlässt keinen Vermerk.
    Column("excluded", Boolean, nullable=False),
)

#: Sicht auf lose Knoten (§7.4, §15.1). Als Tabellenobjekt beschrieben, damit die Abfrage darauf
#: aus derselben Quelle kommt wie jede andere; angelegt wird sie in der Migration als ``VIEW``.
loose_concepts = Table(
    "v_loose_concepts",
    metadata,
    # Ohne Primärschlüssel und durchweg nullbar: Eine Sicht hat keine Constraints, und PostgreSQL
    # meldet jede ihrer Spalten als nullbar — auch die, die es in der zugrunde liegenden Tabelle
    # nicht ist. Eine strengere Beschreibung wäre hier eine Behauptung über etwas, das die
    # Datenbank gar nicht zusichert.
    Column("id", Text),
    Column("scope", Text),
    Column("type", Text),
    Column("title", Text),
    Column("semantic_degree", BigInteger),
)

#: Spalten, die in der Datenbank stehen, hier aber absichtlich fehlen. ``search_tsv`` ist eine
#: generierte Spalte (§7.4): Sie lässt sich nicht beschreiben und wird erst mit der lexikalischen
#: Suche in Stufe 6 gelesen.
NICHT_ABGEBILDETE_SPALTEN: frozenset[str] = frozenset({"search_tsv"})
