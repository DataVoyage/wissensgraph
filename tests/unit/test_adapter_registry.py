"""Tests der Adapter-Registry (§8.3, §6.5).

Der Schwerpunkt liegt auf dem vierten Abnahmekriterium der Stufe 3: "ein dritter, im Test
angelegter Dummy-Adapter wird allein über einen Config-Eintrag aktiv, ohne Kernänderung."
"""

from __future__ import annotations

import time
from importlib.metadata import EntryPoint
from typing import Any

import pytest

from support import quellen
from wissensgraph.config.sources import SourcesConfig
from wissensgraph.infrastructure.adapters import (
    AdapterNotFound,
    AdapterRegistry,
    ConfluenceAdapter,
    FixtureAdapter,
    JiraAdapter,
)
from wissensgraph.ports.sources import HealthState, HealthStatus, SourceAdapter

pytestmark = pytest.mark.unit


def dummy_quelle(**rest: Any) -> Any:
    """Die Quellkonfiguration, mit der der Dummy-Adapter über 'class:' aktiv wird."""
    return quellen.quelle(
        "dummy",
        adapter="dummy",
        id_prefix="dummy",
        **{"class": "support.dummy_adapter:DummyAdapter", **rest},
    )


class TestAbnahmeDritterAdapterUeberDieConfig:
    def test_ein_config_eintrag_genuegt(self) -> None:
        """Kein Entry Point, keine Installation, keine Zeile Kerncode (§24 Abnahme 4)."""
        adapter = AdapterRegistry().create(dummy_quelle())

        assert adapter.name == "dummy"
        assert isinstance(adapter, SourceAdapter)

    def test_der_adapter_ist_danach_konfiguriert(self) -> None:
        registriert = AdapterRegistry().register(dummy_quelle())

        assert registriert.usable
        assert registriert.health.state is HealthState.HEALTHY

    def test_er_liefert_dokumente(self) -> None:
        adapter = AdapterRegistry().create(dummy_quelle())

        assert [item.external_id for item in adapter.iter_documents(None)][:2] == ["d-1", "d-2"]

    def test_er_erscheint_in_der_uebersicht(self) -> None:
        eintrag = dummy_quelle().model_dump(by_alias=True)
        config = SourcesConfig.model_validate({"sources": [eintrag]})

        (registriert,) = AdapterRegistry().build_all(config)

        assert registriert.as_dict()["name"] == "dummy"
        assert registriert.as_dict()["capabilities"]["deletions"] is False


class TestAuffinden:
    def test_die_mitgelieferten_sind_bekannt(self) -> None:
        bekannt = AdapterRegistry().known_keys()

        assert {"confluence", "jira", "fixture-source"} <= set(bekannt)

    @pytest.mark.parametrize(
        ("schluessel", "klasse"),
        [
            ("confluence", ConfluenceAdapter),
            ("jira", JiraAdapter),
            ("fixture-source", FixtureAdapter),
        ],
    )
    def test_eingebaute_werden_gefunden(self, schluessel: str, klasse: type) -> None:
        cfg = quellen.quelle("q", adapter=schluessel, id_prefix="q")

        assert AdapterRegistry().factory_for(cfg) is klasse

    def test_class_schlaegt_den_schluessel(self) -> None:
        """Der spezifischste Weg gewinnt: 'class:' braucht den Schlüssel gar nicht zu kennen."""
        cfg = quellen.quelle(
            "q",
            adapter="confluence",
            id_prefix="q",
            **{"class": "support.dummy_adapter:DummyAdapter"},
        )

        assert AdapterRegistry().factory_for(cfg)().name == "dummy"

    def test_ein_entry_point_verdraengt_den_eingebauten(self, monkeypatch: Any) -> None:
        """§8.3: Ein installiertes Paket darf eine mitgelieferte Umsetzung ersetzen."""
        eintrag = EntryPoint(
            name="confluence", value="support.dummy_adapter:DummyAdapter", group="wg.test"
        )
        registry = AdapterRegistry()
        monkeypatch.setattr(registry, "_entry_points", lambda: iter([eintrag]))

        cfg = quellen.quelle("q", adapter="confluence", id_prefix="q")

        assert registry.factory_for(cfg)().name == "dummy"

    def test_ein_kaputter_entry_point_meldet_seinen_namen(self, monkeypatch: Any) -> None:
        eintrag = EntryPoint(name="kaputt", value="gibt.es.nicht:Klasse", group="wg.test")
        registry = AdapterRegistry()
        monkeypatch.setattr(registry, "_entry_points", lambda: iter([eintrag]))

        with pytest.raises(AdapterNotFound, match="Entry Point 'kaputt'"):
            registry.factory_for(quellen.quelle("q", adapter="kaputt", id_prefix="q"))


