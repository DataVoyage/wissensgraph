"""Die ``.env``-Datei gilt für **alle** drei Konfigurationsdateien (§6.2, §6.4).

Diese Datei gibt es wegen eines Fehlers, den man beim Arbeiten findet und nicht beim Lesen: Die
Kernkonfiguration bekam ihre Werte aus der ``.env``, ``config/models.yaml`` und
``config/sources.yaml`` aber nicht. Beide lösen ihre ``${WG_…}``-Platzhalter gegen ``os.environ``
auf, und dort standen die Werte der Datei nie — ``build_settings`` hatte sie nur für sich selbst
zusammengeführt.

Das Tückische daran war nicht das Fehlen, sondern der Rückfallwert. Aus
``${WG_PROVIDER_VERTEX__LOCATION:-europe-west4}`` wurde stillschweigend ``europe-west4``, obwohl
in der ``.env`` ``eu`` stand: kein Fehler, keine Meldung, nur ein anderer Ort der Verarbeitung.
Im Container fiel es nie auf, weil dort Compose alles in die echte Prozessumgebung schreibt — der
Fehler traf ausschließlich den Weg über den Host.

Geprüft wird deshalb nicht die Hilfsfunktion allein, sondern die Reichweite: Was sieht ein
Prozess, der nichts weiter tut als starten?
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from wissensgraph.config.dotenv import export_dotenv
from wissensgraph.config.loader import build_settings
from wissensgraph.config.models import load_models
from wissensgraph.config.sources import load_sources

pytestmark = pytest.mark.unit

MODELS = {
    "providers": {
        "vertex": {
            "type": "vertex",
            "project": "${WG_PROVIDER_VERTEX__PROJECT:-}",
            "location": "${WG_PROVIDER_VERTEX__LOCATION:-europe-west4}",
        }
    },
    "tasks": {
        "cluster_labeling": {"primary": {"provider": "vertex", "model": "gemini-3.5-flash-lite"}}
    },
}

SOURCES = {
    "sources": [
        {
            "name": "confluence-eng",
            "adapter": "confluence",
            "id_prefix": "confluence",
            "target": {
                "store": "shared",
                "scope": "engineering",
                "default_type": "Confluence Page",
            },
            "connection": {
                "base_url": "${WG_SOURCE_CONFLUENCE__BASE_URL:-http://mock-sources:8090/confluence}",
                "token": "${WG_SOURCE_CONFLUENCE__TOKEN:-}",
            },
            "mapping": {"title": "$.title"},
        }
    ]
}


@pytest.fixture
def saubere_umgebung() -> Iterator[None]:
    """Entfernt alle ``WG_``-Variablen und stellt sie danach wieder her.

    Nötig, weil dieser Test genau die Prozessumgebung prüft, die er sonst von außen geerbt
    bekäme — und weil er sie verändert.
    """
    gesichert = {name: wert for name, wert in os.environ.items() if name.startswith("WG_")}
    for name in gesichert:
        del os.environ[name]
    try:
        yield
    finally:
        for name in [n for n in os.environ if n.startswith("WG_")]:
            del os.environ[name]
        os.environ.update(gesichert)


@pytest.fixture
def projekt(tmp_path: Path, minimal_config_dict: dict) -> Path:
    """Ein vollständiges Projektverzeichnis: drei Config-Dateien und eine ``.env``."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "wissensgraph.yaml").write_text(yaml.safe_dump(minimal_config_dict), encoding="utf-8")
    (config / "models.yaml").write_text(yaml.safe_dump(MODELS), encoding="utf-8")
    (config / "sources.yaml").write_text(yaml.safe_dump(SOURCES), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "WG_CONFIG_DIR=./config\n"
        "WG_MODELS_FILE=./config/models.yaml\n"
        "WG_SOURCES_FILE=./config/sources.yaml\n"
        "WG_PROVIDER_VERTEX__PROJECT=aus-der-datei\n"
        "WG_PROVIDER_VERTEX__LOCATION=eu\n"
        "WG_SOURCE_CONFLUENCE__TOKEN=geheim-aus-der-datei\n",
        encoding="utf-8",
    )
    return tmp_path


