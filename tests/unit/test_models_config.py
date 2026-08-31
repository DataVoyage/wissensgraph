"""Schema und Laden von ``models.yaml`` (§11.4, §6.5, §11.7).

Der Schwerpunkt liegt auf dem, was §6.5 als **Startfehler** verlangt. Jede dieser Prüfungen
existiert, weil ihr Fehlen den Fehler in den ersten großen Lauf verschoben hätte — also in die
Nacht, in der niemand hinsieht.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.models import (
    ModelsConfig,
    UnknownProviderError,
    UnknownTaskError,
    load_models,
    models_file,
)
from wissensgraph.config.schema import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def models_dict() -> dict[str, Any]:
    """Eine gültige Router-Konfiguration mit einem externen und einem lokalen Anbieter."""
    return {
        "providers": {
            "gemini": {"type": "google_genai", "api_key": "geheim", "local": False},
            "ollama": {
                "type": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "local": True,
            },
        },
        "tasks": {
            "embedding": {
                "primary": {"provider": "gemini", "model": "gemini-embedding-2", "dim": 768}
            },
            "relation_extraction": {
                "primary": {
                    "provider": "gemini",
                    "model": "gemini-3.5-flash-lite",
                    "temperature": 0.0,
                    "json_mode": True,
                }
            },
        },
        "policies": {
            "personal": {"allowed_providers": ["ollama"]},
            "shared": {"allowed_providers": ["gemini", "ollama"]},
        },
    }


@pytest.fixture
def write_models(tmp_path: Path) -> Any:
    """Schreibt eine Router-Konfiguration als YAML und gibt den Pfad zurück."""

    def _write(data: dict[str, Any], name: str = "models.yaml") -> Path:
        pfad = tmp_path / name
        pfad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return pfad

    return _write


class TestSchema:
    def test_eine_route_kennt_ihren_modellschluessel(self, models_dict: dict[str, Any]) -> None:
        config = ModelsConfig.model_validate(models_dict)

        assert config.task("embedding").primary.model_key == "gemini:gemini-embedding-2"

    def test_unbekannter_provider_in_einem_task_ist_ein_fehler(
        self, models_dict: dict[str, Any]
    ) -> None:
        """§6.5: 'ein Task-Profil im Router verweist auf einen unbekannten Provider'."""
        models_dict["tasks"]["embedding"]["primary"]["provider"] = "gibtsnicht"

        with pytest.raises(ValueError, match="gibtsnicht"):
            ModelsConfig.model_validate(models_dict)

    def test_unbekannter_provider_in_einer_freigabe_ist_ein_fehler(
        self, models_dict: dict[str, Any]
    ) -> None:
        """Eine Freigabe für etwas, das es nicht gibt, wäre stillschweigend wirkungslos."""
        models_dict["policies"]["personal"]["allowed_providers"] = ["llamafile"]

        with pytest.raises(ValueError, match="llamafile"):
            ModelsConfig.model_validate(models_dict)

    @pytest.mark.parametrize("task", defaults.DETERMINISTIC_TASKS)
    def test_temperatur_ungleich_null_wird_bei_kantenaufgaben_abgelehnt(
        self, models_dict: dict[str, Any], task: str
    ) -> None:
        """§11.6: 'die Validierung lehnt andere Werte ab'."""
        models_dict["tasks"][task] = {
            "primary": {"provider": "gemini", "model": "irgendwas", "temperature": 0.7}
        }

        with pytest.raises(ValueError, match="temperature"):
            ModelsConfig.model_validate(models_dict)

    def test_auch_ein_fallback_muss_deterministisch_sein(self, models_dict: dict[str, Any]) -> None:
        """Der gefährlichste Fall: Er greift erst, wenn schon etwas schiefgegangen ist."""
        models_dict["tasks"]["relation_extraction"]["fallback"] = [
            {"provider": "ollama", "model": "lokal", "temperature": 0.4}
        ]

        with pytest.raises(ValueError, match="temperature"):
            ModelsConfig.model_validate(models_dict)

    def test_temperatur_bei_anderen_aufgaben_ist_erlaubt(self, models_dict: dict[str, Any]) -> None:
        models_dict["tasks"]["summarization"] = {
            "primary": {"provider": "gemini", "model": "irgendwas", "temperature": 0.3}
        }

        config = ModelsConfig.model_validate(models_dict)

        assert config.task("summarization").primary.temperature == 0.3

    def test_fehlende_freigabeliste_und_leere_liste_bedeuten_verschiedenes(
        self, models_dict: dict[str, Any]
    ) -> None:
        """Eine fehlende Angabe erlaubt alles, eine leere nichts — deshalb ``None`` statt ``()``."""
        config = ModelsConfig.model_validate(models_dict)

        assert config.allowed_providers("personal") == ("ollama",)
        assert config.allowed_providers("gibtsnicht") is None

    def test_unbekannte_aufgabe_nennt_die_vorhandenen(self, models_dict: dict[str, Any]) -> None:
        config = ModelsConfig.model_validate(models_dict)

        with pytest.raises(UnknownTaskError, match="embedding"):
            config.task("gibtsnicht")

    def test_unbekannter_provider_nennt_die_vorhandenen(self, models_dict: dict[str, Any]) -> None:
        config = ModelsConfig.model_validate(models_dict)

        with pytest.raises(UnknownProviderError, match="gemini"):
            config.provider("gibtsnicht")

    def test_ein_tippfehler_im_schluessel_bricht_ab(self, models_dict: dict[str, Any]) -> None:
        """``extra='forbid'``: Ein ignorierter Schlüssel wäre ein falsch laufendes System."""
        models_dict["tasks"]["embedding"]["primary"]["batchsize"] = 8

        with pytest.raises(ValueError, match="batchsize"):
            ModelsConfig.model_validate(models_dict)

    def test_kostenschaetzung_rechnet_je_tausend_token(self, models_dict: dict[str, Any]) -> None:
        models_dict["tasks"]["summarization"] = {
            "primary": {
                "provider": "gemini",
                "model": "irgendwas",
                "cost_per_1k_input_eur": 0.10,
                "cost_per_1k_output_eur": 0.40,
            }
        }
        route = ModelsConfig.model_validate(models_dict).task("summarization").primary

        assert route.estimate_cost(tokens_in=2000, tokens_out=500) == pytest.approx(0.4)

    def test_ohne_preisangabe_kostet_ein_aufruf_rechnerisch_nichts(
        self, models_dict: dict[str, Any]
    ) -> None:
        """Dann greift der Wächter allein über die Aufrufzahl — und das ist die härtere Grenze."""
        route = ModelsConfig.model_validate(models_dict).task("embedding").primary

        assert route.estimate_cost(tokens_in=10_000, tokens_out=0) == 0.0


class TestZugangsdaten:
    @pytest.mark.parametrize(
        ("provider", "erwartet"),
        [
            ({"type": "google_genai", "api_key": "x"}, True),
            ({"type": "google_genai"}, False),
            ({"type": "google_genai", "api_key": ""}, False),
            # Vertex braucht beides: Aus dem Standort folgt der Endpunkt, ohne ihn gibt es
            # keinen Host, den man ansprechen könnte.
            ({"type": "vertex", "project": "mein-projekt", "location": "eu"}, True),
            ({"type": "vertex", "project": "mein-projekt"}, False),
            ({"type": "vertex", "location": "eu"}, False),
            ({"type": "vertex"}, False),
            ({"type": "openai_compatible", "base_url": "http://x/v1"}, True),
            ({"type": "openai_compatible"}, False),
        ],
    )
    def test_konfiguriertheit_haengt_am_typ(
        self, models_dict: dict[str, Any], provider: dict[str, Any], erwartet: bool
    ) -> None:
        """Was ein Anbieter braucht, unterscheidet sich — ein Schlüssel ist nicht überall nötig."""
        models_dict["providers"]["kandidat"] = provider

        config = ModelsConfig.model_validate(models_dict)

        assert config.provider("kandidat").is_configured is erwartet

    def test_ein_leerer_platzhalter_zaehlt_als_nicht_gesetzt(
        self, models_dict: dict[str, Any]
    ) -> None:
        """``${WG_...:-}`` liefert einen leeren String; er darf nicht als Schlüssel durchgehen."""
        models_dict["providers"]["gemini"]["api_key"] = "   "

        assert ModelsConfig.model_validate(models_dict).provider("gemini").api_key is None


class TestLaden:
    def test_fehlende_datei_ergibt_eine_leere_konfiguration(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Ein System ohne Modell ist ein zulässiger Zustand (§11.5) und kein Startfehler."""
        config = load_models(settings, path=tmp_path / "gibtsnicht.yaml")

        assert config.tasks == {}

    def test_platzhalter_kommen_aus_der_umgebung(
        self, settings: Settings, write_models: Any, models_dict: dict[str, Any]
    ) -> None:
        models_dict["providers"]["gemini"]["api_key"] = "${WG_PROVIDER_GEMINI__API_KEY}"
        pfad = write_models(models_dict)

        config = load_models(
            settings, path=pfad, env={"WG_PROVIDER_GEMINI__API_KEY": "aus-der-umgebung"}
        )

        assert config.provider("gemini").api_key == "aus-der-umgebung"

    def test_abweichende_dimension_verhindert_den_start(
        self, settings: Settings, write_models: Any, models_dict: dict[str, Any]
    ) -> None:
        """§11.7: Der Router startet nicht, wenn ``dim`` von ``WG_EMBEDDING_DIM`` abweicht.

        Das Schema trägt die Zahl als ``vector(n)``; ein abweichendes Modell schriebe Vektoren,
        die die Spalte gar nicht aufnehmen kann — und das erst mitten im ersten Lauf.
        """
        models_dict["tasks"]["embedding"]["primary"]["dim"] = 1536
        pfad = write_models(models_dict)

        with pytest.raises(ConfigValidationError, match="1536"):
            load_models(settings, path=pfad, env={})

    def test_auch_ein_fallback_muss_die_dimension_treffen(
        self, settings: Settings, write_models: Any, models_dict: dict[str, Any]
    ) -> None:
        models_dict["tasks"]["embedding"]["fallback"] = [
            {"provider": "ollama", "model": "nomic-embed-text", "dim": 384}
        ]
        pfad = write_models(models_dict)

        with pytest.raises(ConfigValidationError, match="384"):
            load_models(settings, path=pfad, env={})

    def test_freigabe_fuer_einen_unbekannten_store_ist_ein_fehler(
        self, settings: Settings, write_models: Any, models_dict: dict[str, Any]
    ) -> None:
        models_dict["policies"]["archiv"] = {"allowed_providers": ["gemini"]}
        pfad = write_models(models_dict)

        with pytest.raises(ConfigValidationError, match="archiv"):
            load_models(settings, path=pfad, env={})

    def test_fehlermeldung_nennt_den_ort_des_problems(
        self, settings: Settings, write_models: Any, models_dict: dict[str, Any]
    ) -> None:
        models_dict["tasks"]["embedding"]["primary"]["batch_size"] = 0
        pfad = write_models(models_dict)

        with pytest.raises(ConfigValidationError, match=r"tasks\.embedding\.primary\.batch_size"):
            load_models(settings, path=pfad, env={})

    def test_pfad_kommt_aus_der_umgebungsvariablen(self, settings: Settings) -> None:
        """§6.4: ``WG_MODELS_FILE`` schlägt das Config-Verzeichnis."""
        gewaehlt = models_file(settings, {defaults.MODELS_FILE_ENV: "/woanders/modelle.yaml"})

        assert gewaehlt == Path("/woanders/modelle.yaml")

    def test_ohne_umgebungsvariable_liegt_die_datei_im_config_verzeichnis(
        self, settings: Settings
    ) -> None:
        gewaehlt = models_file(settings, {})

        assert gewaehlt.name == defaults.MODELS_CONFIG_FILENAME
        assert gewaehlt.parent == Path(settings.config_dir)


