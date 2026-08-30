"""Brücken, Store-Trennung und Graph-Engine gegen echtes PostgreSQL (§24, Stufen 5 und 6).

Was hier läuft, lässt sich mit einem Fake grundsätzlich nicht prüfen: zwei getrennte Datenbanken.
Der ganze Punkt der Brückenlogik ist, dass es zwischen ihnen keinen Join gibt — ein
speicherresidenter Ersatz hätte diesen Zwang nicht und bewiese deshalb nichts.

Dazu kommen drei Dinge, die in PostgreSQL selbst stecken und nirgends sonst: der CHECK-Constraint
gegen die verbotene Richtung, ``default_transaction_read_only`` als nur lesender Zugang (§20.1,
Guard 5) und die lexikalische Suche über ``search_tsv`` und Trigramm (§12.4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import CheckStatus, check_store_separation
from wissensgraph.domain.concepts import ConceptDraft
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.migrations import upgrade_all
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.graph import GraphService

pytestmark = pytest.mark.integration


@pytest.fixture
def registry(postgres_settings: Settings, postgres_registry: StoreRegistry) -> StoreRegistry:
    upgrade_all(postgres_settings, postgres_registry)
    return postgres_registry


@pytest.fixture
def factory(registry: StoreRegistry) -> UnitOfWorkFactory:
    return UnitOfWorkFactory(registry)


@pytest.fixture
def concepts(postgres_settings: Settings, factory: UnitOfWorkFactory) -> ConceptService:
    return ConceptService(postgres_settings, factory)


@pytest.fixture
def graph(postgres_settings: Settings, factory: UnitOfWorkFactory) -> GraphService:
    return GraphService(postgres_settings, factory)


def seite(concept_id: str = "confluence:1", **overrides: object) -> ConceptDraft:
    werte: dict[str, object] = {
        "id": concept_id,
        "scope": "engineering",
        "type": "Confluence Page",
        "title": "Zahlungsabgleich mit dem Kernbanksystem",
        "body": "Beschreibung des Abgleichs.",
        "source_name": "confluence",
        "external_id": concept_id.split(":")[1],
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


def bruecke(**overrides: object) -> ConceptDraft:
    werte: dict[str, object] = {
        "id": "project:finance",
        "scope": "personal",
        "type": "Project",
        "title": "Finanzintegration",
        "body": "Grundlage ist [[confluence:1]].",
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


def kante(factory: UnitOfWorkFactory, store: str) -> object:
    with factory(store) as uow:
        return uow.edges.list_outgoing("project:finance")[0]


class TestBrueckenKonzept:
    """§24, Stufe 5: "ein Brücken-Konzept in ``personal`` verlinkt erfolgreich auf ein
    ``shared``-Konzept und ist in beide Richtungen auffindbar"."""

    def test_die_kante_zeigt_ueber_die_datenbankgrenze(
        self, concepts: ConceptService, factory: UnitOfWorkFactory
    ) -> None:
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        gefunden = kante(factory, "personal")

        assert (gefunden.from_store, gefunden.to_store) == ("personal", "shared")  # type: ignore[attr-defined]
        assert gefunden.resolved is True  # type: ignore[attr-defined]

    def test_sie_ist_in_beide_richtungen_auffindbar(self, concepts: ConceptService) -> None:
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        hin = concepts.describe("project:finance", store="personal")
        zurueck = concepts.describe("confluence:1", store="shared")

        assert hin is not None and zurueck is not None
        assert [edge.to_id for edge in hin.outgoing] == ["confluence:1"]
        assert [edge.from_id for edge in zurueck.incoming] == ["project:finance"]

    def test_der_geteilte_store_kennt_die_kante_nicht(
        self, concepts: ConceptService, registry: StoreRegistry
    ) -> None:
        """Er darf sie gar nicht kennen — ``ck_shared_no_personal_ref`` ließe sie nicht zu."""
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        with registry.engine("shared").connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM edges")).scalar_one() == 0

    def test_eine_vor_dem_ziel_geschriebene_notiz_wird_nachtraeglich_verbunden(
        self, concepts: ConceptService, factory: UnitOfWorkFactory
    ) -> None:
        """Der häufigere Fall: Die Notiz ist älter als der erste Sync der Quelle (§8.5)."""
        concepts.upsert(bruecke())
        assert kante(factory, "personal").resolved is False  # type: ignore[attr-defined]

        concepts.upsert(seite())
        anzahl = concepts.refresh_bridges_into("shared")

        gefunden = kante(factory, "personal")
        assert anzahl == 1
        assert (gefunden.to_store, gefunden.resolved) == ("shared", True)  # type: ignore[attr-defined]

    def test_der_abgleich_ist_wiederholbar(self, concepts: ConceptService) -> None:
        """Ein zweiter Aufruf ohne Änderung schreibt nichts."""
        concepts.upsert(bruecke())
        concepts.upsert(seite())
        concepts.refresh_bridges_into("shared")

        assert concepts.refresh_bridges_into("shared") == 0


