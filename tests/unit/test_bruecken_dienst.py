"""Brücken-Konzepte und store-übergreifende Kantenauflösung (§7.3, §8.5, §12.1).

Der Fall, um den es geht, ist einer: Eine persönliche Notiz oder ein Projekt verweist auf eine
Confluence-Seite, die in einer *anderen Datenbank* liegt. Es gibt dafür keinen Fremdschlüssel und
keinen Join (§7.3); ob die Kante auflösbar ist, entscheidet die Anwendungsschicht — und sie muss
es bei jedem Lauf neu entscheiden, weil sich an beiden Enden unabhängig etwas ändern kann.

Geprüft wird gegen die speicherresidenten Ports. Dass das geht, ist selbst eine Aussage: Die
Brückenlogik enthält keine Datenbankannahme, obwohl sie von zwei Datenbanken handelt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from support.memory import MemoryUnitOfWorkFactory
from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import ConceptDraft, ConceptStatus
from wissensgraph.services.concepts import ConceptService

pytestmark = pytest.mark.unit

JETZT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def uow_factory() -> MemoryUnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(("shared", "personal"))


@pytest.fixture
def service(settings: Settings, uow_factory: MemoryUnitOfWorkFactory) -> ConceptService:
    return ConceptService(settings, uow_factory, clock=lambda: JETZT)


def seite(**overrides: object) -> ConceptDraft:
    werte: dict[str, object] = {
        "id": "confluence:1",
        "scope": "engineering",
        "type": "Confluence Page",
        "title": "Zahlungsabgleich",
        "source_name": "confluence",
        "external_id": "1",
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


def bruecke(**overrides: object) -> ConceptDraft:
    """Ein Brücken-Konzept: Typ ``Project``, Scope ``personal`` (§24, Stufe 5)."""
    werte: dict[str, object] = {
        "id": "project:finance",
        "scope": "personal",
        "type": "Project",
        "title": "Finanzintegration",
        "body": "Grundlage ist [[confluence:1]].",
    }
    werte.update(overrides)
    return ConceptDraft.model_validate(werte)


def kanten(factory: MemoryUnitOfWorkFactory, store: str) -> list:  # type: ignore[type-arg]
    return list(factory.state(store).edges)


class TestBrueckeAnlegen:
    def test_die_kante_findet_den_fremden_store(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Der Kern der Stufe: Der Zielstore wird gesucht, nicht angenommen (§12.1)."""
        service.upsert(seite())
        service.upsert(bruecke())

        (kante,) = kanten(uow_factory, "personal")
        assert kante.from_store == "personal"
        assert kante.to_store == "shared"
        assert kante.resolved is True

    def test_die_kante_liegt_im_persoenlichen_store(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Der geteilte Store bleibt unberührt — er weiß nichts von der Notiz (§12.1)."""
        service.upsert(seite())
        service.upsert(bruecke())

        assert kanten(uow_factory, "shared") == []

    def test_ein_unbekanntes_ziel_bleibt_im_eigenen_store(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Wo ein noch nicht synchronisiertes Objekt liegen wird, weiß hier niemand (§8.5)."""
        service.upsert(bruecke())

        (kante,) = kanten(uow_factory, "personal")
        assert kante.to_store == "personal"
        assert kante.resolved is False

    def test_der_geteilte_store_sucht_nicht_im_persoenlichen(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Auch dann nicht, wenn die ID dort läge — sonst entstünde die verbotene Richtung."""
        service.upsert(ConceptDraft(id="note:a", scope="personal", type="Note", title="Notiz"))
        service.upsert(seite(body="Siehe [[note:a]]"))

        (kante,) = kanten(uow_factory, "shared")
        assert kante.to_store == "shared"
        assert kante.resolved is False

    def test_der_eigene_store_hat_vorrang(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Dieselbe ID in beiden Stores: gemeint ist die eigene (:func:`resolution_order`)."""
        service.upsert(
            ConceptDraft(id="cluster:x", scope="engineering", type="Cluster", title="Geteilt")
        )
        service.upsert(
            ConceptDraft(id="cluster:x", scope="personal", type="Cluster", title="Eigen")
        )
        service.upsert(bruecke(body="Siehe [[cluster:x]]"))

        (kante,) = kanten(uow_factory, "personal")
        assert kante.to_store == "personal"


class TestNachtraeglicheAufloesung:
    def test_ein_spaeter_angelegtes_ziel_loest_die_bruecke_auf(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Der übliche Fall: Die Notiz ist älter als der erste Sync der Quelle."""
        service.upsert(bruecke())
        service.upsert(seite())

        anzahl = service.refresh_edge_resolution("personal")

        assert anzahl == 1
        assert kanten(uow_factory, "personal")[0].resolved is True

    def test_die_kante_wird_dabei_an_den_fremden_store_gehaengt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Aus der offenen Frage wird eine Brücke — ohne die Notiz anzufassen (§8.5).

        Eine unaufgelöste Kante hat über ihren Zielstore nie etwas behauptet. Ihn jetzt zu setzen
        nimmt deshalb nichts zurück; es beantwortet nur, was beim Anlegen niemand wissen konnte.
        """
        service.upsert(bruecke())
        service.upsert(seite())
        service.refresh_edge_resolution("personal")

        (kante,) = kanten(uow_factory, "personal")
        assert (kante.to_store, kante.resolved) == ("shared", True)

    def test_eine_bereits_bestehende_bruecke_wird_nicht_verdoppelt(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """``ux_edges_triple`` (§7.4) ließe die zweite ohnehin nicht zu."""
        service.upsert(bruecke())
        service.upsert(seite())
        service.refresh_edge_resolution("personal")
        service.refresh_edge_resolution("personal")

        assert len(kanten(uow_factory, "personal")) == 1

    def test_der_sync_des_geteilten_stores_loest_die_bruecke_von_aussen_auf(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Die Gegenrichtung (§12.1): Verändert wurde 'shared', betroffen ist 'personal'.

        Ohne diesen Weg bliebe die Kante bis zum nächsten Schreibvorgang *im persönlichen Store*
        falsch beschriftet — und den kann es lange nicht geben.
        """
        service.upsert(bruecke(body="Grundlage ist [[confluence:2]]."))
        service.upsert(seite(id="confluence:2", external_id="2"))

        anzahl = service.refresh_bridges_into("shared")

        assert anzahl == 1
        assert kanten(uow_factory, "personal")[0].resolved is True

    def test_der_persoenliche_store_hat_keine_eingehenden_bruecken(
        self, service: ConceptService
    ) -> None:
        """Es gibt keine erlaubte Richtung nach 'personal' — also auch nichts abzugleichen."""
        assert service.refresh_bridges_into("personal") == 0


class TestGrabsteine:
    def test_ein_grabstein_macht_die_bruecke_unaufloesbar(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§7.6: "Kanten bleiben bestehen und werden als ``resolved = false`` markiert.\""""
        service.upsert(seite())
        service.upsert(bruecke())
        service.mark_source_deleted("confluence:1", store="shared")

        anzahl = service.refresh_bridges_into("shared")

        assert anzahl == 1
        assert kanten(uow_factory, "personal")[0].resolved is False

    def test_die_kante_selbst_bleibt_erhalten(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Der Grund für Grabsteine überhaupt: Die Notiz soll nachvollziehbar bleiben (§7.6)."""
        service.upsert(seite())
        service.upsert(bruecke())
        service.mark_source_deleted("confluence:1", store="shared")
        service.refresh_bridges_into("shared")

        assert len(kanten(uow_factory, "personal")) == 1

    def test_eine_wiederherstellung_loest_erneut_auf(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """§7.6: ``tombstone --> stable: in Quelle wiederhergestellt``."""
        service.upsert(seite())
        service.upsert(bruecke())
        service.mark_source_deleted("confluence:1", store="shared")
        service.refresh_bridges_into("shared")

        service.upsert(seite())
        anzahl = service.refresh_bridges_into("shared")

        assert anzahl == 1
        assert kanten(uow_factory, "personal")[0].resolved is True

    def test_ein_grabstein_im_eigenen_store_wirkt_genauso(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Die Regel hängt nicht an der Store-Grenze, sondern am Begriff 'auffindbar'."""
        service.upsert(seite(id="confluence:2", external_id="2"))
        service.upsert(seite(body="Siehe [[confluence:2]]"))
        service.mark_source_deleted("confluence:2", store="shared")

        anzahl = service.refresh_edge_resolution("shared")

        kante = next(edge for edge in kanten(uow_factory, "shared") if edge.to_id == "confluence:2")
        assert anzahl == 1
        assert kante.resolved is False

    def test_eine_referenz_auf_einen_grabstein_entsteht_unaufgeloest(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        service.upsert(seite())
        service.mark_source_deleted("confluence:1", store="shared")
        service.upsert(bruecke())

        (kante,) = kanten(uow_factory, "personal")
        assert kante.resolved is False


class TestAnsicht:
    def test_ein_konzept_ist_aus_beiden_richtungen_auffindbar(
        self, service: ConceptService
    ) -> None:
        """Die Abnahme der Stufe 5: "in beide Richtungen auffindbar" (§24)."""
        service.upsert(seite())
        service.upsert(bruecke())

        von_der_bruecke = service.describe("project:finance", store="personal")
        vom_ziel = service.describe("confluence:1", store="shared")

        assert von_der_bruecke is not None
        assert vom_ziel is not None
        assert [edge.to_id for edge in von_der_bruecke.outgoing] == ["confluence:1"]
        assert [edge.from_id for edge in vom_ziel.incoming] == ["project:finance"]

    def test_die_gegenrichtung_liegt_nicht_im_zielstore(
        self, service: ConceptService, uow_factory: MemoryUnitOfWorkFactory
    ) -> None:
        """Sie wird rekonstruiert. Im geteilten Store selbst steht dazu nichts (§12.1)."""
        service.upsert(seite())
        service.upsert(bruecke())

        with uow_factory("shared") as uow:
            assert uow.edges.list_incoming("confluence:1") == ()

    def test_ein_unbekanntes_konzept_ergibt_nichts(self, service: ConceptService) -> None:
        assert service.describe("confluence:99", store="shared") is None

    def test_die_ansicht_nennt_status_und_auflösbarkeit(self, service: ConceptService) -> None:
        service.upsert(bruecke())

        ansicht = service.describe("project:finance", store="personal")

        assert ansicht is not None
        daten = ansicht.as_dict()
        assert daten["status"] == str(ConceptStatus.STABLE)
        assert daten["outgoing"] == [
            {"kind": "references", "to": "personal:confluence:1", "resolved": False}
        ]
