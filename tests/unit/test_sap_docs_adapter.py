"""Der SAP-docs-Adapter: Kennungen, Verweise, Anriss (§8.2, §8.5).

Den allgemeinen Kontrakt prüft die Contract-Suite (§22.3). Hier steht, was nur diese Quelle
betrifft — und das ist vor allem die Verweisauflösung: Aus relativen Markdown-Links werden
Kanten, und ordnerübergreifende Verweise (`../30-development/…`) sind dabei die interessanten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import quellen
from wissensgraph.infrastructure.adapters.sap_docs import SapDocsAdapter
from wissensgraph.ports.sources import HealthState, SourceError

pytestmark = pytest.mark.unit


def schreiben(pfad: Path, kennung: str | None, titel: str, text: str) -> None:
    """Legt eine Datei im SAP-docs-Format an; ohne Kennung fehlt der Kommentar."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    kopf = f"<!-- loio{kennung} -->\n\n" if kennung else ""
    pfad.write_text(f"{kopf}# {titel}\n\n\n\n{text}\n", encoding="utf-8")


def quelle(wurzel: Path, **selection: object) -> object:
    return quellen.quelle(
        "sap-btp-doku",
        adapter="sap-docs",
        id_prefix="confluence",
        default_type="Confluence Page",
        base_url="https://github.com/SAP-docs/btp-cloud-platform/blob/main",
        selection={"directory": str(wurzel), **selection},
    )


@pytest.fixture
def bestand(tmp_path: Path) -> Path:
    """Drei Dokumente in zwei Ordnern, mit Verweisen in beide Richtungen."""
    wurzel = tmp_path / "docs"
    schreiben(
        wurzel / "10-concepts" / "account-model-8ed4a70.md",
        "8ed4a705efa0431b910056c0acdbf377",
        "Account Model",
        "Learn more about accounts. Siehe [Regionen](regions-2f3b1c4.md) und "
        "[ABAP](../30-development/abap-development-fa5af4e.md).",
    )
    schreiben(
        wurzel / "10-concepts" / "regions-2f3b1c4.md",
        "2f3b1c4d5e6f7a8b9c0d1e2f3a4b5c6d",
        "Regions",
        "Ein Text ohne Verweise.",
    )
    schreiben(
        wurzel / "30-development" / "abap-development-fa5af4e.md",
        "fa5af4ecdf90496b8eec54fe0e22150c",
        "ABAP Development",
        "Verweist zurück auf [Account Model](../10-concepts/account-model-8ed4a70.md).",
    )
    return wurzel


def gebaut(wurzel: Path, **selection: object) -> SapDocsAdapter:
    adapter = SapDocsAdapter()
    adapter.configure(quelle(wurzel, **selection))
    return adapter


class TestKennungen:
    def test_die_stabile_sap_kennung_wird_zur_externen_id(self, bestand: Path) -> None:
        """§22.3: Die externe ID muss über Läufe stabil sein — der Dateiname ist es nicht."""
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        assert dokumente["Account Model"].external_id == "8ed4a705efa0431b910056c0acdbf377"

    def test_ein_dokument_ohne_kennung_faellt_auf_seinen_pfad_zurueck(self, tmp_path: Path) -> None:
        """Lieber eine schwächere ID als ein ausgelassenes Dokument."""
        wurzel = tmp_path / "docs"
        schreiben(wurzel / "10-concepts" / "ohne-kennung.md", None, "Ohne Kennung", "Text.")

        dokumente = list(gebaut(wurzel).iter_documents(None))

        assert [d.external_id for d in dokumente] == ["10-concepts/ohne-kennung.md"]

    def test_der_titel_kommt_aus_der_ersten_ueberschrift(self, bestand: Path) -> None:
        titel = {d.title for d in gebaut(bestand).iter_documents(None)}

        assert titel == {"Account Model", "Regions", "ABAP Development"}


class TestVerweise:
    def test_relative_links_werden_zu_kanten(self, bestand: Path) -> None:
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        ziele = {r.target for r in dokumente["Account Model"].references}
        assert ziele == {
            "2f3b1c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "fa5af4ecdf90496b8eec54fe0e22150c",
        }

    def test_ordneruebergreifende_verweise_finden_ihr_ziel(self, bestand: Path) -> None:
        """Der Fall mit `..` — ohne saubere Pfadauflösung fällt genau er stillschweigend weg."""
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        assert [r.target for r in dokumente["ABAP Development"].references] == [
            "8ed4a705efa0431b910056c0acdbf377"
        ]

    def test_verweise_aus_dem_bestand_heraus_werden_uebergangen(self, tmp_path: Path) -> None:
        """Eine Kante auf ein Dokument, das es hier nie geben wird, wäre eine Behauptung."""
        wurzel = tmp_path / "docs"
        schreiben(
            wurzel / "10-concepts" / "a-1234567.md",
            "aaaa1111",
            "A",
            "Siehe [woanders](../99-nicht-ausgecheckt/x-9999999.md) und "
            "[extern](https://help.sap.com/docs/x.md) und [Anker](#abschnitt).",
        )

        dokumente = list(gebaut(wurzel).iter_documents(None))

        assert dokumente[0].references == ()


