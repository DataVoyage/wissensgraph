"""Die generische Adapter-Contract-Suite (§22.3).

§22.3 nennt fünf Zusicherungen, die jeder Adapter einhalten muss, und §8.6 macht sie zur
Abnahmebedingung einer neuen Quelle: "Eine neue Quelle gilt als fertig, wenn diese Suite grün
ist."

Benutzung — der Adapter-Autor schreibt eine Klasse, keinen Test::

    class TestMeineQuelle(AdapterContractTests):
        @pytest.fixture
        def adapter(self):
            adapter = MeinAdapter()
            adapter.configure(meine_config)
            return adapter

Damit laufen alle Prüfungen. Wer mehr belegen will, überschreibt die Haken :meth:`aendern`,
:meth:`rate_limit_erzwingen` und :meth:`ausfall_erzwingen` — sie steuern die Quelle von außen.
Ohne sie überspringen sich die betroffenen Prüfungen und sagen auch, warum: Eine Zusicherung, die
nicht geprüft werden *kann*, soll als ungeprüft dastehen und nicht als bestanden.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from wissensgraph.ports.sources import (
    Cursor,
    HealthStatus,
    NotSupported,
    SourceAdapter,
    SourceDocument,
    SourceError,
)


class AdapterContractTests:
    """Die Prüfungen, die jeder :class:`SourceAdapter` besteht."""

    # -- Was eine Unterklasse beisteuert ----------------------------------------

    @pytest.fixture
    def adapter(self) -> SourceAdapter:
        """Ein fertig konfigurierter Adapter. Muss überschrieben werden."""
        raise NotImplementedError(
            "Eine Unterklasse von AdapterContractTests muss die Fixture 'adapter' liefern — "
            "einen bereits mit configure() versehenen Adapter."
        )

    def aendern(self, adapter: SourceAdapter) -> str | None:
        """Ändert genau ein Objekt in der Quelle und meldet dessen externe ID.

        Returns:
            Die geänderte externe ID, oder ``None``, wenn sich die Quelle nicht steuern lässt.
        """
        return None

    def rate_limit_erzwingen(self, adapter: SourceAdapter) -> bool:
        """Bringt die Quelle dazu, die nächsten Anfragen mit 429 zu beantworten.

        Returns:
            Ob es gelungen ist. ``False`` überspringt die Prüfung.
        """
        return False

    def ausfall_erzwingen(self, adapter: SourceAdapter) -> bool:
        """Bringt die Quelle dazu, *nach der ersten Anfrage* dauerhaft zu scheitern.

        "Nach der ersten" ist der Kern der Sache: Der Abbruch muss *mitten* in der Iteration
        passieren, sonst prüft §22.3s letzte Zusicherung nichts.

        Returns:
            Ob es gelungen ist. ``False`` überspringt die Prüfung.
        """
        return False

    def aufraeumen(self, adapter: SourceAdapter) -> None:
        """Nimmt erzwungene Störungen zurück. Wird nach jedem Störungstest aufgerufen."""

    # -- Der Kontrakt selbst ----------------------------------------------------

    def test_erfuellt_den_port(self, adapter: SourceAdapter) -> None:
        """Der Adapter erfüllt das Protokoll — strukturell, ohne von ihm zu erben."""
        assert isinstance(adapter, SourceAdapter)
        assert adapter.name, "Ein Adapter ohne Namen ist in der Registry nicht auffindbar (§8.3)."

    def test_health_liefert_einen_status(self, adapter: SourceAdapter) -> None:
        """§8.3: Ein Zustand, keine Ausnahme — sonst verhinderte ein Ausfall den Start."""
        assert isinstance(adapter.health(), HealthStatus)

    def test_iter_documents_ist_ein_generator(self, adapter: SourceAdapter) -> None:
        """§8.2 Regel 1: "lädt nie den gesamten Bestand in den Speicher"."""
        ergebnis = adapter.iter_documents(None)

        assert isinstance(ergebnis, Iterator)
        assert not isinstance(ergebnis, list | tuple), (
            "iter_documents gibt eine fertige Sammlung zurück. Damit ist der gesamte Bestand "
            "bereits im Speicher, bevor das erste Dokument verarbeitet wird (§8.2 Regel 1)."
        )

    def test_ohne_cursor_kommt_alles(self, adapter: SourceAdapter) -> None:
        """Ein Lauf ohne Cursor ist der Vollabgleich."""
        dokumente = list(adapter.iter_documents(None))

        assert dokumente, "Die Quelle liefert nichts — dann prüft diese Suite nichts."
        assert all(isinstance(item, SourceDocument) for item in dokumente)

    def test_pflichtfelder_sind_gesetzt(self, adapter: SourceAdapter) -> None:
        """§22.3: "Alle DTO-Pflichtfelder sind gesetzt"."""
        for document in adapter.iter_documents(None):
            assert document.external_id.strip(), (
                "Ein Dokument ohne external_id lässt sich keiner Konzept-ID zuordnen (§7.5)."
            )

    def test_external_id_ist_ueber_laeufe_stabil(self, adapter: SourceAdapter) -> None:
        """§22.3: "``external_id`` ist stabil über Läufe".

        Ohne diese Zusicherung entstünde bei jedem Lauf ein neues Konzept statt eines Updates —
        die Identitätsregel aus §10.2 hinge in der Luft.
        """
        erster = [item.external_id for item in adapter.iter_documents(None)]
        zweiter = [item.external_id for item in adapter.iter_documents(None)]

        assert erster == zweiter
        assert len(set(erster)) == len(erster), "Zwei Objekte tragen dieselbe external_id."

    def test_derselbe_cursor_liefert_dasselbe(self, adapter: SourceAdapter) -> None:
        """§8.2 Regel 4: "Der Adapter ist idempotent: derselbe Cursor liefert dasselbe Ergebnis"."""
        list(adapter.iter_documents(None))
        cursor = adapter.next_cursor()

        erster = [item.external_id for item in adapter.iter_documents(cursor)]
        zweiter = [item.external_id for item in adapter.iter_documents(cursor)]

        assert erster == zweiter

    def test_ohne_aenderung_bringt_der_zweite_lauf_nichts(self, adapter: SourceAdapter) -> None:
        """Die Grundlage von §22.2 Punkt 1: ein zweiter Sync ohne Quelländerung ändert nichts."""
        if not adapter.capabilities.incremental:
            pytest.skip("Ohne 'incremental' gibt es keinen Teilabgleich (§8.2).")

        list(adapter.iter_documents(None))
        cursor = adapter.next_cursor()

        assert list(adapter.iter_documents(cursor)) == []

    def test_mit_cursor_kommt_nur_geaendertes(self, adapter: SourceAdapter) -> None:
        """§22.3: "ein zweiter Lauf mit dem Cursor liefert nur Geändertes"."""
        if not adapter.capabilities.incremental:
            pytest.skip("Ohne 'incremental' gibt es keinen Teilabgleich (§8.2).")

        list(adapter.iter_documents(None))
        cursor = adapter.next_cursor()

        geaendert = self.aendern(adapter)
        if geaendert is None:
            pytest.skip(
                "Die Quelle lässt sich nicht steuern. Wer den Haken 'aendern' überschreibt, "
                "prüft diese Zusicherung wirklich."
            )

        gefunden = [item.external_id for item in adapter.iter_documents(cursor)]

        assert gefunden == [geaendert]

    def test_cursor_waechst_nur_bei_vollstaendigem_durchlauf(self, adapter: SourceAdapter) -> None:
        """Eine abgebrochene Iteration darf die Marke nicht fortschreiben.

        Das ist die zahme Fassung von §22.3s letzter Zusicherung: Hier bricht kein Netzwerk ab,
        sondern der Aufrufer hört einfach nach dem ersten Dokument auf. Für den Cursor ist das
        derselbe Fall — er hat den Rest des Bestands nicht gesehen.
        """
        vorher = adapter.next_cursor()

        strom = adapter.iter_documents(None)
        next(strom)
        schliessen = getattr(strom, "close", None)
        if schliessen is None:
            pytest.skip("Der Iterator lässt sich nicht schließen; der Abbruch ist nicht prüfbar.")
        schliessen()

        assert adapter.next_cursor() == vorher

    def test_nicht_deklarierte_faehigkeiten_werfen_notsupported(
        self, adapter: SourceAdapter
    ) -> None:
        """§22.3: "Nicht deklarierte Capabilities werfen ``NotSupported``"."""
        if not adapter.capabilities.deletions:
            with pytest.raises(NotSupported):
                list(adapter.list_deleted(None))
        if not adapter.capabilities.single_fetch:
            with pytest.raises(NotSupported):
                adapter.fetch("beliebig")

    def test_deklarierte_faehigkeiten_funktionieren(self, adapter: SourceAdapter) -> None:
        """Die Gegenprobe: Ein gesetztes Flag ist eine Zusage, kein Wunsch."""
        if adapter.capabilities.deletions:
            assert isinstance(list(adapter.list_deleted(None)), list)
        if adapter.capabilities.single_fetch:
            bekannt = next(iter(adapter.iter_documents(None)))
            geholt = adapter.fetch(bekannt.external_id)

            assert geholt is not None
            assert geholt.external_id == bekannt.external_id

    def test_unbekanntes_einzelobjekt_ist_kein_fehler(self, adapter: SourceAdapter) -> None:
        """``fetch`` auf eine unbekannte ID gibt ``None`` — ein gelöschtes Objekt ist normal."""
        if not adapter.capabilities.single_fetch:
            pytest.skip("Ohne 'single_fetch' gibt es kein Einzelobjekt (§8.2).")

        assert adapter.fetch("gibt-es-mit-sicherheit-nicht-4711") is None

    def test_rate_limit_fuehrt_zu_backoff_nicht_zum_abbruch(self, adapter: SourceAdapter) -> None:
        """§22.3: "Rate-Limit-Antworten (429) führen zu Backoff, nicht zum Abbruch"."""
        if not self.rate_limit_erzwingen(adapter):
            pytest.skip(
                "Die Quelle lässt sich nicht drosseln. Wer den Haken 'rate_limit_erzwingen' "
                "überschreibt, prüft diese Zusicherung wirklich."
            )
        try:
            dokumente = list(adapter.iter_documents(None))
        finally:
            self.aufraeumen(adapter)

        assert dokumente, "Ein 429 hat den Lauf beendet, statt ihn zu verzögern."

    def test_netzwerkfehler_laesst_den_cursor_unveraendert(self, adapter: SourceAdapter) -> None:
        """§22.3: "Netzwerkfehler mitten in der Iteration lassen den Cursor unverändert"."""
        list(adapter.iter_documents(None))
        vorher = adapter.next_cursor()

        if not self.ausfall_erzwingen(adapter):
            pytest.skip(
                "Die Quelle lässt sich nicht zum Ausfall bringen. Wer den Haken "
                "'ausfall_erzwingen' überschreibt, prüft diese Zusicherung wirklich."
            )
        try:
            with pytest.raises(SourceError):
                list(adapter.iter_documents(None))
        finally:
            self.aufraeumen(adapter)

        assert adapter.next_cursor() == vorher, (
            "Der Cursor ist trotz Abbruch fortgeschritten. Beim nächsten Lauf beginnt die Quelle "
            "hinter den nie gelesenen Objekten — sie fehlen dann dauerhaft (§22.3)."
        )

    def test_cursor_ist_serialisierbar(self, adapter: SourceAdapter) -> None:
        """Der Cursor wird als JSONB abgelegt (§8.2) — was nicht durch JSON passt, geht verloren."""
        import json

        list(adapter.iter_documents(None))
        cursor = adapter.next_cursor()

        wieder: Any = json.loads(json.dumps(cursor.value))

        assert Cursor(value=wieder) == cursor