class TestGrabsteinUeberDieGrenze:
    def test_ein_grabstein_macht_die_bruecke_unaufloesbar(
        self, concepts: ConceptService, factory: UnitOfWorkFactory
    ) -> None:
        """§7.6: Die Kante bleibt, ihre Auflösbarkeit nicht."""
        concepts.upsert(seite())
        concepts.upsert(bruecke())
        concepts.mark_source_deleted("confluence:1", store="shared")

        assert concepts.refresh_bridges_into("shared") == 1
        assert kante(factory, "personal").resolved is False  # type: ignore[attr-defined]

    def test_die_wiederherstellung_loest_sie_wieder_auf(
        self, concepts: ConceptService, factory: UnitOfWorkFactory
    ) -> None:
        concepts.upsert(seite())
        concepts.upsert(bruecke())
        concepts.mark_source_deleted("confluence:1", store="shared")
        concepts.refresh_bridges_into("shared")

        concepts.upsert(seite())
        assert concepts.refresh_bridges_into("shared") == 1
        assert kante(factory, "personal").resolved is True  # type: ignore[attr-defined]


class TestGuard5NurLesenderZugang:
    """§20.1, Guard 5: "muss bei jedem Schreibversuch einen Datenbankfehler erzeugen"."""

    def test_ueber_den_lesenden_zugang_laesst_sich_lesen(
        self, concepts: ConceptService, registry: StoreRegistry
    ) -> None:
        concepts.upsert(seite())

        with registry.readonly_engine("shared").connect() as connection:
            anzahl = connection.execute(text("SELECT count(*) FROM concepts")).scalar_one()

        assert anzahl == 1

    def test_ein_insert_scheitert_in_der_datenbank(self, registry: StoreRegistry) -> None:
        """Nicht in der Anwendung: Eine Prüfung im Code wäre nur so gut wie ihr Aufrufer."""
        with (
            pytest.raises(SQLAlchemyError, match="read-only"),
            registry.readonly_engine("shared").begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO concepts (id, store, scope, type) "
                    "VALUES ('confluence:9', 'shared', 'engineering', 'Confluence Page')"
                )
            )

    def test_auch_ein_update_scheitert(
        self, concepts: ConceptService, registry: StoreRegistry
    ) -> None:
        concepts.upsert(seite())

        with (
            pytest.raises(SQLAlchemyError, match="read-only"),
            registry.readonly_engine("shared").begin() as connection,
        ):
            connection.execute(text("UPDATE concepts SET title = 'gekapert'"))


class TestDiagnose:
    """``wg doctor`` prüft die Store-Trennung (§24, Stufe 5)."""

    def test_beide_stores_melden_eine_gewahrte_grenze(self, registry: StoreRegistry) -> None:
        ergebnisse = check_store_separation(registry)

        assert {item.name for item in ergebnisse} == {
            "store_trennung:shared",
            "store_trennung:personal",
        }
        assert all(item.status is CheckStatus.OK for item in ergebnisse)

    def test_die_bruecke_taucht_in_der_diagnose_auf(
        self, concepts: ConceptService, registry: StoreRegistry
    ) -> None:
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        persoenlich = next(
            item for item in check_store_separation(registry) if item.name.endswith("personal")
        )

        assert persoenlich.status is CheckStatus.OK
        assert persoenlich.context["kanten_ueber_die_grenze"] == 1

    def test_der_constraint_steht_nur_im_geteilten_store(self, registry: StoreRegistry) -> None:
        ergebnisse = {item.name: item for item in check_store_separation(registry)}

        assert ergebnisse["store_trennung:shared"].context["ck_shared_no_personal_ref"] is True
        assert ergebnisse["store_trennung:personal"].context["ck_shared_no_personal_ref"] is False


