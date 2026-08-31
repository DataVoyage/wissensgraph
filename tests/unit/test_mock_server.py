"""Tests des Mock-Quellservers und seiner Steuerungs-API (§9).

Der Mock ist Entwicklungswerkzeug und wird deshalb selbst geprüft: Ein Mock, der falsch
paginiert, lässt einen korrekten Adapter scheitern — und man sucht den Fehler dann an der
falschen Stelle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from support import quellen
from wissensgraph.mocks import FixturesNotFound, MockState, create_mock_app

pytestmark = pytest.mark.unit

CONFLUENCE = "/confluence/rest/api"
JIRA = "/jira/rest/api/3"


@pytest.fixture
def client() -> TestClient:
    """Ein Client auf eine frische Mock-Anwendung mit den Seed-Daten des Repositories."""
    return TestClient(quellen.mock_app(), base_url=quellen.CONTROL_BASE)


class TestSeedDaten:
    def test_der_korpus_hat_den_umfang_aus_92(self, client: TestClient) -> None:
        """§9.2: "~120 Seiten" und "~80 Issues"."""
        zustand = client.get("/_control/state").json()

        assert zustand["pages"] == 120
        assert zustand["issues"] == 80
        assert zustand["spaces"] == 2

    def test_es_gibt_verweise_fuer_die_referenzaufloesung(self, client: TestClient) -> None:
        assert client.get("/_control/state").json()["links"] > 100

    def test_ein_fehlendes_seed_verzeichnis_bricht_ab(self, tmp_path: Path) -> None:
        """Ein Mock ohne Daten sähe aus wie eine leere Quelle statt wie ein Fehler."""
        with pytest.raises(FixturesNotFound, match=r"§9\.2"):
            create_mock_app(tmp_path / "gibt-es-nicht")


class TestConfluenceEndpunkte:
    def test_spaces(self, client: TestClient) -> None:
        antwort = client.get(f"{CONFLUENCE}/space").json()

        assert {item["key"] for item in antwort["results"]} == {"ENG", "ARCH"}

    def test_paginierung(self, client: TestClient) -> None:
        erste = client.get(f"{CONFLUENCE}/content", params={"start": 0, "limit": 25}).json()
        zweite = client.get(f"{CONFLUENCE}/content", params={"start": 25, "limit": 25}).json()

        assert len(erste["results"]) == 25
        assert erste["totalSize"] == 120
        assert "next" in erste["_links"]
        assert {item["id"] for item in erste["results"]}.isdisjoint(
            {item["id"] for item in zweite["results"]}
        )

    def test_die_letzte_seite_hat_keinen_nachfolger(self, client: TestClient) -> None:
        letzte = client.get(f"{CONFLUENCE}/content", params={"start": 100, "limit": 25}).json()

        assert len(letzte["results"]) == 20
        assert "_links" not in letzte

    def test_die_reihenfolge_ist_stabil(self, client: TestClient) -> None:
        """Ohne sie liefert dieselbe Seite bei zwei Läufen andere Objekte."""
        erste = client.get(f"{CONFLUENCE}/content", params={"limit": 10}).json()
        zweite = client.get(f"{CONFLUENCE}/content", params={"limit": 10}).json()

        assert [item["id"] for item in erste["results"]] == [
            item["id"] for item in zweite["results"]
        ]

    def test_filter_nach_space(self, client: TestClient) -> None:
        antwort = client.get(f"{CONFLUENCE}/content", params={"spaceKey": "ARCH", "limit": 200})

        spaces = {item["space"]["key"] for item in antwort.json()["results"]}

        assert spaces == {"ARCH"}

    def test_die_verweise_aus_linksjson_haengen_an_der_seite(self, client: TestClient) -> None:
        seite = client.get(f"{CONFLUENCE}/content/100001").json()

        assert seite["links"]["internal"] == ["100002"]

    def test_unbekannte_seite(self, client: TestClient) -> None:
        assert client.get(f"{CONFLUENCE}/content/999999").status_code == 404

    def test_geloeschte_sind_anfangs_leer(self, client: TestClient) -> None:
        assert client.get(f"{CONFLUENCE}/content/deleted").json()["results"] == []


class TestJiraEndpunkte:
    def test_boards(self, client: TestClient) -> None:
        assert client.get("/jira/rest/agile/1.0/board").json()["total"] == 1

    def test_paginierung(self, client: TestClient) -> None:
        antwort = client.get(f"{JIRA}/search", params={"startAt": 0, "maxResults": 30}).json()

        assert len(antwort["issues"]) == 30
        assert antwort["total"] == 80

    def test_einzelner_vorgang(self, client: TestClient) -> None:
        assert client.get(f"{JIRA}/issue/TEAM-4").json()["fields"]["labels"] == [
            "datenpipeline",
            "poc",
        ]

    def test_unbekannter_vorgang(self, client: TestClient) -> None:
        assert client.get(f"{JIRA}/issue/TEAM-9999").status_code == 404

    def test_verweise_auf_confluence_stehen_im_text(self, client: TestClient) -> None:
        """§9.2: "teils mit Confluence-Verweisen im Text"."""
        beschreibung = client.get(f"{JIRA}/issue/TEAM-4").json()["fields"]["description"]

        assert "[[confluence:" in beschreibung


class TestSteuerungsApi:
    def test_szenario_aenderung(self, client: TestClient) -> None:
        vorher = client.get(f"{CONFLUENCE}/content/100001").json()["title"]

        bericht = client.post("/_control/scenario/incremental_update").json()
        nachher = client.get(f"{CONFLUENCE}/content/100001").json()

        assert bericht["confluence"]["updated"] == 1
        assert nachher["title"] != vorher
        assert nachher["version"]["when"] > "2027"

    def test_szenario_loeschung(self, client: TestClient) -> None:
        client.post("/_control/scenario/deletion")

        zustand = client.get("/_control/state").json()

        assert zustand["pages"] == 119
        assert zustand["issues"] == 79
        assert client.get(f"{CONFLUENCE}/content/deleted").json()["results"] == [{"id": "100003"}]
        assert client.get(f"{JIRA}/deleted").json()["keys"] == ["TEAM-2"]

    def test_szenario_verwaister_knoten(self, client: TestClient) -> None:
        client.post("/_control/scenario/orphan")

        assert client.get("/_control/state").json()["pages"] == 121

    def test_unbekanntes_szenario_nennt_die_verfuegbaren(self, client: TestClient) -> None:
        antwort = client.post("/_control/scenario/gibtesnicht")

        assert antwort.status_code == 404
        assert "incremental_update" in antwort.json()["detail"]

    def test_reset_stellt_den_seed_wieder_her(self, client: TestClient) -> None:
        client.post("/_control/scenario/deletion")
        client.post("/_control/latency", json={"seconds": 0.01})

        zustand = client.post("/_control/reset").json()

        assert zustand["pages"] == 120
        assert zustand["latency_seconds"] == 0.0
        assert zustand["applied_scenarios"] == []

    def test_szenarien_wirken_kumulativ(self, client: TestClient) -> None:
        client.post("/_control/scenario/deletion")
        client.post("/_control/scenario/orphan")

        assert client.get("/_control/state").json()["applied_scenarios"] == [
            "deletion",
            "orphan",
        ]

    def test_latenz_wird_uebernommen(self, client: TestClient) -> None:
        antwort = client.post("/_control/latency", json={"seconds": 0.25})

        assert antwort.json() == {"latency_seconds": 0.25}

    def test_erzwungener_fehler_ist_abgezaehlt(self, client: TestClient) -> None:
        client.post("/_control/fail", json={"status": 429, "count": 2, "retry_after": 1})

        erste = client.get(f"{CONFLUENCE}/space")
        zweite = client.get(f"{CONFLUENCE}/space")
        dritte = client.get(f"{CONFLUENCE}/space")

        assert erste.status_code == 429
        assert erste.headers["Retry-After"] == "1.0"
        assert zweite.status_code == 429
        assert dritte.status_code == 200

    def test_fehler_erst_nach_der_ersten_anfrage(self, client: TestClient) -> None:
        """Der Fall aus §22.3: Der Abbruch muss *mitten* in einer Iteration passieren können."""
        client.post("/_control/fail", json={"status": 500, "count": 5, "after_requests": 1})

        assert client.get(f"{CONFLUENCE}/space").status_code == 200
        assert client.get(f"{CONFLUENCE}/space").status_code == 500

    def test_fehler_nur_fuer_einen_pfad(self, client: TestClient) -> None:
        client.post("/_control/fail", json={"status": 500, "count": 5, "path_prefix": "/jira"})

        assert client.get(f"{CONFLUENCE}/space").status_code == 200
        assert client.get("/jira/rest/agile/1.0/board").status_code == 500

    def test_leerer_rumpf_schaltet_ab(self, client: TestClient) -> None:
        client.post("/_control/fail", json={"status": 500, "count": 5})

        assert client.post("/_control/fail", json={}).json() == {"fail": None}
        assert client.get(f"{CONFLUENCE}/space").status_code == 200

    def test_die_steuerung_selbst_faellt_nie_aus(self, client: TestClient) -> None:
        """Sonst käme man aus einem erzwungenen Fehler nicht mehr heraus."""
        client.post("/_control/fail", json={"status": 500, "count": 999})

        assert client.get("/_control/state").status_code == 200


class TestZustand:
    def test_szenariodateien_sind_gueltiges_json(self) -> None:
        for datei in sorted((quellen.FIXTURES / "scenarios").glob("*.json")):
            inhalt: Any = json.loads(datei.read_text(encoding="utf-8"))

            assert "description" in inhalt, f"{datei.name} ohne Beschreibung"

    def test_ein_szenario_auf_ein_unbekanntes_objekt_geht_ins_leere(self, tmp_path: Path) -> None:
        """Ein Szenario darf schiefgehen, ohne den Server zu beschädigen."""
        (tmp_path / "confluence" / "pages").mkdir(parents=True)
        (tmp_path / "scenarios").mkdir()
        (tmp_path / "scenarios" / "leer.json").write_text(
            json.dumps(
                {
                    "description": "x",
                    "confluence": {"update": [{"id": "999", "title": "T"}], "delete": ["888"]},
                    "jira": {"update": [{"key": "X-1", "summary": "S"}], "delete": ["Y-1"]},
                }
            ),
            encoding="utf-8",
        )
        state = MockState.from_fixtures(tmp_path)

        bericht = state.apply_scenario("leer")

        assert bericht["confluence"]["updated"] == 1
        assert state.pages == {}


class TestZweiterZugang:
    """Der Gateway-Zugang und die zweite API-Version (§9.1, §15).

    Sie sind kein Beiwerk. Ohne sie liefe in der Entwicklung genau eine der Betriebsarten
    durch — und die andere fiele erst gegen das echte System auf, wo man sie nicht mehr
    provozieren kann.
    """

    def test_dasselbe_ergebnis_ueber_beide_zugaenge(self, client: Any) -> None:
        ueber_rest = client.get("/confluence/rest/api/content/100001")
        ueber_gateway = client.get("/gateway/confluence/content/100001", headers={"x-apikey": "g"})

        assert ueber_gateway.status_code == 200
        assert ueber_gateway.json() == ueber_rest.json()

    def test_ohne_schluessel_antwortet_das_gateway_mit_401(self, client: Any) -> None:
        antwort = client.get("/gateway/confluence/space")

        assert antwort.status_code == 401

    def test_der_gewoehnliche_zugang_verlangt_keinen_schluessel(self, client: Any) -> None:
        assert client.get("/confluence/rest/api/space").status_code == 200

    @pytest.mark.parametrize("version", ["2", "3"])
    def test_beide_jira_versionen_antworten(self, client: Any, version: str) -> None:
        antwort = client.get(f"/jira/rest/api/{version}/issue/TEAM-1")

        assert antwort.status_code == 200
        assert antwort.json()["key"] == "TEAM-1"


class TestTitelsuche:
    """``/content/search`` — der einzige Schritt der Linkauflösung, der die Instanz fragt."""

    def test_space_und_titel_werden_ausgewertet(self, client: Any) -> None:
        antwort = client.get(
            "/confluence/rest/api/content/search",
            params={"cql": 'space="ENG" and title="Nächtlicher ETL-Lauf"'},
        )

        assert [seite["id"] for seite in antwort.json()["results"]] == ["100001"]

    def test_ein_unbekannter_titel_liefert_nichts(self, client: Any) -> None:
        antwort = client.get(
            "/confluence/rest/api/content/search",
            params={"cql": 'space="ENG" and title="Gibt es nicht"'},
        )

        assert antwort.json()["results"] == []

    def test_der_falsche_space_liefert_nichts(self, client: Any) -> None:
        """Sonst zeigte ein Verweis über Space-Grenzen auf die erstbeste gleichnamige Seite."""
        antwort = client.get(
            "/confluence/rest/api/content/search",
            params={"cql": 'space="ARCH" and title="Nächtlicher ETL-Lauf"'},
        )

        assert antwort.json()["results"] == []


class TestRemoteLinks:
    """Verweise von Jira nach außen — über einen eigenen Endpunkt, wie in der echten API."""

    def test_ein_vorgang_mit_remote_link(self, client: Any) -> None:
        antwort = client.get("/jira/rest/api/2/issue/TEAM-1/remotelink")

        assert antwort.status_code == 200
        assert "pageId=100001" in antwort.json()[0]["globalId"]

    def test_ein_vorgang_ohne_remote_links(self, client: Any) -> None:
        assert client.get("/jira/rest/api/2/issue/TEAM-3/remotelink").json() == []

    def test_ein_unbekannter_vorgang(self, client: Any) -> None:
        assert client.get("/jira/rest/api/2/issue/TEAM-9999/remotelink").status_code == 404
