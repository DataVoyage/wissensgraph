"""Tests der Regeln 1 bis 4 aus §10.2 und der Kurationstabelle aus §10.4.

Diese Tests kommen ohne Datenbank aus. Das ist die eigentliche Aussage der Aufteilung zwischen
:mod:`wissensgraph.domain.upsert` und dem Dienst: Was ein Upsert *entscheidet*, ist eine reine
Funktion über zwei Zustände und lässt sich vollständig durchspielen. Regel 5 — die
Transaktionalität — ist die einzige, die eine Umgebung braucht; sie wird in
``test_concept_service.py`` und in den Integrationstests geprüft.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from wissensgraph.domain.concepts import Concept, ConceptDraft, ConceptStatus
from wissensgraph.domain.hashing import content_hash
from wissensgraph.domain.upsert import UpsertOutcome, plan_upsert

pytestmark = pytest.mark.unit

JETZT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
SPAETER = JETZT + timedelta(hours=1)


def entwurf(**overrides: object) -> ConceptDraft:
    """Ein Entwurf aus der Quelle ``confluence``, sofern nicht anders angegeben."""
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


def gespeichert(draft: ConceptDraft, **overrides: object) -> Concept:
    """Der gespeicherte Zustand, wie ihn ein erster Upsert erzeugt hätte."""
    werte: dict[str, object] = {
        **draft.model_dump(exclude={"references"}),
        "store": "shared",
        "content_hash": draft.content_hash,
        "created_at": JETZT,
        "updated_at": JETZT,
    }
    werte.update(overrides)
    return Concept.model_validate(werte)


def plane(existing: Concept | None, draft: ConceptDraft, *, source_mirrored: bool = True):
    """Kürzel für den immer gleichen Aufruf."""
    return plan_upsert(
        existing=existing,
        draft=draft,
        store="shared",
        source_mirrored=source_mirrored,
        now=SPAETER,
    )


class TestRegel1Identitaet:
    def test_unbekannte_id_wird_angelegt(self) -> None:
        plan = plane(None, entwurf())

        assert plan.outcome is UpsertOutcome.CREATED
        assert plan.concept is not None
        assert plan.concept.id == "confluence:1"

    def test_der_store_kommt_nicht_aus_dem_entwurf(self) -> None:
        """§20.1: Kein Codepfad wählt seinen Store selbst — auch kein Adapter über ein Feld."""
        plan = plan_upsert(
            existing=None,
            draft=entwurf(),
            store="personal",
            source_mirrored=False,
            now=SPAETER,
        )

        assert plan.concept is not None
        assert plan.concept.store == "personal"

    def test_der_hash_wird_beim_anlegen_gesetzt(self) -> None:
        draft = entwurf()

        plan = plane(None, draft)

        assert plan.concept is not None
        assert plan.concept.content_hash == content_hash(title="Titel", body="Inhalt")


class TestRegel2Und3Hash:
    def test_gleicher_hash_erzeugt_keinen_schreibvorgang(self) -> None:
        draft = entwurf()

        plan = plane(gespeichert(draft), draft)

        assert plan.outcome is UpsertOutcome.UNCHANGED
        assert plan.concept is None
        assert not plan.writes

    def test_geaenderter_hash_erzeugt_ein_update(self) -> None:
        draft = entwurf()
        neu = entwurf(body="Inhalt, überarbeitet")

        plan = plane(gespeichert(draft), neu)

        assert plan.outcome is UpsertOutcome.UPDATED
        assert plan.concept is not None
        assert plan.concept.body == "Inhalt, überarbeitet"
        assert plan.concept.updated_at == SPAETER

    def test_reine_tagaenderung_der_quelle_bleibt_folgenlos(self) -> None:
        """Der Hash umfasst nur die Inhaltsfelder (§10.3) — das ist der Preis der Sparsamkeit."""
        draft = entwurf()

        plan = plane(gespeichert(draft), entwurf(tags=("neu",)))

        assert plan.outcome is UpsertOutcome.UNCHANGED


class TestRegel4Kuration:
    def test_kuratierter_inhalt_ueberlebt_ein_quellupdate(self) -> None:
        """Der nicht gespiegelte Fall: Ein Mensch hat den Text geschrieben (§10.2 Regel 4)."""
        vorhanden = gespeichert(entwurf(title="Von Hand"), curated=True)

        plan = plane(vorhanden, entwurf(title="Aus der Quelle"), source_mirrored=False)

        assert plan.outcome is UpsertOutcome.CONFLICT
        assert plan.held_back == ("title",)

    def test_der_gehaltene_stand_wird_nicht_erneut_geschrieben(self) -> None:
        """Ein UPDATE, das nur updated_at fortschreibt, wäre eine Änderung ohne Änderung."""
        vorhanden = gespeichert(entwurf(title="Von Hand"), curated=True)

        plan = plane(vorhanden, entwurf(title="Aus der Quelle"), source_mirrored=False)

        assert plan.concept is None

    def test_bei_gespiegeltem_typ_gewinnt_die_quelle_auch_gegen_kuration(self) -> None:
        """§7.2: Bei source_mirrored sind Inhaltsfelder ohnehin schreibgeschützt (§10.4)."""
        vorhanden = gespeichert(entwurf(body="alt"), curated=True)

        plan = plane(vorhanden, entwurf(body="neu"), source_mirrored=True)

        assert plan.outcome is UpsertOutcome.UPDATED
        assert plan.concept is not None
        assert plan.concept.body == "neu"

    def test_lokale_aenderung_ueberschreibt_die_eigene_kuration(self) -> None:
        """Wer von Hand schreibt, schreibt gegen seinen eigenen früheren Stand."""
        vorhanden = gespeichert(
            entwurf(source_name=None, external_id=None, title="alt"), curated=True
        )

        plan = plane(
            vorhanden,
            entwurf(source_name=None, external_id=None, title="neu", curated=True),
            source_mirrored=False,
        )

        assert plan.outcome is UpsertOutcome.UPDATED
        assert plan.concept is not None
        assert plan.concept.title == "neu"

    def test_der_kuriert_marker_bleibt_bestehen(self) -> None:
        vorhanden = gespeichert(entwurf(), curated=True)

        plan = plane(vorhanden, entwurf(body="neu"))

        assert plan.concept is not None
        assert plan.concept.curated is True


class TestKurationstabelle104:
    def test_tags_sind_die_vereinigung(self) -> None:
        vorhanden = gespeichert(entwurf(tags=("kuriert",)))

        plan = plane(vorhanden, entwurf(body="neu", tags=("aus-der-quelle",)))

        assert plan.concept is not None
        assert plan.concept.tags == ("kuriert", "aus-der-quelle")

    def test_kuration_gewinnt_beim_status(self) -> None:
        vorhanden = gespeichert(entwurf(), status=ConceptStatus.DEPRECATED, curated=True)

        plan = plane(vorhanden, entwurf(body="neu", status=ConceptStatus.STABLE))

        assert plan.concept is not None
        assert plan.concept.status is ConceptStatus.DEPRECATED
        assert "status" in plan.held_back
        assert plan.outcome is UpsertOutcome.CONFLICT

    def test_geloescht_in_der_quelle_schlaegt_die_kuration(self) -> None:
        # §10.4: "Kuration gewinnt, außer die Quelle meldet Löschung."
        vorhanden = gespeichert(entwurf(), status=ConceptStatus.STABLE, curated=True)

        plan = plane(vorhanden, entwurf(body="neu", status=ConceptStatus.TOMBSTONE))

        assert plan.concept is not None
        assert plan.concept.status is ConceptStatus.TOMBSTONE
        assert plan.held_back == ()

    def test_ohne_kuration_gilt_der_status_der_quelle(self) -> None:
        vorhanden = gespeichert(entwurf())

        plan = plane(vorhanden, entwurf(body="neu", status=ConceptStatus.DEPRECATED))

        assert plan.concept is not None
        assert plan.concept.status is ConceptStatus.DEPRECATED

    def test_bestaetigung_faellt_mit_dem_inhalt(self) -> None:
        vorhanden = gespeichert(entwurf(), verified_by="user:mn", verified_at=JETZT)

        plan = plane(vorhanden, entwurf(body="neu"))

        assert plan.verification_reset is True
        assert plan.concept is not None
        assert plan.concept.verified_by is None
        assert plan.concept.verified_at is None

    def test_abgewehrte_quellaenderung_laesst_die_bestaetigung_stehen(self) -> None:
        """Der gespeicherte Inhalt hat sich nicht geändert — die Bestätigung bleibt gedeckt."""
        vorhanden = gespeichert(
            entwurf(title="Von Hand"), curated=True, verified_by="user:mn", verified_at=JETZT
        )

        plan = plane(vorhanden, entwurf(title="Aus der Quelle"), source_mirrored=False)

        assert plan.verification_reset is False

    def test_created_at_bleibt_der_erste_zeitpunkt(self) -> None:
        vorhanden = gespeichert(entwurf())

        plan = plane(vorhanden, entwurf(body="neu"))

        assert plan.concept is not None
        assert plan.concept.created_at == JETZT
        assert plan.concept.updated_at == SPAETER

    def test_quellzeitpunkt_wird_uebernommen(self) -> None:
        vorhanden = gespeichert(entwurf())

        plan = plane(vorhanden, entwurf(body="neu", source_updated_at=SPAETER))

        assert plan.concept is not None
        assert plan.concept.source_updated_at == SPAETER


class TestRueckkehrAusDemGrabstein:
    """§7.6: Ein Grabstein ist kein Endzustand — die Quelle kann ein Objekt wieder ausliefern.

    Die Aussage geht am Hash vorbei: Sie betrifft die *Existenz* des Objekts, nicht seinen Inhalt.
    Ohne diese Regel bliebe ein wiederhergestelltes Objekt für immer ein Grabstein, weil sein Text
    sich nicht geändert hat (§10.2 Regel 2).
    """

    def test_ein_wiedergeliefertes_objekt_verlaesst_den_grabstein(self) -> None:
        draft = entwurf()
        vorhanden = gespeichert(draft, status=ConceptStatus.TOMBSTONE)

        plan = plane(vorhanden, draft)

        assert plan.outcome is UpsertOutcome.UPDATED
        assert plan.concept is not None
        assert plan.concept.status is ConceptStatus.STABLE

    def test_eine_erneute_loeschmeldung_ist_keine_rueckkehr(self) -> None:
        draft = entwurf(status=ConceptStatus.TOMBSTONE)
        vorhanden = gespeichert(draft, status=ConceptStatus.TOMBSTONE)

        assert plane(vorhanden, draft).outcome is UpsertOutcome.UNCHANGED

    def test_ein_lokaler_schreibvorgang_holt_nichts_zurueck(self) -> None:
        """Nur eine Quelle kann sagen, dass ein Quellobjekt wieder da ist."""
        draft = entwurf(source_name=None, external_id=None)
        vorhanden = gespeichert(draft, status=ConceptStatus.TOMBSTONE)

        assert plane(vorhanden, draft).outcome is UpsertOutcome.UNCHANGED