class TestLexikalischeSuche:
    """§12.4 gegen echtes ``search_tsv`` und ``pg_trgm``."""

    def test_ein_wort_aus_dem_titel_findet_die_seite(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        concepts.upsert(seite())

        ergebnis = graph.search("Kernbanksystem", store="shared")

        assert [hit.concept.id for hit in ergebnis.hits] == ["confluence:1"]

    def test_ein_wort_aus_dem_fliesstext_findet_sie_auch(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        """``search_tsv`` deckt Titel, Beschreibung und Body ab (§7.4)."""
        concepts.upsert(seite())

        assert graph.search("Abgleichs", store="shared").hits

    def test_ein_vertippter_titel_findet_sie_ueber_trigramme(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        """Die zweite Hälfte der Fusion: Was der Volltext nicht findet, findet die Ähnlichkeit."""
        concepts.upsert(seite(title="Zahlungsabgleich"))

        assert graph.search("Zahlungsabgleic", store="shared").hits

    def test_grabsteine_erscheinen_nicht(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        concepts.upsert(seite())
        concepts.mark_source_deleted("confluence:1", store="shared")

        assert graph.search("Kernbanksystem", store="shared").hits == ()

    def test_die_suche_ueberschreitet_die_store_grenze_nicht(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        concepts.upsert(bruecke())

        assert graph.search("Finanzintegration", store="shared").hits == ()
        assert len(graph.search("Finanzintegration", store="personal").hits) == 1


class TestKommandozeile:
    """Dieselbe Abnahme, aber über den Weg, den ein Mensch nimmt (§19).

    Die CLI ist eine dünne Hülle um dieselben Dienste (Leitprinzip 14). Dass sie es wirklich ist,
    zeigt sich nur, wenn sie einmal von außen gegen eine echte Datenbank läuft.
    """

    @pytest.fixture
    def cli_config(
        self, minimal_config_dict: dict, postgres_settings: Settings, write_config: object
    ) -> Path:
        minimal_config_dict["embedding_dim"] = postgres_settings.embedding_dim
        minimal_config_dict["stores"] = {
            name: {"dsn": store.dsn} for name, store in postgres_settings.stores.items()
        }
        return write_config(minimal_config_dict)  # type: ignore[operator, no-any-return]

    def rufe(self, cli_config: Path, *args: str) -> object:
        from typer.testing import CliRunner

        from wissensgraph.cli import app

        return CliRunner().invoke(
            app, [*args, "--config", str(cli_config), "--dotenv", str(cli_config.parent / "x.env")]
        )

    def test_eine_bruecke_entsteht_und_ist_beidseitig_sichtbar(
        self, registry: StoreRegistry, concepts: ConceptService, cli_config: Path
    ) -> None:
        concepts.upsert(seite())

        anlegen = self.rufe(
            cli_config,
            "concepts",
            "add",
            "project:finance",
            "--title",
            "Finanzintegration",
            "--link",
            "confluence:1",
        )
        ansicht = self.rufe(
            cli_config, "concepts", "show", "confluence:1", "--store", "shared", "--json"
        )

        assert anlegen.exit_code == 0, anlegen.output  # type: ignore[attr-defined]
        assert ansicht.exit_code == 0, ansicht.output  # type: ignore[attr-defined]
        daten = json.loads(ansicht.stdout)  # type: ignore[attr-defined]
        assert daten["incoming"] == [
            {"kind": "references", "from": "personal:project:finance", "resolved": True}
        ]

    def test_die_traversierung_nennt_ihre_abfragezahl(
        self, registry: StoreRegistry, concepts: ConceptService, cli_config: Path
    ) -> None:
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        ergebnis = self.rufe(
            cli_config,
            "graph",
            "traverse",
            "--start",
            "confluence:1",
            "--store",
            "shared",
            "--hops",
            "1",
            "--json",
        )

        daten = json.loads(ergebnis.stdout)  # type: ignore[attr-defined]
        assert daten["queries"] > 0
        assert {node["id"] for node in daten["nodes"]} == {"confluence:1", "project:finance"}

    def test_doctor_prueft_die_store_trennung(
        self, registry: StoreRegistry, cli_config: Path
    ) -> None:
        """§24, Stufe 5: "``wg doctor``-Prüfung der Store-Trennung"."""
        ergebnis = self.rufe(cli_config, "doctor")

        assert "store_trennung:shared" in ergebnis.stdout  # type: ignore[attr-defined]
        assert "store_trennung:personal" in ergebnis.stdout  # type: ignore[attr-defined]
        assert "[fail] store_trennung" not in ergebnis.stdout  # type: ignore[attr-defined]


class TestTraversierung:
    def test_ein_nur_ueber_die_bruecke_erreichbares_konzept_erscheint_nah(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        """§24, Stufe 6 — über zwei Datenbanken hinweg, ohne einen einzigen Join."""
        concepts.upsert(seite())
        concepts.upsert(bruecke())

        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1)

        projekt = next(node for node in ergebnis.nodes if node.concept.id == "project:finance")
        assert (projekt.hops, projekt.store) == (1, "personal")

    def test_drei_hops_in_einem_store_bleiben_unter_sechs_abfragen(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        """Die Abnahme aus §24, Stufe 6."""
        for nummer in (1, 2, 3, 4):
            concepts.upsert(
                seite(f"confluence:{nummer}", body=f"Weiter zu [[confluence:{nummer + 1}]].")
            )

        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=3)

        assert ergebnis.queries <= 6
        assert {node.concept.id for node in ergebnis.nodes} >= {
            "confluence:1",
            "confluence:2",
            "confluence:3",
            "confluence:4",
        }

    def test_die_dichte_unterscheidet_sich_bei_unterschiedlicher_lokaler_struktur(
        self, concepts: ConceptService, graph: GraphService
    ) -> None:
        """§24, Stufe 6: "identische Zielkonzepte erhalten bei unterschiedlicher lokaler
        Struktur unterschiedliche Dichtewerte"."""
        concepts.upsert(seite("confluence:1"))
        concepts.upsert(seite("confluence:2", title="Zweite Seite"))
        concepts.upsert(bruecke())
        concepts.upsert(
            ConceptDraft(
                id="note:a",
                scope="personal",
                type="Note",
                title="Notiz",
                body="Siehe [[confluence:1]].",
            )
        )

        eins = graph.traverse(["confluence:1"], store="shared", hops=1)
        zwei = graph.traverse(["confluence:2"], store="shared", hops=1)

        assert next(n for n in eins.nodes if n.concept.id == "confluence:1").density == 2
        assert next(n for n in zwei.nodes if n.concept.id == "confluence:2").density == 0
