"""Ausgangsschema beider Stores nach §7.4.

Revision ID: 0001_initial_schema
Revises:
Erstellt: Stufe 1 des Stufenplans (§24)

Die DDL steht bewusst als SQL und nicht als SQLAlchemy-Metadaten. §7.4 gibt sie wörtlich vor, und
in dieser Form lässt sie sich Zeile für Zeile gegen das Dokument prüfen. Mehrere Konstrukte —
eine generierte ``tsvector``-Spalte, ein partieller Unique-Index, ein HNSW-Index von pgvector —
wären als Metadaten ohnehin nur über ``text()``-Ausdrücke darstellbar.

Zwei Werte kommen von außen (siehe ``migration_context``): der Store-Name, weil §7.4 den
CHECK-Constraint gegen personal-Verweise ausdrücklich nur im shared-Store anlegt, und die
Vektordimension aus ``WG_EMBEDDING_DIM``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from wissensgraph.config import defaults
from wissensgraph.migrations.context import current_options

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Legt Erweiterungen, Tabellen, Indizes, Invarianten und Sichten an."""
    options = current_options()
    dim = options.embedding_dim

    _create_extensions()
    _create_concepts(store=options.store)
    _create_edges(is_shared=options.is_shared)
    _create_embeddings(dim)
    _create_clustering(dim)
    _create_journal_and_runs()
    _create_loose_concepts_view()


