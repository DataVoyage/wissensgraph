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

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    MetaData,
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

#: Spalten, die in der Datenbank stehen, hier aber absichtlich fehlen. ``search_tsv`` ist eine
#: generierte Spalte (§7.4): Sie lässt sich nicht beschreiben und wird erst mit der lexikalischen
#: Suche in Stufe 6 gelesen.
NICHT_ABGEBILDETE_SPALTEN: frozenset[str] = frozenset({"search_tsv"})
