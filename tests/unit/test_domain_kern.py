"""Tests des Domänenkerns: IDs, Content-Hash, Referenzen, Modelle (§7.1, §7.5, §7.7, §10.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptDraft, ConceptStatus
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.hashing import content_hash
from wissensgraph.domain.ids import (
    InvalidConceptIdError,
    concept_id,
    is_valid_concept_id,
    new_cluster_id,
    new_note_id,
    project_id,
    source_concept_id,
    split_concept_id,
)
from wissensgraph.domain.references import extract_references

pytestmark = pytest.mark.unit

JETZT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class TestIdKonvention:
    @pytest.mark.parametrize(
        "value",
        ["confluence:184320", "jira:PROJ-123", "cluster:3f2a", "project:finance-integration"],
    )
    def test_beispiele_aus_dem_dokument_sind_gueltig(self, value: str) -> None:
        assert is_valid_concept_id(value)

    def test_trennt_am_ersten_doppelpunkt(self) -> None:
        # Eine Quelle darf externe IDs mit Doppelpunkt vergeben, ohne dass die ID zerfällt.
        assert split_concept_id("jira:PROJ:123") == ("jira", "PROJ:123")

    @pytest.mark.parametrize(
        ("value", "grund"),
        [
            ("ohne-doppelpunkt", "fehlender Trenner"),
            (":leer", "leeres Präfix"),
            ("Confluence:1", "Großbuchstabe im Präfix"),
            ("1source:x", "Ziffer am Anfang"),
            ("note:", "leerer lokaler Teil"),
            ("note:mit leerzeichen", "Leerraum"),
            ("note:mit[klammer]", "eckige Klammer"),
        ],
    )
    def test_verstoesse_werden_abgewiesen(self, value: str, grund: str) -> None:
        assert not is_valid_concept_id(value), grund
        with pytest.raises(InvalidConceptIdError):
            split_concept_id(value)

    def test_fehlermeldung_nennt_den_grund_und_die_erwartete_form(self) -> None:
        with pytest.raises(InvalidConceptIdError, match="lokale Teil ist leer"):
            concept_id("note", "")

        with pytest.raises(InvalidConceptIdError, match="fehlt der Doppelpunkt"):
            split_concept_id("ohne-trenner")

        with pytest.raises(InvalidConceptIdError, match="Präfix"):
            concept_id("", "note")

    def test_erzeugte_ids_sind_eindeutig_und_gueltig(self) -> None:
        ids = {new_cluster_id() for _ in range(50)} | {new_note_id() for _ in range(50)}

        assert len(ids) == 100
        assert all(is_valid_concept_id(item) for item in ids)

    def test_praefixe_folgen_der_tabelle_aus_75(self) -> None:
        assert new_cluster_id().startswith("cluster:")
        assert new_note_id().startswith("note:")
        assert project_id("finance-integration") == "project:finance-integration"
        assert source_concept_id("confluence", "184320") == "confluence:184320"


class TestContentHash:
    def test_gleicher_inhalt_gleicher_hash(self) -> None:
        erster = content_hash(title="A", description="B", body="C")
        zweiter = content_hash(title="A", description="B", body="C")

        assert erster == zweiter

    def test_feldgrenzen_sind_nicht_verschiebbar(self) -> None:
        """Ohne Trennzeichen wären diese beiden Konzepte für die Änderungserkennung gleich."""
        assert content_hash(title="ab", description="") != content_hash(title="a", description="b")

    def test_fehlende_felder_gelten_als_leer(self) -> None:
        assert content_hash() == content_hash(title=None, description=None, body=None)

    def test_nur_inhaltsfelder_gehen_ein(self) -> None:
        # §10.3: title + description + body. Tags oder Status ändern den Hash nicht — sonst
        # löste jede Kuration ein Re-Embedding aus.
        basis = ConceptDraft(id="note:1", scope="personal", type="Note", title="T", tags=("a",))
        ohne_tag = basis.model_copy(update={"tags": ()})

        assert basis.content_hash == ohne_tag.content_hash


class TestReferenzen:
    def test_liest_referenzen_in_reihenfolge(self) -> None:
        body = "Siehe [[confluence:1]] und danach [[jira:PROJ-2]]."

        assert extract_references(body) == ("confluence:1", "jira:PROJ-2")

    def test_dubletten_erscheinen_einmal(self) -> None:
        assert extract_references("[[note:a]] … [[note:a]]") == ("note:a",)

    def test_ungueltiges_ist_keine_referenz_und_kein_fehler(self) -> None:
        # §8.5: "Kaputte Referenzen sind kein Fehler."
        assert extract_references("[[kein wirklicher wert]] [[]] [[note:ok]]") == ("note:ok",)

    def test_leerraum_um_die_id_wird_ignoriert(self) -> None:
        assert extract_references("[[ note:a ]]") == ("note:a",)

    @pytest.mark.parametrize("body", [None, "", "ganz ohne Klammern"])
    def test_ohne_referenzen(self, body: str | None) -> None:
        assert extract_references(body) == ()

    def test_referenz_ueber_zeilenumbruch_hinweg_zaehlt_nicht(self) -> None:
        assert extract_references("[[note:a\nnote:b]]") == ()

    def test_selbstbezug_faellt_weg(self) -> None:
        # ck_edges_no_self (§7.4) würde ihn abweisen; ein Konzept, das sich selbst erwähnt, ist
        # aber gewöhnlicher Text und kein Fehler.
        draft = ConceptDraft(
            id="note:a", scope="personal", type="Note", body="Ich, [[note:a]], und [[note:b]]."
        )

        assert draft.all_references == ("note:b",)

    def test_adapter_referenzen_werden_mit_denen_aus_dem_body_vereinigt(self) -> None:
        draft = ConceptDraft(
            id="note:a",
            scope="personal",
            type="Note",
            body="[[note:b]]",
            references=("note:b", "note:c"),
        )

        assert draft.all_references == ("note:b", "note:c")


class TestKonzeptModell:
    def test_ungueltige_id_wird_abgewiesen(self) -> None:
        with pytest.raises(ValidationError):
            ConceptDraft(id="kaputt", scope="personal", type="Note")

    def test_tags_werden_getrimmt_und_entdoppelt(self) -> None:
        draft = ConceptDraft(
            id="note:a", scope="personal", type="Note", tags=("  x ", "x", "", "y")
        )

        assert draft.tags == ("x", "y")

    def test_provenienz_muss_vollstaendig_sein(self) -> None:
        # §7.1: generated_by/generated_at sind "bei Generiertem Pflicht".
        with pytest.raises(ValidationError, match="generated_by und generated_at"):
            ConceptDraft(id="note:a", scope="personal", type="Note", generated_by="model:x")

    def test_quellzuordnung_muss_vollstaendig_sein(self) -> None:
        with pytest.raises(ValidationError, match="source_name und external_id"):
            ConceptDraft(id="confluence:1", scope="engineering", type="Note", source_name="conf")

    def test_unbekanntes_feld_bricht_ab(self) -> None:
        with pytest.raises(ValidationError):
            ConceptDraft(id="note:a", scope="personal", type="Note", titel="Tippfehler")

    def test_konzept_ist_unveraenderlich(self) -> None:
        concept = _konzept()

        with pytest.raises(ValidationError):
            concept.title = "anders"  # type: ignore[misc]

    def test_status_default_ist_stable(self) -> None:
        assert ConceptDraft(id="note:a", scope="personal", type="Note").status is (
            ConceptStatus.STABLE
        )

    def test_is_from_source_unterscheidet_quelle_von_handarbeit(self) -> None:
        lokal = ConceptDraft(id="note:a", scope="personal", type="Note")
        aus_quelle = ConceptDraft(
            id="confluence:1",
            scope="engineering",
            type="Confluence Page",
            source_name="confluence",
            external_id="1",
        )

        assert not lokal.is_from_source
        assert aus_quelle.is_from_source


class TestKantenModell:
    def test_selbstkante_wird_abgewiesen(self) -> None:
        with pytest.raises(ValidationError, match="Ausgangspunkt"):
            EdgeDraft(
                from_store="shared",
                from_id="confluence:1",
                to_store="shared",
                to_id="confluence:1",
                kind="references",
            )

    def test_gleiche_id_in_anderem_store_ist_zulaessig(self) -> None:
        """Die Invariante ist (Store, ID) — nicht die ID allein (§7.3)."""
        edge = EdgeDraft(
            from_store="personal",
            from_id="cluster:a",
            to_store="shared",
            to_id="cluster:a",
            kind="related",
        )

        assert edge.triple == ("personal", "cluster:a", "shared", "cluster:a", "related")

    def test_resolved_ist_ohne_angabe_false(self) -> None:
        edge = EdgeDraft(
            from_store="shared",
            from_id="confluence:1",
            to_store="shared",
            to_id="confluence:2",
            kind="references",
        )

        assert edge.resolved is False


class TestJournalEintrag:
    def test_eintrag_ohne_ziel_ist_wertlos(self) -> None:
        with pytest.raises(ValidationError, match="nicht zuzuordnen"):
            ChangeEntry(change_type=ChangeType.UPDATED, actor="system:sync")

    def test_kante_allein_genuegt_als_ziel(self) -> None:
        from uuid import uuid4

        entry = ChangeEntry(
            change_type=ChangeType.EDGE_REMOVED, actor="system:sync", edge_id=uuid4()
        )

        assert entry.concept_id is None

    def test_die_beiden_ergaenzten_arten_sind_vorhanden(self) -> None:
        # §10.2 Regel 4 und §10.4 verlangen sie, §7.4 zählt sie nicht auf.
        assert ChangeType.CURATION_CONFLICT == "curation_conflict"
        assert ChangeType.VERIFICATION_RESET == "verification_reset"


def _konzept(**overrides: object) -> Concept:
    """Ein gespeichertes Konzept mit sinnvollen Vorgaben."""
    werte: dict[str, object] = {
        "id": "confluence:1",
        "store": "shared",
        "scope": "engineering",
        "type": "Confluence Page",
        "title": "Titel",
        "content_hash": content_hash(title="Titel"),
        "created_at": JETZT,
        "updated_at": JETZT,
    }
    werte.update(overrides)
    return Concept.model_validate(werte)


class TestListenNormierung:
    def test_nichtlisten_werden_durchgereicht(self) -> None:
        """Die Normierung korrigiert nicht, sie normiert — den Typfehler meldet Pydantic."""
        from wissensgraph.domain.base import unique_strings

        assert unique_strings("keine liste") == "keine liste"

    def test_gemischte_listen_bleiben_unangetastet(self) -> None:
        from wissensgraph.domain.base import unique_strings

        assert unique_strings(["a", 1]) == ["a", 1]

    def test_pydantic_meldet_den_typfehler(self) -> None:
        with pytest.raises(ValidationError):
            ConceptDraft(id="note:a", scope="personal", type="Note", tags=["a", 1])
