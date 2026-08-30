"""Tests der Kernoperation gegen die Ports (§10.2, §8.5, §20.1).

Der Dienst läuft hier gegen eine speicherresidente Umsetzung der Persistenz-Ports. Zwei Dinge
werden damit zugleich geprüft: das Verhalten der Operation und die Behauptung, dass der Dienst
keine Infrastruktur kennt — liefe er nicht ohne Datenbank, wären diese Tests nicht schreibbar.

Die vier Abnahmekriterien der Stufe 2 (§24) tragen den Marker im Namen ihrer Klasse; dieselben
Fälle laufen in ``tests/integration/test_konzepte_postgres.py`` noch einmal gegen echtes
PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from support.memory import MemoryChangeLogRepository, MemoryUnitOfWorkFactory
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeType
from wissensgraph.domain.concepts import ConceptDraft, ConceptStatus
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.upsert import UpsertOutcome
from wissensgraph.services.concepts import ConceptService, ConceptValidationError

pytestmark = pytest.mark.unit

JETZT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def uow_factory() -> MemoryUnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(("shared", "personal"))


@pytest.fixture
def service(settings: Settings, uow_factory: MemoryUnitOfWorkFactory) -> ConceptService:
    return ConceptService(settings, uow_factory, clock=lambda: JETZT)


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


def notiz(**overrides: object) -> ConceptDraft:
    """Eine persönliche Notiz im Store ``personal``."""
    werte: dict[str, object] = {
        "id": "note:a",
        "scope": "personal",
        "type": "Note",
        "title": "Notiz",
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


class TestStoreAuflösung:
    def test_der_scope_bestimmt_den_store(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite())
        service.upsert(notiz())

        assert set(uow_factory.state("shared").concepts) == {"confluence:1"}
        assert set(uow_factory.state("personal").concepts) == {"note:a"}

    def test_unbekannter_scope_bricht_verstaendlich_ab(self, service: ConceptService) -> None:
        with pytest.raises(ConceptValidationError, match="unbekannten Scope 'marketing'"):
            service.upsert(notiz(scope="marketing"))

    def test_unbekannter_typ_verweist_auf_die_taxonomie(self, service: ConceptService) -> None:
        with pytest.raises(ConceptValidationError, match=r"config/wissensgraph\.yaml"):
            service.upsert(notiz(type="Gedanke"))

    def test_typ_im_falschen_store_wird_abgewiesen(self, service: ConceptService) -> None:
        # 'Note' ist laut Taxonomie nur in 'personal' zugelassen (§7.2).
        with pytest.raises(ConceptValidationError, match="nicht zugelassen"):
            service.upsert(notiz(id="note:b", scope="engineering"))


class TestAbnahmeZweifachesUpsert:
    """Abnahme 1 und 2: ein Eintrag bei Gleichheit, ein zweiter bei geändertem Hash."""

    def test_unveraendertes_upsert_erzeugt_genau_einen_eintrag(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        erstes = service.upsert(seite())
        zweites = service.upsert(seite())

        assert erstes.outcome is UpsertOutcome.CREATED
        assert zweites.outcome is UpsertOutcome.UNCHANGED
        assert len(uow_factory.state("shared").changes) == 1

    def test_geaenderter_hash_erzeugt_den_zweiten_eintrag(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite())
        ergebnis = service.upsert(seite(body="Inhalt, überarbeitet"))

        arten = [entry.change_type for entry in uow_factory.state("shared").changes]

        assert ergebnis.outcome is UpsertOutcome.UPDATED
        assert arten == [ChangeType.CREATED, ChangeType.UPDATED]

    def test_der_journaleintrag_enthaelt_keinen_inhalt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        # §21.1 gilt sinngemäß auch für das Journal: IDs und Hashes ja, Bodies nie.
        service.upsert(seite())

        detail = uow_factory.state("shared").changes[0].detail or {}

        assert set(detail) == {"content_hash"}


class TestAbnahmeUnbekanntesKantenziel:
    """Abnahme 3: Eine Kante auf ein unbekanntes Ziel entsteht mit ``resolved = false``."""

    def test_referenz_auf_unbekanntes_ziel_ist_kein_fehler(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        ergebnis = service.upsert(seite(body="Siehe [[confluence:999]]"))

        (kante,) = uow_factory.state("shared").edges

        assert ergebnis.outcome is UpsertOutcome.CREATED
        assert kante.to_id == "confluence:999"
        assert kante.resolved is False
        assert kante.kind == "references"

    def test_vorhandenes_ziel_wird_sofort_aufgeloest(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite(id="confluence:2", external_id="2"))
        service.upsert(seite(body="Siehe [[confluence:2]]"))

        kante = next(
            edge for edge in uow_factory.state("shared").edges if edge.from_id == "confluence:1"
        )

        assert kante.resolved is True

    def test_nachtraeglich_angelegtes_ziel_wird_beim_naechsten_lauf_aufgeloest(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§8.5: Die Kante "wird bei jedem Lauf erneut geprüft"."""
        service.upsert(seite(body="Siehe [[confluence:2]]"))
        service.upsert(seite(id="confluence:2", external_id="2"))

        anzahl = service.refresh_edge_resolution("shared")

        kante = next(
            edge for edge in uow_factory.state("shared").edges if edge.from_id == "confluence:1"
        )
        assert anzahl == 1
        assert kante.resolved is True

    def test_entfernte_referenz_entfernt_die_kante(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite(body="Siehe [[confluence:9]]"))
        ergebnis = service.upsert(seite(body="Ohne Verweis."))

        assert uow_factory.state("shared").edges == []
        assert len(ergebnis.edges_removed) == 1

    def test_kuratierte_kante_bleibt_unangetastet(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§10.4: "Kanten mit curated = true bleiben unangetastet"."""
        service.upsert(seite(body="Siehe [[confluence:9]]"))
        zustand = uow_factory.state("shared")
        zustand.edges = [
            zustand.edges[0].model_copy(update={"curated": True, "generated_by": None})
        ]

        service.upsert(seite(body="Ohne Verweis."))

        assert len(zustand.edges) == 1
        assert zustand.edges[0].curated is True

    def test_jede_kante_hinterlaesst_einen_journaleintrag(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite(body="[[confluence:9]] und [[jira:PROJ-1]]"))

        arten = [entry.change_type for entry in uow_factory.state("shared").changes]

        assert arten.count(ChangeType.EDGE_ADDED) == 2


class TestAbnahmeKurationskonflikt:
    """Abnahme 4: Ein kuratiertes Feld überlebt ein Quell-Update mit Konfliktvermerk."""

    def _kuratiertes_cluster(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> ConceptDraft:
        """Ein nicht gespiegelter Typ im shared-Store, von Hand geschrieben."""
        draft = ConceptDraft(
            id="cluster:a",
            scope="engineering",
            type="Cluster",
            title="Von Hand benannt",
            curated=True,
        )
        service.upsert(draft, actor="user:mn")
        return draft

    def test_kuratiertes_feld_ueberlebt_und_wird_vermerkt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        self._kuratiertes_cluster(service, uow_factory)

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

        zustand = uow_factory.state("shared")
        konflikte = [
            entry for entry in zustand.changes if entry.change_type is ChangeType.CURATION_CONFLICT
        ]

        assert ergebnis.outcome is UpsertOutcome.CONFLICT
        assert ergebnis.held_back == ("title",)
        assert zustand.concepts["cluster:a"].title == "Von Hand benannt"
        assert len(konflikte) == 1
        assert konflikte[0].detail is not None
        assert konflikte[0].detail["fields"] == ["title"]

    def test_derselbe_konflikt_wird_nur_einmal_vermerkt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Ein Konflikt ist ein Zustand, kein Ereignis — sonst wächst die Liste je Lauf."""
        self._kuratiertes_cluster(service, uow_factory)
        quellstand = ConceptDraft(
            id="cluster:a",
            scope="engineering",
            type="Cluster",
            title="Aus der Quelle",
            source_name="confluence",
            external_id="c-a",
        )

        service.upsert(quellstand)
        service.upsert(quellstand)
        service.upsert(quellstand)

        konflikte = [
            entry
            for entry in uow_factory.state("shared").changes
            if entry.change_type is ChangeType.CURATION_CONFLICT
        ]

        assert len(konflikte) == 1

    def test_ein_neuer_quellstand_ist_ein_neuer_konflikt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        self._kuratiertes_cluster(service, uow_factory)
        for titel in ("Fassung A", "Fassung B"):
            service.upsert(
                ConceptDraft(
                    id="cluster:a",
                    scope="engineering",
                    type="Cluster",
                    title=titel,
                    source_name="confluence",
                    external_id="c-a",
                )
            )

        konflikte = [
            entry
            for entry in uow_factory.state("shared").changes
            if entry.change_type is ChangeType.CURATION_CONFLICT
        ]

        assert len(konflikte) == 2
        assert konflikte[0].detail is not None
        assert CONFLICT_SOURCE_HASH_KEY in konflikte[0].detail


class TestVerifikation:
    def test_inhaltsaenderung_setzt_die_bestaetigung_zurueck(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite())
        zustand = uow_factory.state("shared")
        zustand.concepts["confluence:1"] = zustand.concepts["confluence:1"].model_copy(
            update={"verified_by": "user:mn", "verified_at": JETZT}
        )

        ergebnis = service.upsert(seite(body="Inhalt, überarbeitet"))

        arten = [entry.change_type for entry in zustand.changes]
        assert ergebnis.verification_reset is True
        assert zustand.concepts["confluence:1"].verified_by is None
        assert ChangeType.VERIFICATION_RESET in arten


class TestRegel5Transaktionalitaet:
    def test_ein_fehler_mitten_im_vorgang_laesst_nichts_zurueck(
        self,
        settings: Settings,
        uow_factory: MemoryUnitOfWorkFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """§10.2 Regel 5: Konzept, Kanten und change_log gemeinsam — oder gar nicht.

        Der Fehler wird bewusst *nach* dem Speichern des Konzepts ausgelöst: Ein Abbruch vor dem
        ersten Schreibvorgang würde nichts über Transaktionalität aussagen.
        """
        service = ConceptService(settings, uow_factory, clock=lambda: JETZT)
        service.upsert(seite())
        vorher_eintraege = len(uow_factory.state("shared").changes)

        original = MemoryChangeLogRepository.append
        aufrufe = {"n": 0}

        def stolpert(self: MemoryChangeLogRepository, entry: object) -> None:
            aufrufe["n"] += 1
            if aufrufe["n"] == 2:  # nach dem 'updated'-Eintrag, beim Schreiben der Kante
                raise RuntimeError("Verbindung verloren")
            original(self, entry)  # type: ignore[arg-type]

        monkeypatch.setattr(MemoryChangeLogRepository, "append", stolpert)

        with pytest.raises(RuntimeError):
            service.upsert(seite(body="Neuer Inhalt mit [[confluence:9]]"))

        zustand = uow_factory.state("shared")
        assert len(zustand.changes) == vorher_eintraege
        assert zustand.concepts["confluence:1"].body == "Inhalt"
        assert zustand.edges == []


class TestErgebnisbericht:
    def test_as_dict_enthaelt_keine_inhalte(self, service: ConceptService) -> None:
        ergebnis = service.upsert(seite(body="Geheimer Inhalt [[confluence:9]]"))

        bericht = ergebnis.as_dict()

        assert "Geheimer Inhalt" not in str(bericht)
        assert bericht["outcome"] == "created"
        assert bericht["edges_added"] == 1

    def test_written_meldet_den_schreibvorgang(self, service: ConceptService) -> None:
        assert service.upsert(seite()).written is True
        assert service.upsert(seite()).written is False

    def test_run_id_und_akteur_landen_im_journal(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        run_id = uuid4()

        service.upsert(notiz(), actor="agent:claude", run_id=run_id)

        eintrag = uow_factory.state("personal").changes[0]
        assert eintrag.actor == "agent:claude"
        assert eintrag.run_id == run_id


class TestStatus:
    def test_tombstone_loescht_nichts(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§7.6: Löschung in der Quelle führt nie zu einem DELETE."""
        service.upsert(seite(body="[[confluence:9]]"))

        service.upsert(seite(body="", status=ConceptStatus.TOMBSTONE))

        zustand = uow_factory.state("shared")
        assert zustand.concepts["confluence:1"].status is ConceptStatus.TOMBSTONE
        assert zustand.concepts["confluence:1"].title == "Titel"


class TestPortsWerdenErfuellt:
    def test_die_speicherumsetzung_erfuellt_die_protokolle(
        self, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        from wissensgraph.ports.repositories import (
            ChangeLogRepository,
            ConceptRepository,
            EdgeRepository,
        )

        with uow_factory("shared") as uow:
            assert isinstance(uow.concepts, ConceptRepository)
            assert isinstance(uow.edges, EdgeRepository)
            assert isinstance(uow.changes, ChangeLogRepository)

    def test_unbekannter_store_wird_abgewiesen(self, uow_factory: MemoryUnitOfWorkFactory) -> None:
        with pytest.raises(KeyError):
            uow_factory("gibtsnicht")

    def test_kantenentwurf_traegt_den_eigenen_store(self) -> None:
        entwurf = EdgeDraft(
            from_store="personal",
            from_id="note:a",
            to_store="shared",
            to_id="confluence:1",
            kind="references",
        )

        assert entwurf.from_store == "personal"


class TestQuelleMeldetLoeschung:
    """``mark_source_deleted`` — die Löschbehandlung der Stufe 4 (§7.6, §10.4)."""

    def test_der_status_wird_zum_grabstein(self, service: ConceptService) -> None:
        service.upsert(seite())

        assert service.mark_source_deleted("confluence:1", store="shared") is True

    def test_inhalt_und_kanten_bleiben_stehen(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Der Grund aus §7.6: Persönliche Notizen darauf sollen nachvollziehbar bleiben."""
        service.upsert(seite(body="Inhalt mit [[confluence:2]]"))
        kanten_vorher = list(uow_factory.state("shared").edges)

        service.mark_source_deleted("confluence:1", store="shared")

        gespeichert = uow_factory.state("shared").concepts["confluence:1"]
        assert gespeichert.status is ConceptStatus.TOMBSTONE
        assert gespeichert.body == "Inhalt mit [[confluence:2]]"
        assert uow_factory.state("shared").edges == kanten_vorher

    def test_die_loeschung_steht_im_journal(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        run_id = uuid4()
        service.upsert(seite())

        service.mark_source_deleted("confluence:1", store="shared", run_id=run_id)

        eintrag = next(
            entry
            for entry in uow_factory.state("shared").changes
            if entry.change_type is ChangeType.SOURCE_DELETED
        )
        assert eintrag.concept_id == "confluence:1"
        assert eintrag.run_id == run_id
        assert eintrag.detail == {"vorheriger_status": "stable"}

    def test_ein_unbekanntes_konzept_ist_kein_fehler(self, service: ConceptService) -> None:
        """Eine Quelle darf ein Objekt melden, das nie synchronisiert wurde."""
        assert service.mark_source_deleted("confluence:99", store="shared") is False

    def test_eine_zweite_meldung_schreibt_nicht_erneut(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Löschung ist ein Zustand, kein Ereignis — sonst wüchse das Journal bei jedem Lauf."""
        service.upsert(seite())
        service.mark_source_deleted("confluence:1", store="shared")
        vorher = len(uow_factory.state("shared").changes)

        assert service.mark_source_deleted("confluence:1", store="shared") is False
        assert len(uow_factory.state("shared").changes) == vorher

    def test_kuration_verhindert_den_grabstein_nicht(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§10.4: "Kuration gewinnt, außer die Quelle meldet Löschung"."""
        service.upsert(seite(curated=True, status=ConceptStatus.DEPRECATED))

        assert service.mark_source_deleted("confluence:1", store="shared") is True
        assert (
            uow_factory.state("shared").concepts["confluence:1"].status is ConceptStatus.TOMBSTONE
        )
