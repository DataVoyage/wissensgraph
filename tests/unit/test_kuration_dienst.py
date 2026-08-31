"""Der Kurationsdienst ohne HTTP (§16.2, §17.3, §17.4).

Die API-Tests prüfen den Weg, hier steht die Regel. Schwerpunkt ist das Undo: Was sich
zurücknehmen lässt, muss vollständig zurückgenommen werden — eine halb wiederhergestellte Kante
wäre schlimmer als gar keine —, und was sich nicht zurücknehmen lässt, muss das offen sagen.
"""

from __future__ import annotations

from typing import Any

import pytest

from support.memory import MemoryUnitOfWorkFactory
from support.semantik import konzept
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.edges import Edge, EdgeDraft
from wissensgraph.services.curation import CurationError, CurationService, NotFoundError

pytestmark = pytest.mark.unit

AKTEUR = "user:test"


@pytest.fixture
def uow(settings: Settings) -> MemoryUnitOfWorkFactory:
    fabrik = MemoryUnitOfWorkFactory(tuple(settings.stores))
    with fabrik("shared") as arbeit:
        for concept in (
            konzept("confluence:1", title="Eins"),
            konzept("confluence:2", title="Zwei"),
            konzept("cluster:a", title="Cluster A", concept_type=defaults.CONCEPT_TYPE_CLUSTER),
            konzept("cluster:b", title="Cluster B", concept_type=defaults.CONCEPT_TYPE_CLUSTER),
        ):
            arbeit.concepts.save(concept)
    with fabrik("personal") as arbeit:
        arbeit.concepts.save(
            konzept(
                "note:1",
                title="Notiz",
                scope="personal",
                store="personal",
                concept_type="Note",
            )
        )
    return fabrik


@pytest.fixture
def dienst(settings: Settings, uow: MemoryUnitOfWorkFactory) -> CurationService:
    return CurationService(settings, uow)


def _generiert(uow: MemoryUnitOfWorkFactory, **over: Any) -> Edge:
    """Eine Kante, wie ein Lauf sie erzeugt."""
    with uow("shared") as arbeit:
        kante = arbeit.edges.add(
            EdgeDraft(
                from_store="shared",
                from_id="confluence:1",
                to_store="shared",
                to_id="confluence:2",
                kind=defaults.EDGE_KIND_REFERENCES,
                confidence=0.7,
                resolved=True,
                generated_by="gemini:m/relation_extraction@v1",
                **over,
            )
        )
    assert kante is not None
    return kante


class TestAnlegen:
    def test_ein_projekt_bekommt_sein_eigenes_praefix(self, dienst: CurationService) -> None:
        """§7.5: Die ID nennt die Art des Konzepts, nicht nur seine Herkunft."""
        ergebnis = dienst.create_concept(
            scope="personal", concept_type="Project", title="Onboarding", actor=AKTEUR
        )

        assert ergebnis.concept is not None
        assert ergebnis.concept.id.startswith(f"{defaults.ID_PREFIX_PROJECT}:")

    def test_ein_unbekannter_typ_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="Taxonomie"):
            dienst.create_concept(
                scope="personal", concept_type="Erfunden", title="X", actor=AKTEUR
            )

    def test_ein_typ_im_falschen_store_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="nicht zugelassen"):
            dienst.create_concept(
                scope="personal", concept_type="Confluence Page", title="X", actor=AKTEUR
            )

    def test_ein_unbekannter_scope_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="Unbekannter Scope"):
            dienst.create_concept(scope="gibtsnicht", concept_type="Note", title="X", actor=AKTEUR)


