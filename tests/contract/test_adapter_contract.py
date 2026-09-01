"""Die Contract-Suite gegen alle vier Adapter (§22.3, §24 Abnahme Stufe 3).

Vier Klassen, kein einziger eigener Test. Das ist der Punkt: Die Zusicherungen stehen genau
einmal, im Kern, in :mod:`wissensgraph.testing.adapter_contract` — und jede neue Quelle erbt sie,
statt sie abzuschreiben (§8.6 Schritt 2).

Confluence und Jira laufen dabei gegen den echten Mock-Server, nur ohne Socket. Damit prüfen
diese Tests wirklich Paginierung, 429-Behandlung und Abbruchverhalten und nicht bloß eine
Attrappe des Adapters.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from support import quellen
from support.dummy_adapter import DummyAdapter
from wissensgraph.infrastructure.adapters import ConfluenceAdapter, FixtureAdapter, JiraAdapter
from wissensgraph.infrastructure.adapters.sap_docs import SapDocsAdapter
from wissensgraph.ports.sources import SourceAdapter
from wissensgraph.testing import AdapterContractTests

pytestmark = pytest.mark.contract


def nicht_warten(_seconds: float) -> None:
    """Ersatz für den Backoff. Geprüft wird, *dass* der Adapter wartet und weitermacht.

    Wie lange er wartet, ist eine Frage der Konfiguration und keine des Kontrakts — und ein Test,
    der die Wartezeit wirklich absitzt, prüft am Ende die Uhr.
    """


DOKUMENTE: list[dict[str, Any]] = [
    {
        "external_id": "f-1",
        "title": "Erstes Fixture-Dokument",
        "body": "Ein Text mit einem Verweis auf [[fix:f-2]].",
        "tags": ["fixture"],
        "updated_at": "2026-01-01T09:00:00+00:00",
        "references": ["f-2"],
    },
    {
        "external_id": "f-2",
        "title": "Zweites Fixture-Dokument",
        "body": "Ein Text ohne Verweise.",
        "updated_at": "2026-01-02T09:00:00+00:00",
    },
    {
        "external_id": "f-3",
        "title": "Drittes Fixture-Dokument",
        "body": "Noch ein Text.",
        "updated_at": "2026-01-03T09:00:00+00:00",
    },
]


def fixture_quelle(dokumente: list[dict[str, Any]]) -> Any:
    """Eine Fixture-Quelle mit den übergebenen Dokumenten."""
    return quellen.quelle(
        "fixture",
        adapter="fixture-source",
        id_prefix="fix",
        selection={"documents": dokumente, "deleted": ["f-9"]},
    )


class MockGesteuert(AdapterContractTests):
    """Gemeinsame Steuerhaken für die beiden Adapter am Mock-Server (§9.3).

    Die Anwendung steht auf der Testinstanz und nicht auf dem Adapter: Ein Adapter, dem man für
    den Test ein Attribut anhängt, ist nicht mehr der Adapter, der im Betrieb läuft.
    """

    #: Die ID, die das Szenario ``incremental_update`` in diesem System ändert.
    geaenderte_id: str = ""

    app: Any = None

    def _steuerung(self) -> TestClient:
        return quellen.control(self.app)

    def aendern(self, adapter: SourceAdapter) -> str | None:
        antwort = self._steuerung().post("/_control/scenario/incremental_update")
        assert antwort.status_code == 200, antwort.text
        return self.geaenderte_id

    def rate_limit_erzwingen(self, adapter: SourceAdapter) -> bool:
        antwort = self._steuerung().post(
            "/_control/fail", json={"status": 429, "count": 1, "retry_after": 0}
        )
        return antwort.status_code == 200

    def ausfall_erzwingen(self, adapter: SourceAdapter) -> bool:
        # 'after_requests: 1' ist das Entscheidende: Die erste Anfrage geht durch, erst die
        # zweite scheitert. Der Abbruch passiert damit *mitten* in der Iteration (§22.3).
        antwort = self._steuerung().post(
            "/_control/fail", json={"status": 500, "count": 9999, "after_requests": 1}
        )
        return antwort.status_code == 200

    def aufraeumen(self, adapter: SourceAdapter) -> None:
        self._steuerung().post("/_control/fail", json={})


class TestConfluenceAdapter(MockGesteuert):
    """Der Confluence-Adapter gegen den Mock-Server."""

    geaenderte_id = "100001"

    @pytest.fixture
    def adapter(self) -> SourceAdapter:
        self.app = quellen.mock_app()
        cfg = quellen.quelle(
            "confluence-eng",
            adapter="confluence",
            id_prefix="confluence",
            base_url=quellen.CONFLUENCE_BASE,
            selection={"spaces": ["ENG", "ARCH"], "exclude_labels": ["archiv"]},
            mapping={
                "title": "$.title",
                "description": "$.excerpt",
                "body": "$.body.storage.value",
                "resource": "$.links.webui",
                "tags": "$.metadata.labels[*].name",
            },
        )
        gebaut = ConfluenceAdapter(
            client_factory=quellen.client_factory(self.app), sleep=nicht_warten
        )
        gebaut.configure(cfg)
        return gebaut


class TestJiraAdapter(MockGesteuert):
    """Der Jira-Adapter gegen denselben Mock-Server."""

    geaenderte_id = "TEAM-1"

    @pytest.fixture
    def adapter(self) -> SourceAdapter:
        self.app = quellen.mock_app()
        cfg = quellen.quelle(
            "jira-team",
            adapter="jira",
            id_prefix="jira",
            base_url=quellen.JIRA_BASE,
            default_type="Jira Issue",
            selection={"jql_filter": "project = TEAM"},
            mapping={
                "title": "$.fields.summary",
                "body": "$.fields.description",
                "resource": "$.self",
                "tags": "$.fields.labels[*]",
            },
        )
        gebaut = JiraAdapter(client_factory=quellen.client_factory(self.app), sleep=nicht_warten)
        gebaut.configure(cfg)
        return gebaut


class TestFixtureAdapter(AdapterContractTests):
    """Der Fixture-Adapter — eine Ebene unter dem Mock-Server (§9.1)."""

    @pytest.fixture
    def adapter(self) -> SourceAdapter:
        gebaut = FixtureAdapter()
        gebaut.configure(fixture_quelle(DOKUMENTE))
        return gebaut

    def aendern(self, adapter: SourceAdapter) -> str | None:
        """Konfiguriert neu — für eine Quelle ohne Server ist das ihre Änderung."""
        geaendert = DOKUMENTE[1] | {"updated_at": "2030-01-01T00:00:00+00:00"}
        adapter.configure(fixture_quelle([DOKUMENTE[0], geaendert, DOKUMENTE[2]]))
        return str(geaendert["external_id"])


class TestSapDocsAdapter(AdapterContractTests):
    """Der SAP-docs-Adapter gegen einen kleinen, echten Bestand im Dateisystem.

    Die Dateien werden im Test angelegt und sehen aus wie die echten: Kennungs-Kommentar in der
    ersten Zeile, Überschrift, relative Verweise über Ordnergrenzen. Ein Bestand aus dem
    Netz wäre hier fehl am Platz — geprüft wird der Kontrakt, nicht GitHub.
    """

    @pytest.fixture
    def adapter(self, tmp_path: Any) -> SourceAdapter:
        self.wurzel = tmp_path / "docs"
        (self.wurzel / "10-concepts").mkdir(parents=True)
        (self.wurzel / "30-development").mkdir(parents=True)
        _schreiben(
            self.wurzel / "10-concepts" / "account-model-8ed4a70.md",
            "8ed4a705efa0431b910056c0acdbf377",
            "Account Model",
            "Ein Text mit Verweis auf [Regionen](regions-2f3b1c4.md) und quer auf "
            "[ABAP](../30-development/abap-development-fa5af4e.md).",
        )
        _schreiben(
            self.wurzel / "10-concepts" / "regions-2f3b1c4.md",
            "2f3b1c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "Regions",
            "Ein Text ohne Verweise.",
        )
        _schreiben(
            self.wurzel / "30-development" / "abap-development-fa5af4e.md",
            "fa5af4ecdf90496b8eec54fe0e22150c",
            "ABAP Development",
            "Noch ein Text.",
        )
        gebaut = SapDocsAdapter()
        gebaut.configure(_sapdocs_quelle(self.wurzel))
        return gebaut

    def aendern(self, adapter: SourceAdapter) -> str | None:
        """Eine Datei anfassen — für eine Quelle im Dateisystem ist das ihre Änderung."""
        datei = self.wurzel / "10-concepts" / "regions-2f3b1c4.md"
        datei.touch()
        adapter.configure(_sapdocs_quelle(self.wurzel))
        return "2f3b1c4d5e6f7a8b9c0d1e2f3a4b5c6d"


def _schreiben(pfad: Any, kennung: str, titel: str, text: str) -> None:
    """Legt eine Datei im echten SAP-docs-Format an."""
    pfad.write_text(f"<!-- loio{kennung} -->\n\n# {titel}\n\n\n\n{text}\n", encoding="utf-8")


def _sapdocs_quelle(wurzel: Any) -> Any:
    """Eine SAP-docs-Quelle auf das angegebene Verzeichnis."""
    return quellen.quelle(
        "sap-btp-doku",
        adapter="sap-docs",
        id_prefix="confluence",
        default_type="Confluence Page",
        base_url="https://github.com/SAP-docs/btp-cloud-platform/blob/main",
        selection={"directory": str(wurzel)},
    )


class TestDummyAdapter(AdapterContractTests):
    """Der im Test angelegte Adapter, der von keiner Kernklasse erbt (§24 Abnahme 4)."""

    @pytest.fixture
    def adapter(self) -> SourceAdapter:
        gebaut = DummyAdapter()
        gebaut.configure(quellen.quelle("dummy", adapter="dummy", id_prefix="dummy"))
        return gebaut