class TestNichtAuffindbar:
    def test_unbekannter_schluessel_ist_ein_startfehler(self) -> None:
        """§6.5, letzter Punkt — und ausdrücklich kein Zustand in einer Statusanzeige."""
        cfg = quellen.quelle("q", adapter="gibtesnicht", id_prefix="q")

        with pytest.raises(AdapterNotFound, match="nirgends auffindbar"):
            AdapterRegistry().factory_for(cfg)

    def test_die_meldung_nennt_die_bekannten_und_beide_wege(self) -> None:
        cfg = quellen.quelle("q", adapter="gibtesnicht", id_prefix="q")

        with pytest.raises(AdapterNotFound) as fehler:
            AdapterRegistry().factory_for(cfg)

        assert "confluence" in str(fehler.value)
        assert "class:" in str(fehler.value)

    @pytest.mark.parametrize(
        ("pfad", "muster"),
        [
            ("ohne_trenner", "kein Modulpfad"),
            (":Klasse", "kein Modulpfad"),
            ("modul:", "kein Modulpfad"),
            ("gibt.es.nicht:Klasse", "lässt sich nicht laden"),
            ("support.dummy_adapter:GibtEsNicht", "kennt 'GibtEsNicht' nicht"),
        ],
    )
    def test_fehlerhafte_modulpfade(self, pfad: str, muster: str) -> None:
        cfg = quellen.quelle("q", adapter="egal", id_prefix="q", **{"class": pfad})

        with pytest.raises(AdapterNotFound, match=muster):
            AdapterRegistry().factory_for(cfg)


class TestAuffindbarAberKaputt:
    """§8.3: "Ein fehlerhafter Adapter deaktiviert sich selbst … ohne den Start zu verhindern"."""

    def test_ein_scheiterndes_health_macht_die_quelle_unbenutzbar(self) -> None:
        registry = AdapterRegistry(builtins={"kaputt": _HealthWirftEineAusnahme})

        registriert = registry.register(quellen.quelle("q", adapter="kaputt", id_prefix="q"))

        assert not registriert.usable
        assert registriert.health.state is HealthState.UNHEALTHY
        assert "Netz weg" in registriert.health.detail

    def test_eine_kaputte_quelle_stoppt_die_anderen_nicht(self) -> None:
        registry = AdapterRegistry(
            builtins={"kaputt": _HealthWirftEineAusnahme, "gesund": _ImmerGesund}
        )
        config = SourcesConfig.model_validate(
            {
                "sources": [
                    quellen.quelle("a", adapter="kaputt", id_prefix="a").model_dump(by_alias=True),
                    quellen.quelle("b", adapter="gesund", id_prefix="b").model_dump(by_alias=True),
                ]
            }
        )

        gebaut = registry.build_all(config)

        assert [item.usable for item in gebaut] == [False, True]

    def test_require_nennt_den_grund(self) -> None:
        registry = AdapterRegistry(builtins={"kaputt": _HealthWirftEineAusnahme})

        registriert = registry.register(quellen.quelle("q", adapter="kaputt", id_prefix="q"))

        with pytest.raises(RuntimeError, match="Netz weg"):
            registriert.require()

    def test_require_gibt_den_gesunden_adapter_heraus(self) -> None:
        registriert = AdapterRegistry().register(dummy_quelle())

        assert registriert.require().name == "dummy"

    def test_ausgeschaltete_quellen_werden_nicht_gebaut(self) -> None:
        """'enabled: false' heißt "gibt es gerade nicht" — nicht "ist kaputt"."""
        config = SourcesConfig.model_validate(
            {"sources": [dummy_quelle(enabled=False).model_dump(by_alias=True)]}
        )

        assert AdapterRegistry().build_all(config) == ()