class TestAendern:
    def test_eine_aenderung_ohne_felder_ist_keine(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="keine Änderung"):
            dienst.patch_concept("confluence:1", store="shared", changes={}, actor=AKTEUR)

    def test_ein_unbekanntes_feld_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="lassen sich nicht kuratieren"):
            dienst.patch_concept(
                "confluence:1", store="shared", changes={"content_hash": "x"}, actor=AKTEUR
            )

    def test_ein_unbekanntes_konzept_ist_nicht_gefunden(self, dienst: CurationService) -> None:
        with pytest.raises(NotFoundError):
            dienst.patch_concept(
                "confluence:99", store="shared", changes={"status": "x"}, actor=AKTEUR
            )

    def test_eine_inhaltsaenderung_zieht_den_hash_nach(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """§10.3: Der Hash beantwortet "hat sich der Inhalt geändert?" — hier lautet sie ja."""
        vorher = uow.state("personal").concepts["note:1"].content_hash

        dienst.patch_concept("note:1", store="personal", changes={"title": "Neu"}, actor=AKTEUR)

        assert uow.state("personal").concepts["note:1"].content_hash != vorher


class TestKanten:
    def test_eine_kante_auf_ein_unbekanntes_ziel_bleibt_unaufgeloest(
        self, dienst: CurationService
    ) -> None:
        """§8.5: ``resolved = false`` ist kein Fehler, sondern eine offene Frage."""
        ergebnis = dienst.add_edge(
            store="shared", from_id="confluence:1", to_id="confluence:99", actor=AKTEUR
        )

        assert ergebnis.edge is not None
        assert ergebnis.edge.resolved is False

    def test_eine_bruecke_wird_gegen_den_zielstore_aufgeloest(
        self, dienst: CurationService
    ) -> None:
        ergebnis = dienst.add_edge(
            store="personal",
            from_id="note:1",
            to_id="cluster:a",
            to_store="shared",
            actor=AKTEUR,
        )

        assert ergebnis.edge is not None
        assert ergebnis.edge.resolved is True

    def test_ein_unbekannter_ausgangspunkt_ist_nicht_gefunden(
        self, dienst: CurationService
    ) -> None:
        with pytest.raises(NotFoundError):
            dienst.add_edge(
                store="shared", from_id="confluence:99", to_id="confluence:1", actor=AKTEUR
            )

    def test_das_anlegen_nimmt_einen_negativvermerk_zurueck(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Wer die Kante von Hand setzt, hat seine Meinung geändert."""
        kante = _generiert(uow)
        dienst.reject_edge(kante.id, store="shared", actor=AKTEUR)

        dienst.add_edge(
            store="shared",
            from_id="confluence:1",
            to_id="confluence:2",
            kind=defaults.EDGE_KIND_REFERENCES,
            actor=AKTEUR,
        )

        assert uow.state("shared").rejections == {}

    def test_eine_unbekannte_kante_laesst_sich_nicht_bestaetigen(
        self, dienst: CurationService
    ) -> None:
        from uuid import uuid4

        with pytest.raises(NotFoundError):
            dienst.verify_edge(uuid4(), store="shared", actor=AKTEUR)


class TestCluster:
    def test_ein_cluster_im_falschen_store_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="liegt nicht im Store"):
            dienst.create_cluster(store="personal", scope="engineering", title="X", actor=AKTEUR)

    def test_ein_cluster_mit_sich_selbst_zu_verschmelzen_ist_sinnlos(
        self, dienst: CurationService
    ) -> None:
        with pytest.raises(CurationError, match="mit sich selbst"):
            dienst.merge(store="shared", source_id="cluster:a", target_id="cluster:a", actor=AKTEUR)

    def test_verschmelzen_mit_einem_unbekannten_cluster_ist_nicht_gefunden(
        self, dienst: CurationService
    ) -> None:
        with pytest.raises(NotFoundError):
            dienst.merge(store="shared", source_id="cluster:a", target_id="cluster:x", actor=AKTEUR)

    def test_eine_ausgliederung_ohne_mitglieder_wird_abgelehnt(
        self, dienst: CurationService
    ) -> None:
        with pytest.raises(CurationError, match="leeres Cluster"):
            dienst.split("cluster:a", store="shared", concept_ids=[], title="X", actor=AKTEUR)

    def test_ein_patch_ohne_werte_wird_abgelehnt(self, dienst: CurationService) -> None:
        with pytest.raises(CurationError, match="Weder Titel noch Beschreibung"):
            dienst.patch_cluster("cluster:a", store="shared", actor=AKTEUR)

    def test_ein_doppeltes_mitglied_wird_abgelehnt(self, dienst: CurationService) -> None:
        dienst.add_members("cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR)

        with pytest.raises(CurationError, match="bereits Mitglied"):
            dienst.add_members(
                "cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR
            )

    def test_ein_unbekanntes_mitglied_ist_nicht_gefunden(self, dienst: CurationService) -> None:
        with pytest.raises(NotFoundError):
            dienst.add_members(
                "cluster:a", store="shared", concept_ids=["confluence:99"], actor=AKTEUR
            )

    def test_ein_nicht_vorhandenes_mitglied_zu_entfernen_ist_nicht_gefunden(
        self, dienst: CurationService
    ) -> None:
        with pytest.raises(NotFoundError):
            dienst.remove_member("cluster:a", "confluence:1", store="shared", actor=AKTEUR)

    def test_ein_ausschluss_wird_beim_wiedereinfuegen_aufgehoben(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """§13.4: Der Vermerk hält eine Entscheidung fest — und die kann sich ändern."""
        dienst.add_members("cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR)
        dienst.remove_member("cluster:a", "confluence:1", store="shared", actor=AKTEUR)
        assert uow.state("shared").candidates

        dienst.add_members("cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR)

        assert uow.state("shared").candidates == {}


class TestUndo:
    def test_nimmt_eine_mitgliedschaft_zurueck(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        ergebnis = dienst.add_members(
            "cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR
        )[0]
        assert ergebnis.entry.id is not None

        dienst.undo(ergebnis.entry.id, store="shared", actor=AKTEUR)

        assert uow.state("shared").edges == []

    def test_stellt_ein_entferntes_mitglied_wieder_her(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        dienst.add_members("cluster:a", store="shared", concept_ids=["confluence:1"], actor=AKTEUR)
        entfernt = dienst.remove_member("cluster:a", "confluence:1", store="shared", actor=AKTEUR)
        assert entfernt.entry.id is not None

        dienst.undo(entfernt.entry.id, store="shared", actor=AKTEUR)

        assert len(uow.state("shared").edges) == 1
        # Der Ausschluss ist mit zurückgenommen — sonst hielte der nächste Lauf ihn heraus.
        assert uow.state("shared").candidates == {}

    def test_nimmt_eine_bestaetigung_zurueck_und_laesst_die_kuration_stehen(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Dass ein Mensch die Kante angefasst hat, bleibt wahr (§10.4)."""
        kante = _generiert(uow)
        bestaetigt = dienst.verify_edge(kante.id, store="shared", actor=AKTEUR)
        assert bestaetigt.entry.id is not None

        dienst.undo(bestaetigt.entry.id, store="shared", actor=AKTEUR)

        wieder = uow.state("shared").edges[0]
        assert wieder.verified_by is None
        assert wieder.curated is True

    def test_nimmt_eine_anlage_zurueck(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        angelegt = dienst.create_concept(
            scope="personal", concept_type="Note", title="Weg damit", actor=AKTEUR
        )
        assert angelegt.entry.id is not None and angelegt.concept is not None

        dienst.undo(angelegt.entry.id, store="personal", actor=AKTEUR)

        assert angelegt.concept.id not in uow.state("personal").concepts

    def test_nimmt_einen_statuswechsel_zurueck(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        geaendert = dienst.patch_concept(
            "confluence:1", store="shared", changes={"status": "deprecated"}, actor=AKTEUR
        )
        assert geaendert.entry.id is not None

        dienst.undo(geaendert.entry.id, store="shared", actor=AKTEUR)

        assert str(uow.state("shared").concepts["confluence:1"].status) == "stable"

    def test_eine_inhaltsaenderung_laesst_sich_nicht_zurueckholen(
        self, dienst: CurationService
    ) -> None:
        """§7.4: Das Journal hält Feldnamen fest, keine Werte."""
        geaendert = dienst.patch_concept(
            "note:1", store="personal", changes={"title": "Neu"}, actor=AKTEUR
        )
        assert geaendert.entry.id is not None

        with pytest.raises(CurationError, match="Feldnamen"):
            dienst.undo(geaendert.entry.id, store="personal", actor=AKTEUR)

    def test_ein_unbekannter_eintrag_ist_nicht_gefunden(self, dienst: CurationService) -> None:
        with pytest.raises(NotFoundError):
            dienst.undo(999, store="shared", actor=AKTEUR)

    def test_eine_zweimal_zurueckgenommene_kante_meldet_das(self, dienst: CurationService) -> None:
        ergebnis = dienst.add_edge(
            store="shared", from_id="confluence:1", to_id="confluence:2", actor=AKTEUR
        )
        assert ergebnis.entry.id is not None
        dienst.undo(ergebnis.entry.id, store="shared", actor=AKTEUR)

        with pytest.raises(CurationError, match="nicht mehr"):
            dienst.undo(ergebnis.entry.id, store="shared", actor=AKTEUR)

    def test_ein_eintrag_ohne_kante_laesst_sich_nicht_zurueckholen(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        with uow("shared") as arbeit:
            eintrag = arbeit.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED, concept_id="confluence:1", actor=AKTEUR
                )
            )
        assert eintrag.id is not None

        with pytest.raises(CurationError, match="keine Kante"):
            dienst.undo(eintrag.id, store="shared", actor=AKTEUR)

    def test_ein_entfernen_ohne_festgehaltene_kante_meldet_das(
        self, dienst: CurationService, uow: MemoryUnitOfWorkFactory
    ) -> None:
        with uow("shared") as arbeit:
            eintrag = arbeit.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_REMOVED,
                    concept_id="confluence:1",
                    actor=AKTEUR,
                    detail={},
                )
            )
        assert eintrag.id is not None

        with pytest.raises(CurationError, match="hält die Kante nicht fest"):
            dienst.undo(eintrag.id, store="shared", actor=AKTEUR)

    def test_ein_verschmelzen_laesst_sich_nicht_zurueckholen(self, dienst: CurationService) -> None:
        """Die umgehängten Kanten sind nicht mehr unterscheidbar von denen, die dort standen."""
        verschmolzen = dienst.merge(
            store="shared", source_id="cluster:a", target_id="cluster:b", actor=AKTEUR
        )
        assert verschmolzen.entry.id is not None

        with pytest.raises(CurationError, match="lässt sich nicht zurücknehmen"):
            dienst.undo(verschmolzen.entry.id, store="shared", actor=AKTEUR)
