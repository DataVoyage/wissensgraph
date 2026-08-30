"""Tests des gemeinsamen Adapter-Unterbaus: Drosselung, Wiederholung, Cursor (§8.2).

Die Contract-Suite prüft, *dass* ein Adapter ein Rate-Limit übersteht. Hier geht es um das Wie:
welche Antworten einen erneuten Versuch rechtfertigen, welche nicht, und woher die Wartezeit
kommt. Diese Unterscheidungen entscheiden darüber, ob ein fehlkonfigurierter Lauf eine Quelle
in eine Sperre treibt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from support import quellen
from wissensgraph.config import defaults
from wissensgraph.infrastructure.adapters.base import (
    CURSOR_UPDATED_AFTER,
    BaseAdapter,
    HttpSourceAdapter,
    _default_client,
)
from wissensgraph.infrastructure.adapters.fixture import FixtureAdapter
from wissensgraph.ports.sources import (
    Cursor,
    HealthState,
    NotSupported,
    SourceError,
    SourceObjectNotFound,
    SourceUnavailable,
)

pytestmark = pytest.mark.unit


class Antwortfolge:
    """Ein Transport, der eine vorgegebene Folge von Antworten liefert."""

    def __init__(self, *antworten: httpx.Response | Exception) -> None:
        self.antworten = list(antworten)
        self.aufrufe = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.aufrufe += 1
        naechste = self.antworten[min(self.aufrufe - 1, len(self.antworten) - 1)]
        if isinstance(naechste, Exception):
            raise naechste
        return naechste


def json_antwort(status: int, inhalt: Any = None, **kopfzeilen: str) -> httpx.Response:
    return httpx.Response(status, json=inhalt if inhalt is not None else {}, headers=kopfzeilen)


class Probe(HttpSourceAdapter):
    """Ein minimaler HTTP-Adapter, um den Unterbau isoliert zu prüfen."""

    name = "probe"

    def health_path(self) -> str:
        return "/ping"


def probe(folge: Antwortfolge, **verbindung: Any) -> tuple[Probe, list[float]]:
    """Ein Probe-Adapter mit aufgezeichneten Wartezeiten statt echter Pausen."""
    gewartet: list[float] = []
    adapter = Probe(
        client_factory=lambda cfg: httpx.Client(
            transport=httpx.MockTransport(folge), base_url=cfg.connection.base_url or "http://q"
        ),
        sleep=gewartet.append,
    )
    adapter.configure(
        quellen.quelle(
            "probe",
            adapter="probe",
            id_prefix="probe",
            base_url="http://quelle.invalid",
            connection=verbindung,
        )
    )
    return adapter, gewartet


class TestWiederholung:
    def test_ein_rate_limit_wird_wiederholt(self) -> None:
        """§22.3: "Rate-Limit-Antworten (429) führen zu Backoff, nicht zum Abbruch"."""
        folge = Antwortfolge(json_antwort(429), json_antwort(200, {"ok": True}))
        adapter, gewartet = probe(folge, retries=2, rate_limit_per_second=0)

        assert adapter.get("/x") == {"ok": True}
        assert folge.aufrufe == 2
        assert gewartet == [defaults.SOURCE_BACKOFF_INITIAL_SECONDS]

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_serverfehler_werden_wiederholt(self, status: int) -> None:
        folge = Antwortfolge(json_antwort(status), json_antwort(200, {"ok": True}))
        adapter, _ = probe(folge, retries=2, rate_limit_per_second=0)

        assert adapter.get("/x") == {"ok": True}

    def test_ein_verbindungsfehler_wird_wiederholt(self) -> None:
        folge = Antwortfolge(
            httpx.ConnectError("weg"), httpx.ReadTimeout("zu spät"), json_antwort(200, {"ok": True})
        )
        adapter, gewartet = probe(folge, retries=3, rate_limit_per_second=0)

        assert adapter.get("/x") == {"ok": True}
        assert len(gewartet) == 2

    def test_der_backoff_verdoppelt_sich(self) -> None:
        folge = Antwortfolge(json_antwort(503))
        adapter, gewartet = probe(folge, retries=3, rate_limit_per_second=0)

        with pytest.raises(SourceUnavailable):
            adapter.get("/x")

        assert gewartet == [0.5, 1.0, 2.0, 4.0]

    def test_retry_after_schlaegt_den_backoff(self) -> None:
        """Ein Server, der eine Wartezeit vorgibt, weiß es besser als eine Formel."""
        folge = Antwortfolge(json_antwort(429, **{"Retry-After": "7"}), json_antwort(200))
        adapter, gewartet = probe(folge, retries=2, rate_limit_per_second=0)

        adapter.get("/x")

        assert gewartet == [7.0]

    def test_ein_retry_after_als_datum_wird_ignoriert(self) -> None:
        """Die Kopfzeile darf laut RFC ein Datum tragen; der berechnete Backoff ist die Vorgabe."""
        kopf = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        folge = Antwortfolge(json_antwort(429, **kopf), json_antwort(200))
        adapter, gewartet = probe(folge, retries=2, rate_limit_per_second=0)

        adapter.get("/x")

        assert gewartet == [defaults.SOURCE_BACKOFF_INITIAL_SECONDS]

    def test_nach_allen_versuchen_kommt_source_unavailable(self) -> None:
        folge = Antwortfolge(json_antwort(429))
        adapter, _ = probe(folge, retries=1, rate_limit_per_second=0)

        with pytest.raises(SourceUnavailable, match="nach 2 Versuchen"):
            adapter.get("/x")

        assert folge.aufrufe == 2

    def test_ohne_retries_wird_genau_einmal_versucht(self) -> None:
        folge = Antwortfolge(json_antwort(503))
        adapter, _ = probe(folge, retries=0, rate_limit_per_second=0)

        with pytest.raises(SourceUnavailable):
            adapter.get("/x")

        assert folge.aufrufe == 1


class TestKeineWiederholung:
    def test_ein_dauerhafter_fehler_wird_nicht_wiederholt(self) -> None:
        """Eine falsche Anfrage ist beim zweiten Mal genauso falsch."""
        folge = Antwortfolge(json_antwort(401))
        adapter, _ = probe(folge, retries=3, rate_limit_per_second=0)

        with pytest.raises(SourceError, match="HTTP 401"):
            adapter.get("/x")

        assert folge.aufrufe == 1

    def test_404_ist_ein_eigener_fall(self) -> None:
        """Ein nicht (mehr) vorhandenes Objekt ist für den Aufrufer kein Fehler."""
        folge = Antwortfolge(json_antwort(404))
        adapter, _ = probe(folge, rate_limit_per_second=0)

        with pytest.raises(SourceObjectNotFound):
            adapter.get("/x")

    def test_eine_antwort_ohne_json(self) -> None:
        folge = Antwortfolge(httpx.Response(200, text="<html>kein JSON</html>"))
        adapter, _ = probe(folge, rate_limit_per_second=0)

        with pytest.raises(SourceError, match="kein JSON"):
            adapter.get("/x")


class TestDrosselung:
    def test_die_rate_bestimmt_den_mindestabstand(self) -> None:
        folge = Antwortfolge(json_antwort(200))
        adapter, gewartet = probe(folge, rate_limit_per_second=2)

        adapter.get("/a")
        adapter.get("/b")

        assert len(gewartet) == 1
        assert 0 < gewartet[0] <= 0.5

    def test_rate_null_schaltet_die_drosselung_ab(self) -> None:
        folge = Antwortfolge(json_antwort(200))
        adapter, gewartet = probe(folge, rate_limit_per_second=0)

        adapter.get("/a")
        adapter.get("/b")

        assert gewartet == []


class TestVerbindung:
    def test_ohne_configure_ist_der_zugriff_ein_programmfehler(self) -> None:
        with pytest.raises(SourceError, match="configure"):
            _ = Probe().config

    def test_die_standardfabrik_setzt_das_token_als_bearer(self) -> None:
        cfg = quellen.quelle(
            "q",
            adapter="probe",
            id_prefix="q",
            base_url="http://quelle.invalid",
            connection={"token": "geheim", "timeout_seconds": 5},
        )

        with _default_client(cfg) as client:
            assert client.headers["Authorization"] == "Bearer geheim"
            assert str(client.base_url) == "http://quelle.invalid"

    def test_ohne_token_gibt_es_keine_leere_kopfzeile(self) -> None:
        cfg = quellen.quelle("q", adapter="probe", id_prefix="q", base_url="http://quelle.invalid")

        with _default_client(cfg) as client:
            assert "Authorization" not in client.headers

    def test_ohne_base_url_bricht_die_fabrik_ab(self) -> None:
        cfg = quellen.quelle("q", adapter="probe", id_prefix="q")

        with pytest.raises(SourceError, match="keine base_url"):
            _default_client(cfg)

    def test_close_gibt_den_client_frei(self) -> None:
        folge = Antwortfolge(json_antwort(200))
        adapter, _ = probe(folge, rate_limit_per_second=0)
        adapter.get("/x")

        adapter.close()
        adapter.close()

        assert adapter._client is None

    def test_health_meldet_einen_ausfall_als_zustand(self) -> None:
        """§8.3: Ein fehlerhafter Adapter deaktiviert sich, statt den Start zu verhindern."""
        folge = Antwortfolge(json_antwort(500))
        adapter, _ = probe(folge, retries=0, rate_limit_per_second=0)

        zustand = adapter.health()

        assert zustand.state is HealthState.UNHEALTHY
        assert not zustand.usable

    def test_health_meldet_erreichbarkeit(self) -> None:
        folge = Antwortfolge(json_antwort(200))
        adapter, _ = probe(folge, rate_limit_per_second=0)

        assert adapter.health().state is HealthState.HEALTHY

    def test_ohne_health_path_ist_der_adapter_unvollstaendig(self) -> None:
        with pytest.raises(NotImplementedError):
            HttpSourceAdapter().health_path()


class TestCursor:
    @pytest.mark.parametrize(
        "cursor",
        [None, Cursor(), Cursor(value={"anderes": 1}), Cursor(value={CURSOR_UPDATED_AFTER: 42})],
    )
    def test_ein_unbrauchbarer_cursor_gilt_als_keiner(self, cursor: Cursor | None) -> None:
        """Der schlimmste Fall ist ein Vollabgleich — teuer, aber korrekt."""
        assert BaseAdapter.cursor_since(cursor) is None

    def test_ein_unlesbarer_zeitpunkt_gilt_als_keiner(self) -> None:
        assert BaseAdapter.cursor_since(Cursor(value={CURSOR_UPDATED_AFTER: "kein Datum"})) is None

    def test_ein_gueltiger_cursor_wird_gelesen(self) -> None:
        cursor = Cursor(value={CURSOR_UPDATED_AFTER: "2026-05-01T10:00:00+00:00"})

        gelesen = BaseAdapter.cursor_since(cursor)

        assert gelesen is not None
        assert gelesen.year == 2026


class TestFaehigkeitsflags:
    def test_ohne_deletions_wirft_list_deleted(self) -> None:
        with pytest.raises(NotSupported, match=r"capabilities\.deletions"):
            list(BaseAdapter().list_deleted(None))

    def test_ohne_single_fetch_wirft_fetch(self) -> None:
        with pytest.raises(NotSupported, match=r"capabilities\.single_fetch"):
            BaseAdapter().fetch("x")

    def test_close_ist_ohne_ressourcen_eine_leere_zusage(self) -> None:
        assert BaseAdapter().close() is None


class TestFixtureAdapterAusDateien:
    def test_dokumente_aus_einem_verzeichnis(self, tmp_path: Path) -> None:
        (tmp_path / "eins.json").write_text(
            json.dumps({"external_id": "a", "title": "A"}), encoding="utf-8"
        )
        (tmp_path / "zwei.json").write_text(
            json.dumps([{"external_id": "b"}, {"external_id": "c"}]), encoding="utf-8"
        )
        adapter = FixtureAdapter()

        adapter.configure(
            quellen.quelle(
                "fix",
                adapter="fixture-source",
                id_prefix="fix",
                selection={"directory": str(tmp_path)},
            )
        )

        assert [item.external_id for item in adapter.iter_documents(None)] == ["a", "b", "c"]

    def test_ein_fehlendes_verzeichnis_bricht_ab(self, tmp_path: Path) -> None:
        adapter = FixtureAdapter()

        with pytest.raises(SourceError, match="gibt es nicht"):
            adapter.configure(
                quellen.quelle(
                    "fix",
                    adapter="fixture-source",
                    id_prefix="fix",
                    selection={"directory": str(tmp_path / "weg")},
                )
            )

    def test_ein_eintrag_der_kein_objekt_ist(self) -> None:
        adapter = FixtureAdapter()

        with pytest.raises(SourceError, match="statt eines Objekts"):
            adapter.configure(
                quellen.quelle(
                    "fix",
                    adapter="fixture-source",
                    id_prefix="fix",
                    selection={"documents": ["kein Objekt"]},
                )
            )

    def test_ohne_dokumente_ist_der_adapter_eingeschraenkt(self) -> None:
        adapter = FixtureAdapter()
        adapter.configure(quellen.quelle("fix", adapter="fixture-source", id_prefix="fix"))

        assert adapter.health().state is HealthState.DEGRADED
        assert adapter.health().usable

    def test_ein_mapping_lenkt_die_felder_um(self) -> None:
        adapter = FixtureAdapter()
        adapter.configure(
            quellen.quelle(
                "fix",
                adapter="fixture-source",
                id_prefix="fix",
                selection={"documents": [{"external_id": "a", "roh": {"t": "Titel"}}]},
                mapping={"title": "$.roh.t"},
            )
        )

        (document,) = adapter.iter_documents(None)

        assert document.title == "Titel"