class TestAusgelieferteKonfiguration:
    """Die mitgelieferte ``config/models.yaml`` muss laden — sonst startet kein Container."""

    def test_repository_konfiguration_ist_gueltig(self, settings: Settings) -> None:
        pfad = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

        config = load_models(settings, path=pfad, env={})

        assert defaults.TASK_EMBEDDING in config.tasks
        assert config.task(defaults.TASK_EMBEDDING).primary.model == defaults.DEV_EMBEDDING_MODEL
        assert (
            config.task(defaults.TASK_RELATION_EXTRACTION).primary.model == defaults.DEV_CHAT_MODEL
        )

    def test_der_persoenliche_store_laesst_nur_lokale_anbieter_zu(self, settings: Settings) -> None:
        """§11.5 in der ausgelieferten Datei — nicht nur im Code."""
        pfad = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

        config = load_models(settings, path=pfad, env={})
        erlaubt = config.allowed_providers(defaults.STORE_PERSONAL) or ()

        assert erlaubt
        assert all(config.provider(name).local for name in erlaubt)

    def test_kein_zugangsschluessel_steht_in_der_datei(self) -> None:
        """§6.1 Regel 2 und §20.2: Secrets stehen nie in einer Config-Datei im Repository."""
        pfad = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
        roh = yaml.safe_load(pfad.read_text(encoding="utf-8"))

        for name, provider in roh["providers"].items():
            schluessel = provider.get("api_key")
            assert (
                schluessel is None or schluessel.startswith("${") or schluessel == "not-needed"
            ), (
                f"Provider '{name}' trägt einen ausgeschriebenen Wert in api_key. Secrets kommen "
                f"aus ENV (§6.1 Regel 2)."
            )
