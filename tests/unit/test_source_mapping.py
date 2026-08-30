"""Tests der Abbildung: Rohobjekt -> SourceDocument -> ConceptDraft (§8.4, §8.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from support import quellen
from wissensgraph.domain.concepts import ConceptStatus
from wissensgraph.infrastructure.adapters.mapping import DocumentMapping, MappingError
from wissensgraph.ports.sources import SourceDocument
from wissensgraph.services.sources import SourceMapper

pytestmark = pytest.mark.unit

SEITE = {
    "id": "184320",
    "title": "Zahlungsabgleich",
    "excerpt": "Kurzfassung",
    "body": {"storage": {"value": "Langer Text"}},
    "links": {"webui": "/spaces/ENG/pages/184320"},
    "metadata": {"labels": [{"name": "finanzen"}, {"name": "prozess"}]},
    "version": {"when": "2026-05-01T10:00:00+00:00"},
}


class TestDocumentMapping:
    def test_die_ausdruecke_aus_84_greifen(self) -> None:
        mapping = DocumentMapping(
            {
                "title": "$.title",
                "description": "$.excerpt",
                "body": "$.body.storage.value",
                "resource": "$.links.webui",
                "tags": "$.metadata.labels[*].name",
                "updated_at": "$.version.when",
            },
            source="confluence-eng",
        )

        assert mapping.apply(SEITE) == {
            "title": "Zahlungsabgleich",
            "description": "Kurzfassung",
            "body": "Langer Text",
            "resource": "/spaces/ENG/pages/184320",
            "tags": ("finanzen", "prozess"),
            "updated_at": "2026-05-01T10:00:00+00:00",
        }

    def test_ein_leeres_ergebnis_faellt_weg(self) -> None:
        """Damit bleibt die Vorgabe des Adapters stehen, statt vom Mapping geleert zu werden."""
        mapping = DocumentMapping({"description": "$.gibtesnicht"})

        assert mapping.apply(SEITE) == {}

    def test_zahlen_werden_zu_zeichenketten(self) -> None:
        assert DocumentMapping({"title": "$.id"}).apply(SEITE) == {"title": "184320"}

    def test_updated_at_bleibt_roh(self) -> None:
        """Die Umwandlung in einen Zeitpunkt gehört ins Modell, nicht ins Mapping."""
        mapping = DocumentMapping({"updated_at": "$.zahl"})

        assert mapping.apply({"zahl": 1735689600}) == {"updated_at": 1735689600}

    def test_ein_einzelfeld_auf_einer_struktur_ist_ein_fehler(self) -> None:
        with pytest.raises(MappingError, match="erwartet wird ein einzelner Wert"):
            DocumentMapping({"title": "$.body"}).apply(SEITE)

    def test_ein_listenfeld_auf_verschachteltem_ist_ein_fehler(self) -> None:
        with pytest.raises(MappingError, match="verschachtelte Struktur"):
            DocumentMapping({"tags": "$.metadata.labels[*]"}).apply(SEITE)

    def test_der_fehler_kommt_beim_konfigurieren(self) -> None:
        """Ein Tippfehler soll beim Start auffallen, nicht bei Seite 4.000 eines Laufs."""
        with pytest.raises(MappingError, match="Quelle 'confluence-eng'"):
            DocumentMapping({"title": "$..title"}, source="confluence-eng")

    def test_ein_unbekanntes_feld_wird_abgewiesen(self) -> None:
        with pytest.raises(MappingError, match="nicht abbildbar"):
            DocumentMapping({"status": "$.status"})

    def test_leeres_mapping(self) -> None:
        mapping = DocumentMapping({})

        assert len(mapping) == 0
        assert "title" not in mapping
        assert mapping.apply(SEITE) == {}


class TestSourceMapper:
    def _mapper(self, **rest: object) -> SourceMapper:
        cfg = quellen.quelle("confluence-eng", adapter="confluence", id_prefix="confluence", **rest)
        return SourceMapper(cfg, known_prefixes=("confluence", "jira"))

    def test_id_scope_und_typ_kommen_aus_der_konfiguration(self) -> None:
        draft = self._mapper().to_draft(SourceDocument(external_id="184320", title="T"))

        assert draft.id == "confluence:184320"
        assert draft.scope == "engineering"
        assert draft.type == "Confluence Page"
        assert draft.status is ConceptStatus.STABLE

    def test_der_typhinweis_schlaegt_den_default(self) -> None:
        draft = self._mapper().to_draft(
            SourceDocument(external_id="1", type_hint="Cluster", title="T")
        )

        assert draft.type == "Cluster"

    def test_der_typhinweis_kann_den_scope_nicht_wechseln(self) -> None:
        """§20.1: Kein Feld aus der Quelle bestimmt, in welche Datenbank geschrieben wird."""
        draft = self._mapper().to_draft(SourceDocument(external_id="1", type_hint="Note"))

        assert draft.scope == "engineering"

    def test_provenienz_wird_gesetzt(self) -> None:
        geaendert = datetime(2026, 5, 1, 10, tzinfo=UTC)

        draft = self._mapper().to_draft(
            SourceDocument(external_id="184320", title="T", updated_at=geaendert)
        )

        assert draft.source_name == "confluence-eng"
        assert draft.external_id == "184320"
        assert draft.source_updated_at == geaendert
        assert draft.is_from_source

    def test_der_quellname_ist_die_instanz_nicht_der_adapter(self) -> None:
        """§8.4: Zwei Instanzen desselben Adapters sind vorgesehen — erst der Name unterscheidet."""
        cfg = quellen.quelle("confluence-fin", adapter="confluence", id_prefix="conffin")

        draft = SourceMapper(cfg).to_draft(SourceDocument(external_id="1"))

        assert draft.source_name == "confluence-fin"

    def test_externe_referenzen_bekommen_das_praefix_der_quelle(self) -> None:
        """§8.5: "übersetzt sie über das Präfix der Quelle in interne IDs"."""
        draft = self._mapper().to_draft(SourceDocument(external_id="1", references=("2", "3")))

        assert draft.references == ("confluence:2", "confluence:3")

    def test_eine_referenz_mit_bekanntem_praefix_bleibt_stehen(self) -> None:
        """Der quellübergreifende Verweis: ein Jira-Vorgang zeigt auf eine Confluence-Seite."""
        draft = self._mapper().to_draft(
            SourceDocument(external_id="1", references=("jira:TEAM-4",))
        )

        assert draft.references == ("jira:TEAM-4",)

    def test_ein_unbekanntes_praefix_gilt_als_externe_id(self) -> None:
        """Sonst zeigte eine externe ID mit Doppelpunkt auf ein Konzept, das es nie geben wird."""
        draft = self._mapper().to_draft(SourceDocument(external_id="1", references=("ABC:42",)))

        assert draft.references == ("confluence:ABC:42",)

    def test_referenzen_aus_dem_text_bleiben_unangetastet(self) -> None:
        """Eine '[[id]]'-Referenz ist bereits eine interne ID — sie wird nicht übersetzt."""
        draft = self._mapper().to_draft(
            SourceDocument(external_id="1", body="Siehe [[jira:TEAM-9]].")
        )

        assert draft.body_references == ("jira:TEAM-9",)
        assert draft.source_references == ()

    def test_text_schlaegt_quelle_bei_gleicher_zielid(self) -> None:
        draft = self._mapper().to_draft(
            SourceDocument(external_id="1", body="Siehe [[confluence:2]].", references=("2",))
        )

        assert draft.body_references == ("confluence:2",)
        assert draft.source_references == ()
        assert draft.all_references == ("confluence:2",)

    def test_inhalt_wird_unveraendert_durchgereicht(self) -> None:
        draft = self._mapper().to_draft(
            SourceDocument(
                external_id="1",
                title="Titel",
                description="Kurz",
                body="Lang",
                resource="/x",
                tags=("a", "b"),
            )
        )

        assert (draft.title, draft.description, draft.body, draft.resource) == (
            "Titel",
            "Kurz",
            "Lang",
            "/x",
        )
        assert draft.tags == ("a", "b")
