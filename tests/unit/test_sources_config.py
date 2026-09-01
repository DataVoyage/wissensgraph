"""Tests der Quellkonfiguration ``sources.yaml`` (§8.4, §6.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wissensgraph.config.errors import (
    ConfigFileError,
    ConfigValidationError,
    PlaceholderResolutionError,
)
from wissensgraph.config.schema import Settings
from wissensgraph.config.sources import SourcesConfig, load_sources, sources_file

pytestmark = pytest.mark.unit


def quelle(**overrides: Any) -> dict[str, Any]:
    """Ein gültiger Quelleintrag mit sinnvollen Vorgaben."""
    eintrag: dict[str, Any] = {
        "name": "confluence-eng",
        "adapter": "confluence",
        "id_prefix": "confluence",
        "target": {"scope": "engineering", "default_type": "Confluence Page"},
    }
    eintrag.update(overrides)
    return eintrag


@pytest.fixture
def schreibe_sources(tmp_path: Path) -> Any:
    """Schreibt ein sources.yaml und gibt seinen Pfad zurück."""

    def _write(data: dict[str, Any]) -> Path:
        pfad = tmp_path / "sources.yaml"
        pfad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return pfad

    return _write


class TestLaden:
    def test_am_standardort_ist_eine_fehlende_datei_kein_fehler(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Ein System ohne angebundene Quellen ist zulässig (Compose-Profil 'minimal')."""
        ohne = settings.model_copy(update={"config_dir": str(tmp_path)})

        config = load_sources(ohne, env={})

        assert config.sources == ()
        assert config.enabled == ()

    def test_ein_benannter_pfad_der_nicht_stimmt_ist_ein_fehler(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Sonst meldete ein Sync-Lauf ohne eine einzige Quelle einen Erfolg."""
        with pytest.raises(ConfigFileError, match="existiert nicht"):
            load_sources(settings, path=tmp_path / "gibt-es-nicht.yaml", env={})

    def test_die_beispielkonfiguration_des_repositories_laedt(self, settings: Settings) -> None:
        """Das mitgelieferte config/sources.yaml muss gegen die Kernkonfiguration passen."""
        repo = Path(__file__).resolve().parents[2]

        config = load_sources(
            settings,
            path=repo / "config" / "sources.yaml",
            env={
                "WG_SOURCE_CONFLUENCE__BASE_URL": "http://mock/confluence",
                "WG_SOURCE_JIRA__BASE_URL": "http://mock/jira",
            },
        )

        assert [item.name for item in config.sources] == [
            "confluence-eng",
            "jira-team",
            "sap-btp-doku",
        ]
        # Die SAP-Doku ist für den Kern eine gewöhnliche Confluence-Seite — das ist der Zweck
        # der Quelle (Tests an echten Texten) und zugleich die Probe auf Leitprinzip 12: Der
        # Typ kommt aus der Konfiguration, nicht aus dem Adapter. Ihr ID-Präfix ist trotzdem
        # ein eigenes; ein geteiltes hieße geteilter Nummernkreis (§7.5).
        sap = config.get("sap-btp-doku")
        assert sap.target.default_type == "Confluence Page"
        assert sap.id_prefix == "sapdoc"
        # Kein 'mapping' für die Inhaltsfelder — und das ist die eigentliche Zusage dieser
        # Zeile. Die 'mapping'-Sektion schlägt die Vorgaben des Adapters (§8.4); ein Eintrag
        # 'body: $.body.storage.value' ersetzte das fertige Markdown durch das rohe
        # Storage-Format und machte die gesamte Umwandlung wirkungslos, ohne dass etwas
        # fehlschlüge. Genau so stand es hier einmal.
        assert "body" not in config.get("confluence-eng").mapping

    def test_platzhalter_werden_aufgeloest(self, settings: Settings, schreibe_sources: Any) -> None:
        pfad = schreibe_sources(
            {"sources": [quelle(connection={"token": "${WG_SOURCE_CONFLUENCE__TOKEN}"})]}
        )

        config = load_sources(settings, path=pfad, env={"WG_SOURCE_CONFLUENCE__TOKEN": "geheim"})

        assert config.get("confluence-eng").connection.token == "geheim"

    def test_ein_offener_platzhalter_bricht_ab(
        self, settings: Settings, schreibe_sources: Any
    ) -> None:
        """§6.1 Regel 3: kein leerer String, sondern ein Fehler."""
        pfad = schreibe_sources({"sources": [quelle(connection={"token": "${WG_FEHLT}"})]})

        with pytest.raises(PlaceholderResolutionError):
            load_sources(settings, path=pfad, env={})

    def test_leerer_rueckfallwert_gilt_als_nicht_gesetzt(
        self, settings: Settings, schreibe_sources: Any
    ) -> None:
        pfad = schreibe_sources({"sources": [quelle(connection={"token": "${WG_FEHLT:-}"})]})

        config = load_sources(settings, path=pfad, env={})

        assert config.get("confluence-eng").connection.token is None

    def test_der_pfad_kommt_aus_der_umgebung(self, settings: Settings) -> None:
        """§6.4: ``WG_SOURCES_FILE`` schlägt das Config-Verzeichnis."""
        assert sources_file(settings, {"WG_SOURCES_FILE": "/anderswo/q.yaml"}) == Path(
            "/anderswo/q.yaml"
        )
        assert sources_file(settings, {}).name == "sources.yaml"


class TestQuerpruefungen:
    def test_unbekannter_scope(self, settings: Settings, schreibe_sources: Any) -> None:
        pfad = schreibe_sources(
            {"sources": [quelle(target={"scope": "marketing", "default_type": "Note"})]}
        )

        with pytest.raises(ConfigValidationError, match="unbekannten Scope 'marketing'"):
            load_sources(settings, path=pfad, env={})

    def test_unbekannter_konzepttyp(self, settings: Settings, schreibe_sources: Any) -> None:
        pfad = schreibe_sources(
            {"sources": [quelle(target={"scope": "engineering", "default_type": "Rezept"})]}
        )

        with pytest.raises(ConfigValidationError, match="unbekannten Konzepttyp 'Rezept'"):
            load_sources(settings, path=pfad, env={})

    def test_typ_im_zielstore_nicht_zugelassen(
        self, settings: Settings, schreibe_sources: Any
    ) -> None:
        """'Note' gibt es nur in 'personal', 'engineering' liegt aber in 'shared' (§7.2)."""
        pfad = schreibe_sources(
            {"sources": [quelle(target={"scope": "engineering", "default_type": "Note"})]}
        )

        with pytest.raises(ConfigValidationError, match="nicht zugelassen"):
            load_sources(settings, path=pfad, env={})

    def test_widersprechender_store_bricht_ab(
        self, settings: Settings, schreibe_sources: Any
    ) -> None:
        """§20.1: Es darf nicht zwei Wahrheiten darüber geben, wohin eine Quelle schreibt."""
        pfad = schreibe_sources(
            {
                "sources": [
                    quelle(
                        target={
                            "store": "personal",
                            "scope": "engineering",
                            "default_type": "Confluence Page",
                        }
                    )
                ]
            }
        )

        with pytest.raises(ConfigValidationError, match="Maßgeblich ist der Scope"):
            load_sources(settings, path=pfad, env={})

    def test_ein_passender_store_ist_erlaubt(
        self, settings: Settings, schreibe_sources: Any
    ) -> None:
        pfad = schreibe_sources(
            {
                "sources": [
                    quelle(
                        target={
                            "store": "shared",
                            "scope": "engineering",
                            "default_type": "Confluence Page",
                        }
                    )
                ]
            }
        )

        assert len(load_sources(settings, path=pfad, env={}).sources) == 1


class TestSchema:
    def test_doppelter_name(self) -> None:
        with pytest.raises(ValueError, match="doppelt"):
            SourcesConfig.model_validate({"sources": [quelle(), quelle()]})

    def test_doppeltes_id_praefix(self) -> None:
        """Zwei Quellen mit demselben Präfix erzeugen dieselben Konzept-IDs (§7.5)."""
        with pytest.raises(ValueError, match="beide das ID-Präfix"):
            SourcesConfig.model_validate(
                {"sources": [quelle(), quelle(name="confluence-fin")]},
            )

    def test_zwei_instanzen_desselben_adapters_sind_erlaubt(self) -> None:
        """§8.4 sieht 'confluence-eng' und 'confluence-finance' ausdrücklich vor."""
        config = SourcesConfig.model_validate(
            {
                "sources": [
                    quelle(),
                    quelle(name="confluence-fin", id_prefix="conffin"),
                ]
            }
        )

        assert len(config.sources) == 2
        assert {item.adapter for item in config.sources} == {"confluence"}

    @pytest.mark.parametrize("praefix", ["Confluence", "1source", "mit leerzeichen", "-start"])
    def test_ungueltiges_id_praefix(self, praefix: str) -> None:
        with pytest.raises(ValueError, match="ID-Präfix"):
            SourcesConfig.model_validate({"sources": [quelle(id_prefix=praefix)]})

    def test_unbekanntes_mapping_feld(self) -> None:
        with pytest.raises(ValueError, match="kennt die Felder"):
            SourcesConfig.model_validate({"sources": [quelle(mapping={"titel": "$.title"})]})

    def test_unbekannter_schluessel_bricht_ab(self) -> None:
        """extra='forbid': Ein Tippfehler in der YAML soll auffallen, nicht wirkungslos sein."""
        with pytest.raises(ValueError):
            SourcesConfig.model_validate({"sources": [quelle(enable=True)]})

    def test_class_wird_unter_seinem_yaml_namen_gelesen(self) -> None:
        """In der YAML heißt das Feld 'class' — im Python-Modell ginge das nicht (§8.3)."""
        config = SourcesConfig.model_validate(
            {"sources": [quelle(**{"class": "paket.modul:Klasse"})]}
        )

        assert config.get("confluence-eng").adapter_class == "paket.modul:Klasse"

    def test_enabled_filtert(self) -> None:
        config = SourcesConfig.model_validate(
            {"sources": [quelle(), quelle(name="aus", id_prefix="aus", enabled=False)]}
        )

        assert [item.name for item in config.enabled] == ["confluence-eng"]

    def test_unbekannte_quelle(self) -> None:
        with pytest.raises(KeyError, match="Unbekannte Quelle"):
            SourcesConfig().get("gibt-es-nicht")

    def test_vorgaben_der_verbindung(self) -> None:
        """Ohne Angaben gelten die Defaults aus config/defaults.py (§6.1 Regel 1)."""
        verbindung = SourcesConfig.model_validate({"sources": [quelle()]}).sources[0].connection

        assert verbindung.retries == 3
        assert verbindung.rate_limit_per_second == 5.0
        assert verbindung.base_url is None