def downgrade() -> None:
    """Nimmt alles zurück, was ``upgrade`` angelegt hat.

    Die Erweiterungen bleiben stehen. Sie sind Eigenschaft der Datenbank und nicht dieses Schemas;
    ein ``DROP EXTENSION vector`` würde alles mitnehmen, was sonst noch Vektorspalten benutzt.
    """
    op.execute("DROP VIEW IF EXISTS v_loose_concepts")
    for table in (
        "model_calls",
        "source_cursors",
        "runs",
        "change_log",
        "cluster_assignment_candidates",
        "cluster_centroids",
        "concept_embeddings",
        "edges",
        "concepts",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")


# ---------------------------------------------------------------------------
# Erweiterungen (§7.3)
# ---------------------------------------------------------------------------


def _create_extensions() -> None:
    """Legt die von §7.3 geforderten Erweiterungen an.

    Die Migration verlässt sich dabei nicht auf ein Init-Skript des Datenbank-Images: Eine
    Datenbank, die von Hand oder in einem Test angelegt wurde, hat kein solches Skript gesehen.
    ``IF NOT EXISTS`` macht den Schritt in beiden Fällen wiederholbar.
    """
    for extension in defaults.REQUIRED_EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


# ---------------------------------------------------------------------------
# concepts (§7.4)
# ---------------------------------------------------------------------------


def _create_concepts(*, store: str) -> None:
    op.execute(
        """
        CREATE TABLE concepts (
            id                TEXT PRIMARY KEY,
            store             TEXT NOT NULL,
            scope             TEXT NOT NULL,
            type              TEXT NOT NULL,
            title             TEXT,
            description       TEXT,
            body              TEXT,
            resource          TEXT,
            tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
            audience          JSONB NOT NULL DEFAULT '[]'::jsonb,
            status            TEXT NOT NULL DEFAULT 'stable',
            stale_after       TIMESTAMPTZ,
            content_hash      TEXT,
            source_name       TEXT,
            external_id       TEXT,
            source_updated_at TIMESTAMPTZ,
            generated_by      TEXT,
            generated_at      TIMESTAMPTZ,
            verified_by       TEXT,
            verified_at       TIMESTAMPTZ,
            curated           BOOLEAN NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            search_tsv        tsvector GENERATED ALWAYS AS (
                                  to_tsvector(
                                      'simple',
                                      coalesce(title, '') || ' '
                                      || coalesce(description, '') || ' '
                                      || coalesce(body, '')
                                  )
                              ) STORED
        )
        """
    )

    # Ein Quellobjekt darf nur einmal gespiegelt werden. Der Index ist partiell, weil lokal
    # erzeugte Konzepte (Cluster, Notizen) keine Quelle haben und sonst alle miteinander
    # kollidieren würden — in PostgreSQL sind mehrere (NULL, NULL) zwar erlaubt, der partielle
    # Index macht die Absicht aber explizit und hält ihn klein.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_concepts_source ON concepts (source_name, external_id)
            WHERE source_name IS NOT NULL
        """
    )
    op.execute("CREATE INDEX ix_concepts_scope_type ON concepts (scope, type)")
    op.execute("CREATE INDEX ix_concepts_status ON concepts (status)")
    op.execute("CREATE INDEX ix_concepts_tsv ON concepts USING GIN (search_tsv)")
    # Trigrammindex für die lexikalische Suche als Fallback, wenn kein Embedding greift (§12.4).
    op.execute("CREATE INDEX ix_concepts_title_trgm ON concepts USING GIN (title gin_trgm_ops)")

    # Ergänzung über §7.4 hinaus: Die Spalte 'store' ist laut Dokument "redundant, aber explizit".
    # Redundanz ohne Prüfung driftet auseinander, sobald ein Schreibpfad den falschen Wert setzt.
    # Der CHECK bindet die Spalte an die Datenbank, in der die Zeile tatsächlich liegt, und macht
    # eine falsch geroutete Schreiboperation zu einem Fehler statt zu stillem Datenschaden.
    op.execute(f"ALTER TABLE concepts ADD CONSTRAINT ck_concepts_store CHECK (store = '{store}')")


# ---------------------------------------------------------------------------
# edges (§7.4, §7.7)
# ---------------------------------------------------------------------------


def _create_edges(*, is_shared: bool) -> None:
    op.execute(
        """
        CREATE TABLE edges (
            id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            from_store   TEXT NOT NULL,
            from_id      TEXT NOT NULL,
            to_store     TEXT NOT NULL,
            to_id        TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'member',
            weight       DOUBLE PRECISION,
            confidence   DOUBLE PRECISION,
            reasoning    TEXT,
            resolved     BOOLEAN NOT NULL DEFAULT FALSE,
            generated_by TEXT,
            generated_at TIMESTAMPTZ,
            verified_by  TEXT,
            verified_at  TIMESTAMPTZ,
            curated      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_edges_no_self
                CHECK (NOT (from_store = to_store AND from_id = to_id))
        )
        """
    )

    op.execute(
        "CREATE UNIQUE INDEX ux_edges_triple ON edges (from_store, from_id, to_store, to_id, kind)"
    )
    op.execute("CREATE INDEX ix_edges_from ON edges (from_store, from_id, kind)")
    op.execute("CREATE INDEX ix_edges_to ON edges (to_store, to_id, kind)")

    if is_shared:
        # Die zentrale Invariante der Store-Trennung (§7.4, §20.1): Im geteilten Store darf keine
        # Kante einen persönlichen Knoten benennen. Die Brücke läuft immer andersherum — ein
        # Projekt-Konzept in 'personal' verweist auf 'shared' (§7.3). Weil kein Fremdschlüssel
        # über Datenbankgrenzen möglich ist, ist dieser CHECK die einzige Stelle, an der die
        # Datenbank selbst die Richtung erzwingt.
        op.execute(
            """
            ALTER TABLE edges ADD CONSTRAINT ck_shared_no_personal_ref
                CHECK (from_store = 'shared' AND to_store = 'shared')
            """
        )


# ---------------------------------------------------------------------------
# Embeddings und Cluster (§7.4, §13)
# ---------------------------------------------------------------------------


def _create_embeddings(dim: int) -> None:
    op.execute(
        f"""
        CREATE TABLE concept_embeddings (
            concept_id  TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            model_key   TEXT NOT NULL,
            dim         INTEGER NOT NULL,
            embedding   vector({dim}) NOT NULL,
            source_hash TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (concept_id, model_key)
        )
        """
    )

    # HNSW statt IVFFlat: Der Index braucht keine vorab trainierte Liste und liefert auch bei
    # einer noch kleinen, wachsenden Datenmenge brauchbare Nachbarschaften — genau der Fall beim
    # schrittweisen Aufbau des Graphen (§13.1).
    op.execute(
        f"""
        CREATE INDEX ix_emb_hnsw ON concept_embeddings
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = {defaults.HNSW_M}, ef_construction = {defaults.HNSW_EF_CONSTRUCTION})
        """
    )


def _create_clustering(dim: int) -> None:
    op.execute(
        f"""
        CREATE TABLE cluster_centroids (
            cluster_id   TEXT PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
            model_key    TEXT NOT NULL,
            embedding    vector({dim}) NOT NULL,
            member_count INTEGER NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Zuordnungskandidaten der Stabilitätsschwelle (§13.3): Eine Mitgliedschaft wird erst
    # geschrieben, wenn sie über mehrere Läufe hinweg bestehen bleibt. 'seen_count' ist der
    # Zähler dafür; deshalb steht die Tabelle bewusst ohne Fremdschlüssel auf 'concepts' — sie
    # darf auch Kandidaten führen, die es am Ende nie in den Graphen schaffen.
    op.execute(
        """
        CREATE TABLE cluster_assignment_candidates (
            concept_id     TEXT NOT NULL,
            cluster_id     TEXT NOT NULL,
            score          DOUBLE PRECISION NOT NULL,
            seen_count     INTEGER NOT NULL DEFAULT 1,
            first_seen_run UUID NOT NULL,
            last_seen_run  UUID NOT NULL,
            last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (concept_id, cluster_id)
        )
        """
    )


# ---------------------------------------------------------------------------
# Journal, Läufe, Quellen, Modellaufrufe (§7.4)
# ---------------------------------------------------------------------------


def _create_journal_and_runs() -> None:
    op.execute(
        """
        CREATE TABLE change_log (
            id          BIGSERIAL PRIMARY KEY,
            concept_id  TEXT,
            edge_id     UUID,
            changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            change_type TEXT NOT NULL,
            actor       TEXT NOT NULL,
            run_id      UUID,
            detail      JSONB
        )
        """
    )
    op.execute("CREATE INDEX ix_changelog_concept ON change_log (concept_id, changed_at DESC)")
    op.execute("CREATE INDEX ix_changelog_run ON change_log (run_id)")

    op.execute(
        """
        CREATE TABLE runs (
            id          UUID PRIMARY KEY,
            kind        TEXT NOT NULL,
            params      JSONB NOT NULL DEFAULT '{}'::jsonb,
            status      TEXT NOT NULL,
            started_at  TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            progress    DOUBLE PRECISION NOT NULL DEFAULT 0,
            stats       JSONB NOT NULL DEFAULT '{}'::jsonb,
            error       TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE source_cursors (
            source_name    TEXT PRIMARY KEY,
            cursor         JSONB NOT NULL,
            last_full_sync TIMESTAMPTZ,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Kosten, Provenienz und Debugging der Modellaufrufe (§7.4). Diese Tabelle ist die Grundlage
    # des Budget-Wächters aus §11.6 — ohne sie ließe sich weder 'wg models usage' beantworten
    # noch der Token-Verbrauch eines Laufs begrenzen.
    op.execute(
        """
        CREATE TABLE model_calls (
            id            BIGSERIAL PRIMARY KEY,
            run_id        UUID,
            task          TEXT NOT NULL,
            provider      TEXT NOT NULL,
            model         TEXT NOT NULL,
            store         TEXT,
            tokens_in     INTEGER,
            tokens_out    INTEGER,
            latency_ms    INTEGER,
            cost_estimate NUMERIC(10, 5),
            cache_hit     BOOLEAN NOT NULL DEFAULT FALSE,
            attempt       INTEGER NOT NULL DEFAULT 1,
            status        TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _create_loose_concepts_view() -> None:
    """Sicht auf lose Knoten (§7.4) — die Eingabe der Verwaiste-Knoten-Vernetzung (§15).

    Gezählt werden nur nicht-strukturelle Kanten: Ein Konzept, das ausschließlich in einem Cluster
    hängt (``member``), ist thematisch weiterhin unvernetzt und soll hier auftauchen (§7.7).
    """
    op.execute(
        """
        CREATE VIEW v_loose_concepts AS
        SELECT c.id, c.scope, c.type, c.title,
               count(e.id) FILTER (WHERE e.kind <> 'member') AS semantic_degree
        FROM concepts c
        LEFT JOIN edges e
          ON (e.from_id = c.id AND e.from_store = c.store)
          OR (e.to_id   = c.id AND e.to_store   = c.store)
        WHERE c.status <> 'tombstone'
        GROUP BY c.id, c.scope, c.type, c.title
        """
    )
