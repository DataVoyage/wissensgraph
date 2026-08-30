"""Tests des Konfigurationsschemas und seiner Validierungsregeln (§6.5, §20.3)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from wissensgraph.config.schema import (
    ApiConfig,
    ClusteringConfig,
    EdgeKindsConfig,
    LoggingConfig,
    OrphansConfig,
    RankingConfig,
    Settings,
    StoreConfig,
    TraversalConfig,
)

pytestmark = pytest.mark.unit


class TestStoreConfig:
    def test_personal_store_mit_lokalem_dsn_ist_gueltig(self) -> None:
        store = StoreConfig(dsn="postgresql://wg@db-personal:5432/wg", allow_remote=False)

        assert store.allow_remote is False

    def test_personal_store_mit_entferntem_dsn_wird_abgelehnt(self) -> None:
        # §6.5: "stores.personal.allow_remote = false, der DSN aber nicht auf localhost, eine
        # private Adresse oder den bekannten Compose-Service zeigt" -> Startfehler.
        with pytest.raises(ValidationError, match="allow_remote"):
            StoreConfig(dsn="postgresql://wg@db.example.com:5432/wg", allow_remote=False)

    def test_shared_store_darf_entfernt_liegen(self) -> None:
        store = StoreConfig(dsn="postgresql://wg@db.example.com:5432/wg", allow_remote=True)

        assert store.allow_remote is True

    def test_ist_unveraenderlich(self) -> None:
        store = StoreConfig(dsn="postgresql://wg@localhost:5432/wg", allow_remote=True)

        with pytest.raises(ValidationError):
            store.dsn = "postgresql://andere"  # type: ignore[misc]

    def test_unbekanntes_feld_wird_abgelehnt(self) -> None:
        # Ein Tippfehler in einer YAML-Datei soll den Start abbrechen, nicht ignoriert werden.
        with pytest.raises(ValidationError):
            StoreConfig(dsn="postgresql://localhost/wg", allow_remot=True)  # type: ignore[call-arg]


class TestEdgeKindsConfig:
    def test_all_kinds_vereinigt_beide_gruppen(self) -> None:
        kinds = EdgeKindsConfig(structural=["member"], semantic=["depends_on"])

        assert kinds.all_kinds == ("member", "depends_on")

    def test_ueberschneidung_wird_abgelehnt(self) -> None:
        # Die Unterscheidung steuert Traversierung und die Definition eines losen Knotens (§7.7);
        # eine Kantenart in beiden Gruppen macht beides mehrdeutig.
        with pytest.raises(ValidationError, match="zugleich strukturell und semantisch"):
            EdgeKindsConfig(structural=["member", "related"], semantic=["related"])


class TestClusteringConfig:
    def test_defaults_entsprechen_dem_dokument(self) -> None:
        config = ClusteringConfig()

        assert config.neighbors_k == 8
        assert config.min_cluster_size == 3
        assert config.max_cluster_size == 25
        assert config.stability_runs == 2

    def test_min_groesser_als_max_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="min_cluster_size"):
            ClusteringConfig(min_cluster_size=30, max_cluster_size=25)


class TestOrphansConfig:
    def test_kandidatenband_ueber_auto_commit_wird_abgelehnt(self) -> None:
        # Ohne Band zwischen beiden Schwellen gäbe es keine Kandidaten für Stufe 2 (§15.2).
        with pytest.raises(ValidationError, match="proximity_candidate_band"):
            OrphansConfig(proximity_candidate_band=0.9, proximity_auto_commit=0.85)

    @pytest.mark.parametrize("wert", [-0.1, 1.1])
    def test_schwelle_ausserhalb_null_bis_eins_wird_abgelehnt(self, wert: float) -> None:
        with pytest.raises(ValidationError):
            OrphansConfig(min_confidence=wert)


class TestTraversalConfig:
    def test_default_hops_ueber_max_hops_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="default_hops"):
            TraversalConfig(default_hops=6, max_hops=5)

    def test_ranking_ohne_gewicht_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="Ranking-Gewicht"):
            RankingConfig(hop_weight=0.0, density_weight=0.0, recency_weight=0.0)


class TestLoggingConfig:
    def test_unbekanntes_level_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="Log-Level"):
            LoggingConfig(level="LAUT")

    def test_level_ist_case_insensitiv(self) -> None:
        assert LoggingConfig(level="debug").level == "debug"


class TestApiConfig:
    def test_token_modus_ohne_token_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="WG_API_TOKEN"):
            ApiConfig(auth_mode="token", token=None)

    def test_leeres_token_gilt_als_fehlend(self) -> None:
        # ${WG_API_TOKEN:-} löst zu einem leeren String auf; der darf nicht als gültig durchgehen.
        with pytest.raises(ValidationError, match="WG_API_TOKEN"):
            ApiConfig(auth_mode="token", token="")

    def test_auth_none_nur_an_loopback(self) -> None:
        # §20.3: 'none' ist nur bei Bindung an 127.0.0.1 erlaubt; der Start bricht sonst ab.
        with pytest.raises(ValidationError, match="Loopback"):
            ApiConfig(auth_mode="none", host="0.0.0.0")

    def test_auth_none_an_loopback_ist_gueltig(self) -> None:
        config = ApiConfig(auth_mode="none", host="127.0.0.1")

        assert config.auth_mode == "none"

    def test_cors_wildcard_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError, match="Wildcard"):
            ApiConfig(auth_mode="none", host="127.0.0.1", cors_origins=["*"])

    def test_zerlegt_kommaseparierte_origins(self) -> None:
        config = ApiConfig(auth_mode="none", host="127.0.0.1", cors_origins="http://a, http://b")

        assert config.cors_origins == ("http://a", "http://b")

    def test_zerlegt_kommaseparierte_origins_in_liste(self) -> None:
        config = ApiConfig(
            auth_mode="none", host="127.0.0.1", cors_origins=["http://a,http://b", "http://c"]
        )

        assert config.cors_origins == ("http://a", "http://b", "http://c")

    def test_unbekannter_auth_modus_wird_abgelehnt(self) -> None:
        with pytest.raises(ValidationError):
            ApiConfig(auth_mode="basic")


class TestSettings:
    def test_gueltige_minimalkonfiguration(self, minimal_config_dict: dict[str, Any]) -> None:
        settings = Settings.model_validate(minimal_config_dict)

        assert settings.env == "dev"
        assert set(settings.stores) == {"shared", "personal"}
        assert settings.embedding_dim == 768

    def test_scope_auf_unbekannten_store_wird_abgelehnt(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        # §6.5: "ein in scopes referenzierter Store nicht existiert" -> Startfehler.
        minimal_config_dict["scopes"].append({"name": "irgendwas", "store": "gibtsnicht"})

        with pytest.raises(ValidationError, match="unbekannten Store"):
            Settings.model_validate(minimal_config_dict)

    def test_konzepttyp_auf_unbekannten_store_wird_abgelehnt(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        minimal_config_dict["concept_types"].append({"name": "Neu", "stores": ["archiv"]})

        with pytest.raises(ValidationError, match="unbekannte Stores"):
            Settings.model_validate(minimal_config_dict)

    def test_doppelter_scope_name_wird_abgelehnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["scopes"].append({"name": "engineering", "store": "shared"})

        with pytest.raises(ValidationError, match="eindeutig"):
            Settings.model_validate(minimal_config_dict)

    def test_doppelter_konzepttyp_wird_abgelehnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["concept_types"].append({"name": "Note", "stores": ["personal"]})

        with pytest.raises(ValidationError, match="eindeutig"):
            Settings.model_validate(minimal_config_dict)

    def test_fehlende_embedding_dim_wird_abgelehnt(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        del minimal_config_dict["embedding_dim"]

        with pytest.raises(ValidationError, match="embedding_dim"):
            Settings.model_validate(minimal_config_dict)

    def test_leere_scopes_werden_abgelehnt(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["scopes"] = []

        with pytest.raises(ValidationError):
            Settings.model_validate(minimal_config_dict)

    def test_leerer_broker_url_wird_zu_none(self, minimal_config_dict: dict[str, Any]) -> None:
        minimal_config_dict["broker_url"] = ""

        assert Settings.model_validate(minimal_config_dict).broker_url is None

    def test_ist_unveraenderlich(self, minimal_config_dict: dict[str, Any]) -> None:
        # §6.1 Regel 4: einmal validiert, danach unveränderlich.
        settings = Settings.model_validate(minimal_config_dict)

        with pytest.raises(ValidationError):
            settings.embedding_dim = 1536  # type: ignore[misc]


class TestSettingsHilfsmethoden:
    @pytest.fixture
    def settings(self, minimal_config_dict: dict[str, Any]) -> Settings:
        return Settings.model_validate(minimal_config_dict)

    def test_scopes_for_store(self, settings: Settings) -> None:
        namen = [scope.name for scope in settings.scopes_for_store("shared")]

        assert namen == ["engineering"]

    def test_store_of_scope(self, settings: Settings) -> None:
        assert settings.store_of_scope("personal") == "personal"

    def test_store_of_scope_unbekannt_wirft(self, settings: Settings) -> None:
        with pytest.raises(KeyError, match="Unbekannter Scope"):
            settings.store_of_scope("gibtsnicht")

    def test_concept_type(self, settings: Settings) -> None:
        assert settings.concept_type("Confluence Page").source_mirrored is True

    def test_concept_type_unbekannt_wirft(self, settings: Settings) -> None:
        with pytest.raises(KeyError, match="Unbekannter Konzepttyp"):
            settings.concept_type("Gibtsnicht")