class TestInhalt:
    def test_die_beschreibung_ist_der_erste_echte_absatz(self, bestand: Path) -> None:
        """Was hier steht, sieht das Embedding zuerst — Überschriften taugen dafür nicht."""
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        beschreibung = dokumente["Account Model"].description or ""
        assert beschreibung.startswith("Learn more about accounts.")

    def test_die_markdown_maskierung_verschwindet_aus_titel_und_anriss(
        self, tmp_path: Path
    ) -> None:
        """SAP maskiert Klammern; in einem Titel ist das nur ein Rückstand des Formats."""
        wurzel = tmp_path / "docs"
        schreiben(
            wurzel / "50-administration-and-ops" / "event-1c38309.md",
            "1c38309b07c44272ae165",
            r"Business Event Header Data \(v2\)",
            r"Die Kopfdaten \(Version 2\) eines Ereignisses.",
        )

        dokument = next(iter(gebaut(wurzel).iter_documents(None)))

        assert dokument.title == "Business Event Header Data (v2)"
        assert dokument.description == "Die Kopfdaten (Version 2) eines Ereignisses."

    def test_der_kennungskommentar_steht_nicht_im_text(self, bestand: Path) -> None:
        dokumente = list(gebaut(bestand).iter_documents(None))

        assert all("loio" not in (d.body or "") for d in dokumente)

    def test_der_ordner_wird_zum_schlagwort(self, bestand: Path) -> None:
        """Die Gliederung der Quelle — später der Prüfstein für das Clustering."""
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        assert dokumente["Account Model"].tags == ("10-concepts",)
        assert dokumente["ABAP Development"].tags == ("30-development",)

    def test_die_adresse_zeigt_auf_die_quelle(self, bestand: Path) -> None:
        dokumente = {d.title: d for d in gebaut(bestand).iter_documents(None)}

        assert dokumente["Regions"].resource == (
            "https://github.com/SAP-docs/btp-cloud-platform/blob/main/10-concepts/regions-2f3b1c4.md"
        )


class TestAuswahl:
    def test_limit_deckelt_den_bestand(self, bestand: Path) -> None:
        assert len(list(gebaut(bestand, limit=2).iter_documents(None))) == 2

    def test_folders_waehlt_ordner_aus(self, bestand: Path) -> None:
        dokumente = list(gebaut(bestand, folders=["30-development"]).iter_documents(None))

        assert [d.title for d in dokumente] == ["ABAP Development"]

    def test_exclude_haelt_navigationsartefakte_heraus(self, bestand: Path) -> None:
        """Der Anlass ist gemessen: `index.md` brachte 2.069 von 4.918 Kanten mit."""
        schreiben(
            bestand / "index.md",
            "0000ffff",
            "Inhalt",
            "Alles: [A](10-concepts/regions-2f3b1c4.md).",
        )

        ohne = gebaut(bestand, exclude=["index.md"])
        mit = gebaut(bestand)

        assert len(list(mit.iter_documents(None))) == 4
        assert "Inhalt" not in {d.title for d in ohne.iter_documents(None)}


class TestZustand:
    def test_ein_fehlendes_verzeichnis_ist_unhealthy_und_kein_startfehler(
        self, tmp_path: Path
    ) -> None:
        """§8.3: Ein kaputter Adapter schaltet sich ab, statt den Dienst zu verhindern."""
        adapter = gebaut(tmp_path / "gibt-es-nicht")

        assert adapter.health().state is HealthState.UNHEALTHY
        assert "git clone" in adapter.health().detail

    def test_ein_leeres_verzeichnis_ist_degraded(self, tmp_path: Path) -> None:
        leer = tmp_path / "docs"
        leer.mkdir()

        assert gebaut(leer).health().state is HealthState.DEGRADED

    def test_ohne_directory_bricht_die_konfiguration_ab(self) -> None:
        """Ein fehlender Pflichtwert ist ein Konfigurationsfehler und keine Quellstörung."""
        adapter = SapDocsAdapter()

        with pytest.raises(SourceError, match=r"selection\.directory"):
            adapter.configure(
                quellen.quelle("sap-btp-doku", adapter="sap-docs", id_prefix="confluence")
            )

    def test_fetch_holt_ein_einzelnes_dokument(self, bestand: Path) -> None:
        adapter = gebaut(bestand)

        gefunden = adapter.fetch("fa5af4ecdf90496b8eec54fe0e22150c")

        assert gefunden is not None
        assert gefunden.title == "ABAP Development"
        assert adapter.fetch("gibt-es-nicht") is None