class _ImmerGesund:
    """Ein Adapter, der nichts kann außer gesund zu sein."""

    name = "gesund"

    def __init__(self) -> None:
        from wissensgraph.ports.sources import AdapterCapabilities

        self.capabilities = AdapterCapabilities()

    def configure(self, cfg: Any) -> None:
        pass

    def health(self) -> HealthStatus:
        return HealthStatus(state=HealthState.HEALTHY, detail="ok")


class _HealthWirftEineAusnahme(_ImmerGesund):
    """Ein Adapter, dessen Gesundheitsprüfung selbst scheitert."""

    name = "kaputt"

    def health(self) -> HealthStatus:
        raise ConnectionError("Netz weg")


class _LangsamGesund:
    """Ein Adapter, dessen ``health()`` wartet — der Fall, um den es hier geht."""

    def __init__(self) -> None:
        self._cfg: Any = None

    @property
    def name(self) -> str:
        return "langsam"

    @property
    def capabilities(self) -> Any:
        from wissensgraph.ports.sources import AdapterCapabilities

        return AdapterCapabilities()

    def configure(self, cfg: Any) -> None:
        self._cfg = cfg

    def health(self) -> HealthStatus:
        time.sleep(0.08)
        return HealthStatus(state=HealthState.HEALTHY, detail="da")


class TestQuellenGleichzeitig:
    """``sources.max_concurrency``: der Gesundheitscheck aller Quellen (§8.3).

    ``register`` ruft ``health()``, und das ist bei einer HTTP-Quelle eine Anfrage nach draußen.
    Nacheinander summierten sich die Zeitlimits, bevor der Dienst überhaupt startet — und die
    langsamste Quelle bestimmte die Startzeit aller.
    """

    @staticmethod
    def _config(anzahl: int, gleichzeitig: int) -> SourcesConfig:
        return SourcesConfig.model_validate(
            {
                "max_concurrency": gleichzeitig,
                "sources": [
                    quellen.quelle(f"q{i}", adapter="langsam", id_prefix=f"q{i}").model_dump(
                        by_alias=True
                    )
                    for i in range(anzahl)
                ],
            }
        )

    def test_prueft_die_quellen_gleichzeitig(self) -> None:
        registry = AdapterRegistry(builtins={"langsam": _LangsamGesund})

        beginn = time.monotonic()
        gebaut = registry.build_all(self._config(6, 6))
        dauer = time.monotonic() - beginn

        assert [item.usable for item in gebaut] == [True] * 6
        # Nacheinander wären es mindestens 0,48 s. Die Schranke ist großzügig — geprüft wird
        # die Größenordnung, nicht die Maschine.
        assert dauer < 0.3

    def test_haelt_die_reihenfolge_der_konfiguration(self) -> None:
        # Der Status ist eine Anzeige und kein Wettlauf: 'wg sources list' soll die Quellen in
        # derselben Reihenfolge zeigen wie sources.yaml.
        registry = AdapterRegistry(builtins={"langsam": _LangsamGesund})

        gebaut = registry.build_all(self._config(6, 6))

        assert [item.name for item in gebaut] == [f"q{i}" for i in range(6)]

    def test_bleibt_ohne_einstellung_nacheinander(self) -> None:
        # Vorgabe 1: Wer nichts konfiguriert, bekommt den bisherigen Ablauf.
        assert SourcesConfig().max_concurrency == 1
