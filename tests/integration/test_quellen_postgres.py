"""Der Fixture-Korpus in einer echten Datenbank (§24 Abnahme Stufe 3, §22.1).

Dieselben Kriterien laufen in ``tests/unit/test_ingest.py`` gegen die speicherresidenten Ports.
Hier geht es um das, was ein Fake nicht zeigen kann: dass 200 Konzepte und 200 Kanten wirklich
durch das Schema aus §7.4 passen — mit JSONB-Tags, ``TIMESTAMPTZ`` aus der Quelle, dem
eindeutigen Index auf dem Kanten-Tripel und der generierten Suchspalte.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select

from support import quellen
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourceConfig
from wissensgraph.infrastructure.adapters import ConfluenceAdapter, JiraAdapter
from wissensgraph.infrastructure.db import StoreRegistry, upgrade_all
from wissensgraph.infrastructure.db.tables import change_log, concepts, edges
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.sources import IngestReport, SourceIngestService

pytestmark = pytest.mark.integration

SEITEN = 120
VORGAENGE = 80


def nicht_warten(_seconds: float) -> None:
    """Der Backoff soll im Test keine Zeit kosten."""


@pytest.fixture
def migrated(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    """Beide Testdatenbanken auf dem Stand des Schemas aus §7.4."""
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


class Umgebung:
    """Mock-Server, Adapter und Dienste auf einer echten Datenbank."""

    def __init__(self, settings: Settings, registry: StoreRegistry) -> None:
        self.app = quellen.mock_app()
        self.registry = registry
        self.concepts = ConceptService(settings, UnitOfWorkFactory(registry))
        self.ingest = SourceIngestService(self.concepts, known_prefixes=("confluence", "jira"))
        self.confluence_cfg = quellen.quelle(
            "confluence-eng",
            adapter="confluence",
            id_prefix="confluence",
            base_url=quellen.CONFLUENCE_BASE,
        )
        self.jira_cfg = quellen.quelle(
            "jira-team",
            adapter="jira",
            id_prefix="jira",
            base_url=quellen.JIRA_BASE,
            default_type="Jira Issue",
        )
        self.confluence = self._bauen(ConfluenceAdapter, self.confluence_cfg)
        self.jira = self._bauen(JiraAdapter, self.jira_cfg)

    def _bauen(self, klasse: type, cfg: SourceConfig) -> Any:
        adapter = klasse(client_factory=quellen.client_factory(self.app), sleep=nicht_warten)
        adapter.configure(cfg)
        return adapter

    def lauf_confluence(self, cursor: Any = None) -> IngestReport:
        return self.ingest.ingest(self.confluence, self.confluence_cfg, cursor=cursor)

    def lauf_jira(self, cursor: Any = None) -> IngestReport:
        return self.ingest.ingest(self.jira, self.jira_cfg, cursor=cursor)

    def zaehle(self, tabelle: Any) -> int:
        with self.registry.engine("shared").connect() as connection:
            return int(connection.execute(select(func.count()).select_from(tabelle)).scalar_one())

    def eine_zeile(self, tabelle: Any, bedingung: Any) -> Any:
        with self.registry.engine("shared").connect() as connection:
            return connection.execute(select(tabelle).where(bedingung)).mappings().first()


@pytest.fixture
def umgebung(postgres_settings: Settings, migrated: StoreRegistry) -> Iterator[Umgebung]:
    yield Umgebung(postgres_settings, migrated)


class TestAbnahme:
    def test_der_korpus_ist_vollstaendig_abgebildet(self, umgebung: Umgebung) -> None:
        umgebung.lauf_confluence()
        umgebung.lauf_jira()

        assert umgebung.zaehle(concepts) == SEITEN + VORGAENGE

    def test_zweiter_lauf_ohne_aenderung_schreibt_nichts(self, umgebung: Umgebung) -> None:
        umgebung.lauf_confluence()
        vorher = umgebung.zaehle(change_log)

        bericht = umgebung.lauf_confluence()

        assert bericht.unchanged == SEITEN
        assert umgebung.zaehle(change_log) == vorher

    def test_das_aenderungsszenario_wirkt_genau_einmal(self, umgebung: Umgebung) -> None:
        umgebung.lauf_confluence()
        cursor = umgebung.confluence.next_cursor()
        quellen.control(umgebung.app).post("/_control/scenario/incremental_update")

        bericht = umgebung.lauf_confluence(cursor)
        zeile = umgebung.eine_zeile(concepts, concepts.c.id == "confluence:100001")

        assert (bericht.documents, bericht.updated) == (1, 1)
        assert zeile["title"] == "Nächtlicher ETL-Lauf (überarbeitet)"


class TestSchreibverhalten:
    def test_tags_und_zeitpunkte_kommen_unveraendert_zurueck(self, umgebung: Umgebung) -> None:
        umgebung.lauf_confluence()

        zeile = umgebung.eine_zeile(concepts, concepts.c.id == "confluence:100001")

        assert zeile["tags"] == ["daten", "datenpipeline"]
        assert zeile["source_name"] == "confluence-eng"
        assert zeile["external_id"] == "100001"
        assert zeile["source_updated_at"] is not None
        assert zeile["source_updated_at"].tzinfo is not None

    def test_die_generierte_suchspalte_findet_den_korpus(self, umgebung: Umgebung) -> None:
        from sqlalchemy import text

        umgebung.lauf_confluence()

        with umgebung.registry.engine("shared").connect() as connection:
            treffer = connection.execute(
                text(
                    "SELECT count(*) FROM concepts "
                    "WHERE search_tsv @@ to_tsquery('simple', 'warehouse')"
                )
            ).scalar_one()

        assert treffer > 10

    def test_alles_liegt_im_store_des_scopes(self, umgebung: Umgebung) -> None:
        """§20.1: 'personal' bleibt unberührt, auch wenn zwei Quellen laufen."""
        umgebung.lauf_confluence()
        umgebung.lauf_jira()

        with umgebung.registry.engine("personal").connect() as connection:
            assert connection.execute(select(func.count()).select_from(concepts)).scalar_one() == 0


class TestKanten:
    def test_quellverweise_werden_kanten(self, umgebung: Umgebung) -> None:
        umgebung.lauf_confluence()

        assert umgebung.zaehle(edges) == 118

    def test_beide_erzeugerkennungen_kommen_vor(self, umgebung: Umgebung) -> None:
        """§8.5 verlangt 'code:source-reference'; Textverweise tragen 'code:body-reference'."""
        umgebung.lauf_confluence()
        umgebung.lauf_jira()

        with umgebung.registry.engine("shared").connect() as connection:
            erzeuger = set(connection.execute(select(edges.c.generated_by).distinct()).scalars())

        assert erzeuger == {
            defaults.GENERATED_BY_SOURCE_REFERENCE,
            defaults.GENERATED_BY_BODY_REFERENCE,
        }

    def test_unaufgeloeste_kanten_werden_beim_naechsten_lauf_aufgeloest(
        self, umgebung: Umgebung
    ) -> None:
        """§8.5: Eine Kante auf ein fehlendes Ziel entsteht unaufgelöst und wird jeden Lauf
        erneut geprüft."""
        umgebung.lauf_jira()

        with umgebung.registry.engine("shared").connect() as connection:
            offen = connection.execute(
                select(func.count()).select_from(edges).where(edges.c.resolved.is_(False))
            ).scalar_one()

        bericht = umgebung.lauf_confluence()

        with umgebung.registry.engine("shared").connect() as connection:
            danach = connection.execute(
                select(func.count()).select_from(edges).where(edges.c.resolved.is_(False))
            ).scalar_one()

        # Der Confluence-Lauf löst mehr auf als die 80 Verweise aus Jira: Auch innerhalb der
        # eigenen Quelle zeigt eine Seite auf die nächste, die es beim Anlegen noch nicht gab.
        assert offen == VORGAENGE
        assert bericht.edges_resolved >= VORGAENGE
        assert danach == 0

    def test_ein_zweiter_lauf_erzeugt_keine_doppelten_kanten(self, umgebung: Umgebung) -> None:
        """Der eindeutige Index auf dem Tripel (§7.4) würde das sonst als Fehler abweisen."""
        umgebung.lauf_confluence()
        vorher = umgebung.zaehle(edges)

        umgebung.lauf_confluence()

        assert umgebung.zaehle(edges) == vorher