class TestReichweite:
    """Was ein Prozess sieht, der nur ``build_settings`` aufruft."""

    def test_der_model_router_sieht_die_datei(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Der gemeldete Fehler.

        Ohne den Export stünde hier ``None`` — und der Anbieter wäre ohne jede Meldung unbenutzbar.
        """
        monkeypatch.chdir(projekt)

        provider = load_models(build_settings()).provider("vertex")

        assert provider.project == "aus-der-datei"

    def test_ein_ruckfallwert_verdeckt_den_fehler_nicht_mehr(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Der eigentlich gefährliche Fall: ``europe-west4`` statt ``eu`` wäre kein Fehler
        gewesen, sondern ein anderer Ort der Verarbeitung."""
        monkeypatch.chdir(projekt)

        provider = load_models(build_settings()).provider("vertex")

        assert provider.location == "eu"
        assert provider.endpoint == "aiplatform.eu.rep.googleapis.com"

    def test_die_quellen_sehen_die_datei(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dasselbe Loch traf die Quell-Tokens — ohne sie meldet sich kein Live-System an."""
        monkeypatch.chdir(projekt)

        quelle = load_sources(build_settings()).sources[0]

        assert quelle.connection.token == "geheim-aus-der-datei"

    def test_auch_fremde_variablen_kommen_an(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nicht nur ``WG_``-Variablen.

        Die SDKs der Anbieter lesen ihre eigenen Namen aus ``os.environ``:
        ``GOOGLE_APPLICATION_CREDENTIALS``, ``HTTP_PROXY``, ``SSL_CERT_FILE``. Wer sie in die
        ``.env`` schreibt, erwartet zu Recht, dass sie wirken.
        """
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        (projekt / ".env").write_text(
            "WG_CONFIG_DIR=./config\nHTTPS_PROXY=http://proxy.firma.de:3128\n", encoding="utf-8"
        )
        monkeypatch.chdir(projekt)

        build_settings()

        assert os.environ["HTTPS_PROXY"] == "http://proxy.firma.de:3128"


class TestPraezedenz:
    """§6.2: Prozess-ENV schlägt ``.env``-Datei — auch nach dem Export."""

    def test_die_prozessumgebung_behaelt_das_letzte_wort(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(projekt)
        monkeypatch.setenv("WG_PROVIDER_VERTEX__PROJECT", "aus-der-prozessumgebung")

        provider = load_models(build_settings()).provider("vertex")

        assert provider.project == "aus-der-prozessumgebung"

    def test_ein_ausdruecklich_uebergebenes_env_bleibt_folgenlos(
        self, projekt: Path, saubere_umgebung: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wer ``env=`` mitgibt, will Isolation — dann wird nichts exportiert.

        Sonst könnte ein Test die Umgebung des nächsten verändern, und die Testsammlung wäre von
        ihrer eigenen Reihenfolge abhängig.
        """
        monkeypatch.chdir(projekt)

        build_settings(env={"WG_CONFIG_DIR": "./config"})

        assert "WG_PROVIDER_VERTEX__PROJECT" not in os.environ


class TestExport:
    """Die Hilfsfunktion für sich genommen."""

    def test_setzt_was_fehlt(self) -> None:
        ziel: dict[str, str] = {}

        export_dotenv({"A": "1"}, ziel)

        assert ziel == {"A": "1"}

    def test_ueberschreibt_nichts_bestehendes(self) -> None:
        ziel = {"A": "vorhanden"}

        export_dotenv({"A": "aus-der-datei"}, ziel)

        assert ziel["A"] == "vorhanden"

    def test_ein_leeres_mapping_ist_folgenlos(self) -> None:
        ziel = {"A": "1"}

        export_dotenv({}, ziel)

        assert ziel == {"A": "1"}
