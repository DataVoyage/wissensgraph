"""Die Abnahme der Stufe 2 gegen echtes PostgreSQL (§24, §22.1).

Dieselben vier Kriterien laufen in ``tests/unit/test_concept_service.py`` gegen die
speicherresidenten Ports. Hier geht es um das, was ein Fake nicht zeigen kann: dass die
SQL-Anweisungen stimmen, dass ``INSERT … ON CONFLICT DO UPDATE`` das Richtige tut, dass JSONB
und ``TIMESTAMPTZ`` unverfälscht zurückkommen und dass ein Rollback wirklich rollt.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeType
from wissensgraph.domain.concepts import ConceptDraft, ConceptStatus
from wissensgraph.domain.upsert import UpsertOutcome
from wissensgraph.infrastructure.db import StoreRegistry, upgrade_all
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.services.concepts import ConceptService

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    """Beide Testdatenbanken auf dem Stand des Schemas aus §7.4."""
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


@pytest.fixture
def service(postgres_settings: Settings, migrated: StoreRegistry) -> Iterator[ConceptService]:
    yield ConceptService(postgres_settings, UnitOfWorkFactory(migrated))


def seite(**overrides: object) -> ConceptDraft:
    """Eine gespiegelte Confluence-Seite im Store ``shared``."""
    werte: dict[str, object] = {
        "id": "confluence:1",
        "scope": "engineering",
        "type": "Confluence Page",
        "title": "Titel",
        "body": "Inhalt",
        "source_name": "confluence",
        "external_id": "1",
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


def journal(migrated: StoreRegistry, store: str, concept_id: str) -> tuple[ChangeType, ...]:
    """Die Änderungsarten zu einem Konzept, älteste zuerst."""
    with UnitOfWorkFactory(migrated)(store) as uow:
        eintraege = uow.changes.entries_for(concept_id)
    return tuple(entry.change_type for entry in reversed(eintraege))


class TestAbnahme:
    def test_zweifaches_upsert_erzeugt_genau_einen_eintrag(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        erstes = service.upsert(seite())
        zweites = service.upsert(seite())

        assert erstes.outcome is UpsertOutcome.CREATED
        assert zweites.outcome is UpsertOutcome.UNCHANGED
        assert journal(migrated, "shared", "confluence:1") == (ChangeType.CREATED,)

    def test_geaenderter_hash_erzeugt_den_zweiten_eintrag(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite())
        service.upsert(seite(body="Inhalt, überarbeitet"))

        assert journal(migrated, "shared", "confluence:1") == (
            ChangeType.CREATED,
            ChangeType.UPDATED,
        )

    def test_kante_auf_unbekanntes_ziel_entsteht_ohne_fehler(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite(body="Siehe [[confluence:999]]"))

        with UnitOfWorkFactory(migrated)("shared") as uow:
            (kante,) = uow.edges.list_outgoing("confluence:1")

        assert kante.to_id == "confluence:999"
        assert kante.resolved is False

    def test_kuratiertes_feld_ueberlebt_und_erzeugt_einen_konfliktvermerk(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(
            ConceptDraft(
                id="cluster:a",
                scope="engineering",
                type="Cluster",
                title="Von Hand benannt",
                curated=True,
            ),
            actor="user:mn",
        )

        ergebnis = service.upsert(
            ConceptDraft(
                id="cluster:a",
                scope="engineering",
                type="Cluster",
                title="Aus der Quelle",
                source_name="confluence",
                external_id="c-a",
            )
        )

        with UnitOfWorkFactory(migrated)("shared") as uow:
            concept = uow.concepts.get("cluster:a")

        assert ergebnis.outcome is UpsertOutcome.CONFLICT
        assert concept is not None
        assert concept.title == "Von Hand benannt"
        assert ChangeType.CURATION_CONFLICT in journal(migrated, "shared", "cluster:a")


class TestSchreibverhalten:
    def test_felder_kommen_unveraendert_zurueck(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        quelle_geaendert = datetime(2026, 8, 30, 10, 30, tzinfo=UTC)

        service.upsert(
            seite(
                tags=("a", "b"),
                audience=("team:platform",),
                status=ConceptStatus.DRAFT,
                source_updated_at=quelle_geaendert,
                resource="https://example.invalid/1",
            )
        )

        with UnitOfWorkFactory(migrated)("shared") as uow:
            concept = uow.concepts.get("confluence:1")

        assert concept is not None
        assert concept.tags == ("a", "b")
        assert concept.audience == ("team:platform",)
        assert concept.status is ConceptStatus.DRAFT
        assert concept.source_updated_at == quelle_geaendert
        assert concept.store == "shared"

    def test_created_at_bleibt_beim_update_stehen(
        self, postgres_settings: Settings, migrated: StoreRegistry
    ) -> None:
        """§10.2: Ein Update rührt ``created_at`` nicht an und setzt ``updated_at`` neu.

        Die Zeitpunkte sind hier **gestellt** und nicht der Uhr überlassen. Das ist der Grund,
        warum ``ConceptService`` eine Uhr als Parameter nimmt: Beide Upserts liefen zuvor über
        ``datetime.now()``, und ob dabei zwei verschiedene Werte herauskommen, hängt an der
        Auflösung der Systemuhr — unter Windows sind das rund 15 ms, also durchaus ein Tick für
        beide Aufrufe. Der Test war damit nicht falsch, sondern von der Geschwindigkeit des
        Rechners abhängig; mit gestellten Zeitpunkten prüft er die Aussage selbst.
        """
        angelegt = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        geaendert = datetime(2026, 3, 1, 12, 5, tzinfo=UTC)
        # Eine gestellte Uhr, die stehen bleibt, statt einer Folge fester Werte: Ein Upsert darf
        # die Zeit mehrfach lesen, ohne dass der Test daran zerbricht.
        jetzt = angelegt
        service = ConceptService(
            postgres_settings, UnitOfWorkFactory(migrated), clock=lambda: jetzt
        )

        service.upsert(seite())
        with UnitOfWorkFactory(migrated)("shared") as uow:
            erst = uow.concepts.get("confluence:1")

        jetzt = geaendert
        service.upsert(seite(body="neu"))
        with UnitOfWorkFactory(migrated)("shared") as uow:
            danach = uow.concepts.get("confluence:1")

        assert erst is not None
        assert danach is not None
        assert erst.created_at == angelegt
        assert danach.created_at == angelegt
        assert danach.updated_at == geaendert

    def test_generierte_suchspalte_wird_von_der_datenbank_gefuellt(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        """``search_tsv`` steht nicht in den Tabellenbeschreibungen — die Datenbank füllt sie."""
        from sqlalchemy import text

        service.upsert(seite(title="Zahlungsabgleich", body="Der monatliche Lauf."))

        with migrated.engine("shared").connect() as connection:
            treffer = connection.execute(
                text(
                    "SELECT id FROM concepts "
                    "WHERE search_tsv @@ to_tsquery('simple', 'zahlungsabgleich')"
                )
            ).scalar()

        assert treffer == "confluence:1"

    def test_die_beiden_stores_bleiben_getrennt(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite())
        service.upsert(ConceptDraft(id="note:a", scope="personal", type="Note", title="Notiz"))

        factory = UnitOfWorkFactory(migrated)
        with factory("shared") as uow:
            assert uow.concepts.get("note:a") is None
        with factory("personal") as uow:
            assert uow.concepts.get("confluence:1") is None


class TestKantenPflege:
    def test_referenzen_werden_fortgeschrieben(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite(body="[[confluence:2]] und [[confluence:3]]"))
        service.upsert(seite(body="nur noch [[confluence:3]] und neu [[confluence:4]]"))

        with UnitOfWorkFactory(migrated)("shared") as uow:
            ziele = {edge.to_id for edge in uow.edges.list_outgoing("confluence:1")}

        assert ziele == {"confluence:3", "confluence:4"}

    def test_nachtraeglich_angelegtes_ziel_wird_aufgeloest(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite(body="Siehe [[confluence:2]]"))
        service.upsert(seite(id="confluence:2", external_id="2", body="Ziel"))

        anzahl = service.refresh_edge_resolution("shared")

        with UnitOfWorkFactory(migrated)("shared") as uow:
            (kante,) = uow.edges.list_outgoing("confluence:1")

        assert anzahl == 1
        assert kante.resolved is True

    def test_erneutes_aufloesen_findet_nichts_mehr(
        self, service: ConceptService, migrated: StoreRegistry
    ) -> None:
        service.upsert(seite(body="Siehe [[confluence:2]]"))
        service.upsert(seite(id="confluence:2", external_id="2", body="Ziel"))
        service.refresh_edge_resolution("shared")

        assert service.refresh_edge_resolution("shared") == 0


class TestTransaktion:
    def test_ein_abbruch_schreibt_nichts(self, migrated: StoreRegistry) -> None:
        """§10.2 Regel 5 auf der Ebene, auf der sie zählt: der echten Transaktion."""
        factory = UnitOfWorkFactory(migrated)
        vorlage = seite()

        with pytest.raises(RuntimeError), factory("shared") as uow:
            uow.concepts.save(
                _als_konzept(vorlage, store="shared", now=datetime.now(UTC)),
            )
            raise RuntimeError("Abbruch mitten im Vorgang")

        with factory("shared") as uow:
            assert uow.concepts.get("confluence:1") is None

    def test_die_arbeitseinheit_verlangt_den_kontextmanager(self, migrated: StoreRegistry) -> None:
        uow = UnitOfWorkFactory(migrated)("shared")

        with pytest.raises(RuntimeError, match="Kontextmanager"):
            uow.concepts.get("confluence:1")


def _als_konzept(draft: ConceptDraft, *, store: str, now: datetime):
    """Baut aus einem Entwurf das Konzept, das ein erster Upsert erzeugen würde."""
    from wissensgraph.domain.concepts import Concept

    return Concept.model_validate(
        {
            **draft.model_dump(exclude={"references"}),
            "store": store,
            "content_hash": draft.content_hash,
            "created_at": now,
            "updated_at": now,
        }
    )
