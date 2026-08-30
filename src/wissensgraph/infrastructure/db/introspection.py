"""Nachschlagen dessen, was tatsächlich in der Datenbank steht (§7.4).

Ein Migrationsstand sagt aus, welche Skripte gelaufen sind — nicht, ob das Ergebnis noch zur
aktuellen Konfiguration passt. Der wichtigste Fall dafür ist die Vektordimension: Sie steht als
``vector(n)`` im Schema, stammt aber aus ``WG_EMBEDDING_DIM``. Wird die Variable nach der
Migration verändert, ist das Schema still veraltet, und der erste Embedding-Lauf scheitert mitten
im Betrieb statt beim Start (§11.7).

Diese Funktionen beantworten solche Fragen, ohne dass Aufrufer eigenes SQL schreiben. Sie werden
von ``wg doctor`` und von den Integrationstests der Stufe 1 gleichermaßen benutzt.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

#: ``format_type`` liefert für eine pgvector-Spalte den Text ``vector(768)``.
_VECTOR_TYPE = re.compile(r"^vector\((?P<dim>\d+)\)$")


def table_exists(connection: Connection, table: str) -> bool:
    """Ob eine Tabelle oder Sicht im Suchpfad der Verbindung existiert."""
    found = connection.execute(text("SELECT to_regclass(:name)"), {"name": table}).scalar()
    return found is not None


def extension_installed(connection: Connection, extension: str) -> bool:
    """Ob eine PostgreSQL-Erweiterung in dieser Datenbank installiert ist (§7.3)."""
    found = connection.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = :name"), {"name": extension}
    ).scalar()
    return found is not None


def index_method(connection: Connection, index: str) -> str | None:
    """Die Zugriffsmethode eines Index, etwa ``hnsw`` oder ``gin``; ``None``, wenn er fehlt.

    Nicht nur die Existenz, sondern die Methode: §24 verlangt für Stufe 1 ausdrücklich, dass ein
    *HNSW*-Index existiert. Ein versehentlich als B-Tree angelegter Index gleichen Namens würde
    eine reine Existenzprüfung bestehen und die Ähnlichkeitssuche trotzdem unbrauchbar machen.
    """
    return connection.execute(
        text(
            """
            SELECT am.amname
            FROM pg_class i
            JOIN pg_index ix ON ix.indexrelid = i.oid
            JOIN pg_am am ON am.oid = i.relam
            WHERE i.relname = :name
            """
        ),
        {"name": index},
    ).scalar()


def constraint_exists(connection: Connection, table: str, constraint: str) -> bool:
    """Ob eine benannte Tabellen-Constraint existiert (§7.4, §20.1)."""
    found = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = :table AND c.conname = :constraint
            """
        ),
        {"table": table, "constraint": constraint},
    ).scalar()
    return found is not None


def vector_dimension(connection: Connection, table: str, column: str) -> int | None:
    """Die im Schema festgelegte Dimension einer pgvector-Spalte.

    Gibt ``None`` zurück, wenn Tabelle oder Spalte fehlen oder die Spalte keine Vektorspalte ist —
    für einen Aufrufer sind alle drei Fälle dasselbe: "hier steht keine Dimension, die geprüft
    werden könnte".
    """
    rendered = connection.execute(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass(:table)
              AND a.attname = :column
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ),
        {"table": table, "column": column},
    ).scalar()

    if not isinstance(rendered, str):
        return None
    match = _VECTOR_TYPE.match(rendered)
    return int(match.group("dim")) if match else None
