"""Tests der JSONPath-Teilmenge für die Mapping-Konfiguration (§8.4)."""

from __future__ import annotations

import pytest

from wissensgraph.infrastructure.adapters.jsonpath import JsonPath, JsonPathError

pytestmark = pytest.mark.unit

BEISPIEL = {
    "title": "Zahlungsabgleich",
    "excerpt": None,
    "body": {"storage": {"value": "<p>Text</p>"}},
    "links": {"webui": "/spaces/ENG/pages/1"},
    "metadata": {"labels": [{"name": "finanzen"}, {"name": "prozess"}]},
    "versions": [{"when": "2026-01-01"}, {"when": "2026-02-01"}],
    "leer": [],
}


class TestAusdrueckeAusDemDokument:
    @pytest.mark.parametrize(
        ("ausdruck", "erwartet"),
        [
            ("$.title", "Zahlungsabgleich"),
            ("$.body.storage.value", "<p>Text</p>"),
            ("$.links.webui", "/spaces/ENG/pages/1"),
            ("$.versions[0].when", "2026-01-01"),
            ("$.versions[-1].when", "2026-02-01"),
            ("$['title']", "Zahlungsabgleich"),
            ('$["title"]', "Zahlungsabgleich"),
        ],
    )
    def test_einzelwerte(self, ausdruck: str, erwartet: str) -> None:
        assert JsonPath.parse(ausdruck).first(BEISPIEL) == erwartet

    def test_platzhalter_ueber_eine_liste(self) -> None:
        """Genau die Schreibweise aus §8.4: ``$.metadata.labels[*].name``."""
        assert JsonPath.parse("$.metadata.labels[*].name").find(BEISPIEL) == [
            "finanzen",
            "prozess",
        ]

    def test_platzhalter_ueber_ein_objekt(self) -> None:
        assert JsonPath.parse("$.body[*]").find(BEISPIEL) == [{"value": "<p>Text</p>"}]

    def test_die_wurzel_allein_ist_das_ganze_dokument(self) -> None:
        assert JsonPath.parse("$").first(BEISPIEL) == BEISPIEL


class TestNichtsGefunden:
    @pytest.mark.parametrize(
        "ausdruck",
        ["$.gibtesnicht", "$.body.gibtesnicht.value", "$.versions[99].when", "$.leer[*]"],
    )
    def test_ein_fehlender_schritt_ist_kein_fehler(self, ausdruck: str) -> None:
        """§8.4 sieht ein leeres Feld ausdrücklich vor ("leer → wird per Task erzeugt")."""
        assert JsonPath.parse(ausdruck).find(BEISPIEL) == []
        assert JsonPath.parse(ausdruck).first(BEISPIEL) is None

    def test_ein_gesetztes_none_ist_ein_treffer(self) -> None:
        """Zwischen "Feld fehlt" und "Feld ist null" liegt ein Unterschied."""
        assert JsonPath.parse("$.excerpt").find(BEISPIEL) == [None]

    def test_ein_schritt_auf_dem_falschen_typ_liefert_nichts(self) -> None:
        assert JsonPath.parse("$.title.tiefer").find(BEISPIEL) == []
        assert JsonPath.parse("$.title[0]").find(BEISPIEL) == []


class TestNichtUnterstuetzt:
    @pytest.mark.parametrize(
        ("ausdruck", "grund"),
        [
            ("", "leerer Ausdruck"),
            ("title", "ohne Wurzel"),
            ("$..title", "rekursiver Abstieg"),
            ("$.items[?(@.a)]", "Filterausdruck"),
            ("$.items[0:2]", "Slice"),
            ("$.title(", "Klammer"),
        ],
    )
    def test_der_fehler_kommt_beim_parsen_und_nicht_beim_lauf(
        self, ausdruck: str, grund: str
    ) -> None:
        with pytest.raises(JsonPathError):
            JsonPath.parse(ausdruck)

    def test_die_meldung_nennt_stelle_und_das_erlaubte(self) -> None:
        with pytest.raises(JsonPathError, match="Position 7") as fehler:
            JsonPath.parse("$.title..")

        assert "Unterstützt sind" in str(fehler.value)


class TestEigenschaften:
    def test_is_multi_erkennt_den_platzhalter(self) -> None:
        assert JsonPath.parse("$.metadata.labels[*].name").is_multi
        assert not JsonPath.parse("$.title").is_multi

    def test_ein_geparster_ausdruck_ist_wiederverwendbar(self) -> None:
        pfad = JsonPath.parse("$.title")

        assert pfad.first(BEISPIEL) == pfad.first(BEISPIEL)
        assert pfad.first({"title": "anders"}) == "anders"

    def test_leerraum_um_den_ausdruck_stoert_nicht(self) -> None:
        assert JsonPath.parse("  $.title  ").first(BEISPIEL) == "Zahlungsabgleich"
