"""Der Model-Router (§11.2 bis §11.7).

Die fünf Abnahmekriterien der Stufe 7 aus §24 stehen als eigene Klassen darin:

1. Ein Modellwechsel in ``models.yaml`` wirkt ohne Codeänderung — :class:`TestModellwechsel`.
2. Ein Aufruf mit ``store = personal`` gegen einen nicht-lokalen Provider wirft —
   :class:`TestStorePolicy`.
3. Ungültiges JSON löst genau **einen** Reparaturversuch aus —
   :class:`TestStrukturierteAusgabe`.
4. Ein wiederholter identischer Aufruf ist ein Cache-Treffer — :class:`TestZwischenspeicher`.
5. Ein Budgetüberschritt beendet den Lauf sauber mit Teilergebnis — :class:`TestBudget`.

Kein Test hier spricht ein echtes Modell an. Das ist keine Einschränkung, sondern die Aussage:
Alles, was den Router ausmacht, liegt zwischen Aufgabe und Anbieter — nicht im Anbieter.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from support.memory import MemoryUnitOfWorkFactory
from wissensgraph.config import defaults
from wissensgraph.config.models import ModelsConfig
from wissensgraph.config.schema import Settings
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.infrastructure.models.cache import MemoryResponseCache
from wissensgraph.ports.models import (
    BudgetExceededError,
    InvalidModelOutputError,
    ModelError,
    PromptSpec,
    RawCompletion,
    RawEmbedding,
)
from wissensgraph.services.router import ModelRouterService, NoRouteError
from wissensgraph.testing.models import FakeClients, FakeEmbeddings, ScriptedChat

pytestmark = pytest.mark.unit

DIM = 768


class Beziehung(BaseModel):
    """Das Ausgabeschema aus §14.3 — hier nur als Prüfstein der Validierung."""

    relationship: str | None
    confidence: float


@pytest.fixture
def models_dict() -> dict[str, Any]:
    """Ein externer und ein lokaler Anbieter, drei Aufgaben."""
    return {
        "providers": {
            "gemini": {"type": "google_genai", "api_key": "geheim", "local": False},
            "ollama": {
                "type": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "local": True,
            },
        },
        "defaults": {"max_retries": 1},
        "tasks": {
            "embedding": {
                "primary": {
                    "provider": "gemini",
                    "model": "gemini-embedding-2",
                    "dim": DIM,
                    "batch_size": 2,
                    "cost_per_1k_input_eur": 1.0,
                }
            },
            "relation_extraction": {
                "primary": {
                    "provider": "gemini",
                    "model": "gemini-3.5-flash-lite",
                    "temperature": 0.0,
                    "json_mode": True,
                    "cost_per_1k_input_eur": 1.0,
                    "cost_per_1k_output_eur": 1.0,
                }
            },
            "summarization": {"primary": {"provider": "gemini", "model": "gemini-3.5-flash-lite"}},
        },
        "policies": {
            "personal": {"allowed_providers": ["ollama"]},
            "shared": {"allowed_providers": ["gemini", "ollama"]},
        },
    }


@pytest.fixture
def models(models_dict: dict[str, Any]) -> ModelsConfig:
    return ModelsConfig.model_validate(models_dict)


@pytest.fixture
def uow() -> MemoryUnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(("shared", "personal"))


def router(
    settings: Settings,
    models: ModelsConfig,
    clients: Any,
    *,
    uow: MemoryUnitOfWorkFactory | None = None,
    cache: Any = None,
) -> ModelRouterService:
    """Ein Router ohne echte Wartezeiten — ``sleep`` ist stillgelegt."""
    return ModelRouterService(
        settings,
        models,
        clients,
        unit_of_work=uow,
        cache=cache,
        sleep=lambda _: None,
    )


# ---------------------------------------------------------------------------
# 1. Modellwechsel (§11.1, §24)
# ---------------------------------------------------------------------------


class TestModellwechsel:
    def test_describe_nennt_das_greifende_modell_ohne_es_aufzurufen(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        clients = FakeClients(dim=DIM)

        route = router(settings, models, clients).describe(defaults.TASK_EMBEDDING)

        assert route.model_key == "gemini:gemini-embedding-2"
        assert route.dim == DIM
        assert clients.chat_client.calls == []

    def test_ein_geaenderter_eintrag_wirkt_ohne_codeaenderung(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        """§24: 'Ein Modellwechsel in models.yaml wirkt ohne Codeänderung'."""
        models_dict["tasks"]["embedding"]["primary"] = {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "dim": DIM,
        }
        gewechselt = ModelsConfig.model_validate(models_dict)

        route = router(settings, gewechselt, FakeClients(dim=DIM)).describe(defaults.TASK_EMBEDDING)

        assert route.model_key == "ollama:nomic-embed-text"
        assert route.local is True

    def test_provenienz_traegt_provider_modell_aufgabe_und_routerversion(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """§11.6: ``"<provider>:<model>/<task>@v<router-version>"``."""
        route = router(settings, models, FakeClients(dim=DIM)).describe(
            defaults.TASK_RELATION_EXTRACTION
        )

        assert route.generated_by == (
            f"gemini:gemini-3.5-flash-lite/relation_extraction@v{defaults.ROUTER_VERSION}"
        )

    def test_fehlende_zugangsdaten_werden_gemeldet_statt_verschwiegen(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        models_dict["providers"]["gemini"]["api_key"] = None
        ohne = ModelsConfig.model_validate(models_dict)

        route = router(settings, ohne, FakeClients(dim=DIM)).describe(defaults.TASK_EMBEDDING)

        assert route.configured is False

    def test_routes_listet_alle_aufgaben_alphabetisch(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        namen = [route.task for route in router(settings, models, FakeClients(dim=DIM)).routes()]

        assert namen == sorted(namen)
        assert defaults.TASK_EMBEDDING in namen


# ---------------------------------------------------------------------------
# 2. Store-Policy (§11.5, §24)
# ---------------------------------------------------------------------------


class TestStorePolicy:
    def test_persoenliche_inhalte_gehen_nicht_an_einen_externen_anbieter(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """§24: 'ein Aufruf mit store = personal gegen einen nicht-lokalen Provider wirft'."""
        dienst = router(settings, models, FakeClients(dim=DIM))

        with pytest.raises(ProviderNotAllowedError):
            dienst.embed(defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal")

    def test_derselbe_aufruf_im_geteilten_store_geht_durch(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Der Unterschied liegt allein im Store — nicht im Inhalt und nicht im Modell."""
        ergebnis = router(settings, models, FakeClients(dim=DIM)).embed(
            defaults.TASK_EMBEDDING, ["eine Seite"], store="shared"
        )

        assert len(ergebnis.vectors) == 1

    def test_ein_verstoss_faellt_nie_auf_einen_erlaubten_anbieter_zurueck(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        """§11.5: nie 'ein stiller Fallback auf einen erlaubten, aber schlechteren Anbieter'."""
        models_dict["tasks"]["embedding"]["fallback"] = [
            {"provider": "ollama", "model": "nomic-embed-text", "dim": DIM}
        ]
        mit_fallback = ModelsConfig.model_validate(models_dict)
        clients = FakeClients(dim=DIM)

        with pytest.raises(ProviderNotAllowedError):
            router(settings, mit_fallback, clients).embed(
                defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal"
            )

    def test_die_bewusste_ausnahme_oeffnet_die_grenze(
        self, minimal_config_dict: dict[str, Any], models_dict: dict[str, Any]
    ) -> None:
        """``WG_PERSONAL_ALLOW_REMOTE_MODELS=true`` weicht die Ortsregel auf (§11.5)…"""
        offen = Settings.model_validate(
            {**minimal_config_dict, "personal_allow_remote_models": True}
        )
        # …die ausdrückliche Freigabeliste bleibt davon unberührt und muss mitgezogen werden.
        models_dict["policies"]["personal"]["allowed_providers"] = ["gemini", "ollama"]
        geoeffnet = ModelsConfig.model_validate(models_dict)

        ergebnis = router(offen, geoeffnet, FakeClients(dim=DIM)).embed(
            defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal"
        )

        assert len(ergebnis.vectors) == 1

    def test_die_freigabeliste_wirkt_auch_bei_geoeffneter_ortsregel(
        self, minimal_config_dict: dict[str, Any], models: ModelsConfig
    ) -> None:
        """Zwei Hälften, zwei Fragen: *wo* ein Anbieter läuft und *ob* er vorgesehen ist."""
        offen = Settings.model_validate(
            {**minimal_config_dict, "personal_allow_remote_models": True}
        )

        with pytest.raises(ProviderNotAllowedError, match="allowed_providers"):
            router(offen, models, FakeClients(dim=DIM)).embed(
                defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal"
            )

    def test_ein_verstoss_hinterlaesst_einen_eintrag_in_model_calls(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """§11.5 verlangt wörtlich einen Eintrag mit ``status = 'budget_denied'``."""
        dienst = router(settings, models, FakeClients(dim=DIM), uow=uow)

        with pytest.raises(ProviderNotAllowedError):
            dienst.embed(defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal")

        eintraege = uow.state("personal").model_calls
        assert [call.status for call in eintraege] == [defaults.MODEL_CALL_BUDGET_DENIED]


# ---------------------------------------------------------------------------
# 3. Strukturierte Ausgabe (§11.6, §24)
# ---------------------------------------------------------------------------


class TestStrukturierteAusgabe:
    def test_gueltige_antwort_wird_gegen_das_schema_validiert(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        clients = FakeClients(
            dim=DIM, chat=json.dumps({"relationship": "depends_on", "confidence": 0.82})
        )

        ergebnis = router(settings, models, clients).complete(
            defaults.TASK_RELATION_EXTRACTION,
            prompt=PromptSpec(system=None, user="A und B?"),
            schema=Beziehung,
            store="shared",
        )

        assert isinstance(ergebnis.parsed, Beziehung)
        assert ergebnis.parsed.relationship == "depends_on"
        assert ergebnis.attempts == 1

    def test_ein_code_zaun_um_die_antwort_ist_kein_formfehler(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Modelle liefern ihre Antwort gern mit ```json davor — daran soll nichts scheitern."""
        clients = FakeClients(
            dim=DIM,
            chat='Klar!\n```json\n{"relationship": null, "confidence": 0.1}\n```\nPasst so.',
        )

        ergebnis = router(settings, models, clients).complete(
            defaults.TASK_RELATION_EXTRACTION,
            prompt=PromptSpec(system=None, user="A und B?"),
            schema=Beziehung,
            store="shared",
        )

        assert isinstance(ergebnis.parsed, Beziehung)
        assert ergebnis.parsed.relationship is None
        assert ergebnis.attempts == 1

    def test_ungueltiges_json_loest_genau_einen_reparaturversuch_aus(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """§24: 'ungültiges JSON löst genau einen Reparaturversuch aus'."""

        def antwort(prompt: PromptSpec) -> str:
            if "Reparatur" in prompt.user or "gültiges JSON" in prompt.user:
                return json.dumps({"relationship": "extends", "confidence": 0.7})
            return "leider kein JSON"

        clients = FakeClients(dim=DIM, chat=antwort)

        ergebnis = router(settings, models, clients).complete(
            defaults.TASK_RELATION_EXTRACTION,
            prompt=PromptSpec(system=None, user="A und B?"),
            schema=Beziehung,
            store="shared",
        )

        assert ergebnis.attempts == 2
        assert len(clients.chat_client.calls) == 2

    def test_nach_dem_reparaturversuch_ist_schluss(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Trifft ein Modell die Form beim zweiten Mal nicht, trifft es sie auch beim fünften."""
        clients = FakeClients(dim=DIM, chat="niemals JSON")

        with pytest.raises(InvalidModelOutputError):
            router(settings, models, clients).complete(
                defaults.TASK_RELATION_EXTRACTION,
                prompt=PromptSpec(system=None, user="A und B?"),
                schema=Beziehung,
                store="shared",
            )

        assert len(clients.chat_client.calls) == 2

    def test_ungueltige_ausgabe_wird_als_solche_verbucht(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        clients = FakeClients(dim=DIM, chat="niemals JSON")

        with pytest.raises(InvalidModelOutputError):
            router(settings, models, clients, uow=uow).complete(
                defaults.TASK_RELATION_EXTRACTION,
                prompt=PromptSpec(system=None, user="A und B?"),
                schema=Beziehung,
                store="shared",
            )

        status = [call.status for call in uow.state("shared").model_calls]
        assert defaults.MODEL_CALL_INVALID_OUTPUT in status

    def test_ohne_schema_bleibt_die_antwort_roh(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        clients = FakeClients(dim=DIM, chat="Eine Zusammenfassung in einem Satz.")

        ergebnis = router(settings, models, clients).complete(
            defaults.TASK_SUMMARIZATION,
            prompt=PromptSpec(system=None, user="Fasse zusammen."),
            store="shared",
        )

        assert ergebnis.parsed is None
        assert ergebnis.raw == "Eine Zusammenfassung in einem Satz."

    def test_der_reparaturversuch_nennt_die_fehlermeldung(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Ohne den Grund wäre die Nachfrage nur eine Wiederholung derselben Bitte."""
        clients = FakeClients(dim=DIM, chat="kein JSON")

        with pytest.raises(InvalidModelOutputError):
            router(settings, models, clients).complete(
                defaults.TASK_RELATION_EXTRACTION,
                prompt=PromptSpec(system=None, user="A und B?"),
                schema=Beziehung,
                store="shared",
            )

        zweiter = clients.chat_client.calls[1].user
        assert "Fehler:" in zweiter
        assert "relationship" in zweiter


# ---------------------------------------------------------------------------
# 4. Zwischenspeicher (§11.6, §24)
# ---------------------------------------------------------------------------


class TestZwischenspeicher:
    def test_ein_wiederholter_aufruf_ist_ein_cache_treffer(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """§24: 'ein wiederholter identischer Aufruf ist ein Cache-Treffer'."""
        clients = FakeClients(dim=DIM, chat=json.dumps({"relationship": None, "confidence": 0.0}))
        dienst = router(settings, models, clients, cache=MemoryResponseCache())
        prompt = PromptSpec(system="Sei knapp.", user="A und B?")

        erst = dienst.complete(
            defaults.TASK_RELATION_EXTRACTION, prompt=prompt, schema=Beziehung, store="shared"
        )
        wieder = dienst.complete(
            defaults.TASK_RELATION_EXTRACTION, prompt=prompt, schema=Beziehung, store="shared"
        )

        assert erst.cached is False
        assert wieder.cached is True
        assert len(clients.chat_client.calls) == 1

    def test_ein_geaendertes_systemprompt_trifft_nicht_denselben_eintrag(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Eine geänderte Anweisung macht den Zwischenspeicher genauso ungültig wie ein Inhalt."""
        clients = FakeClients(dim=DIM, chat=json.dumps({"relationship": None, "confidence": 0.0}))
        dienst = router(settings, models, clients, cache=MemoryResponseCache())

        dienst.complete(
            defaults.TASK_RELATION_EXTRACTION,
            prompt=PromptSpec(system="Sei knapp.", user="A und B?"),
            schema=Beziehung,
            store="shared",
        )
        dienst.complete(
            defaults.TASK_RELATION_EXTRACTION,
            prompt=PromptSpec(system="Sei ausführlich.", user="A und B?"),
            schema=Beziehung,
            store="shared",
        )

        assert len(clients.chat_client.calls) == 2

    def test_ein_modellwechsel_macht_den_zwischenspeicher_von_selbst_ungueltig(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        """Der ``model_key`` geht in den Schlüssel ein — sonst käme die alte Antwort zurück."""
        cache = MemoryResponseCache()
        prompt = PromptSpec(system=None, user="Fasse zusammen.")

        erste = FakeClients(dim=DIM, chat="Antwort des ersten Modells.")
        router(settings, ModelsConfig.model_validate(models_dict), erste, cache=cache).complete(
            defaults.TASK_SUMMARIZATION, prompt=prompt, store="shared"
        )

        models_dict["tasks"]["summarization"]["primary"]["model"] = "ein-anderes-modell"
        zweite = FakeClients(dim=DIM, chat="Antwort des zweiten Modells.")
        ergebnis = router(
            settings, ModelsConfig.model_validate(models_dict), zweite, cache=cache
        ).complete(defaults.TASK_SUMMARIZATION, prompt=prompt, store="shared")

        assert ergebnis.raw == "Antwort des zweiten Modells."

    def test_embeddings_werden_je_text_zwischengespeichert(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Nicht je Aufruf: Ein zweiter Lauf über einen fast gleichen Bestand zahlt nur das Neue."""
        clients = FakeClients(dim=DIM)
        dienst = router(settings, models, clients, cache=MemoryResponseCache())

        dienst.embed(defaults.TASK_EMBEDDING, ["A", "B"], store="shared")
        wieder = dienst.embed(defaults.TASK_EMBEDDING, ["A", "B", "C"], store="shared")

        assert wieder.cached == 2
        assert len(wieder.vectors) == 3

    def test_ein_cache_treffer_wird_als_solcher_verbucht(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        clients = FakeClients(dim=DIM, chat=json.dumps({"relationship": None, "confidence": 0.0}))
        dienst = router(settings, models, clients, uow=uow, cache=MemoryResponseCache())
        prompt = PromptSpec(system=None, user="A und B?")

        dienst.complete(
            defaults.TASK_RELATION_EXTRACTION, prompt=prompt, schema=Beziehung, store="shared"
        )
        dienst.complete(
            defaults.TASK_RELATION_EXTRACTION, prompt=prompt, schema=Beziehung, store="shared"
        )

        status = [call.status for call in uow.state("shared").model_calls]
        assert status.count(defaults.MODEL_CALL_CACHE_HIT) == 1

    def test_ein_unlesbarer_eintrag_gilt_als_fehltreffer(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Der Zwischenspeicher ist kein Speicher: Unlesbares wird neu berechnet, nicht gemeldet."""
        cache = MemoryResponseCache()
        clients = FakeClients(dim=DIM)
        dienst = router(settings, models, clients, cache=cache)
        dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared")

        # Ein früherer Stand hätte den Vektor anders abgelegt.
        for schluessel in list(cache._werte):
            cache._werte[schluessel] = "kein Vektor"

        ergebnis = dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared")

        assert ergebnis.cached == 0
        assert len(ergebnis.vectors) == 1


# ---------------------------------------------------------------------------
# 5. Budget (§11.6, §24)
# ---------------------------------------------------------------------------


class TestBudget:
    def test_ein_erschoepftes_budget_beendet_den_aufruf_statt_ihn_zu_bezahlen(
        self, minimal_config_dict: dict[str, Any], models: ModelsConfig
    ) -> None:
        """§24: 'ein Budgetüberschritt beendet den Lauf sauber mit Teilergebnis'."""
        knapp = Settings.model_validate(
            {**minimal_config_dict, "budget": {"max_model_calls_per_run": 0}}
        )
        clients = FakeClients(dim=DIM)

        with pytest.raises(BudgetExceededError):
            router(knapp, models, clients).embed(defaults.TASK_EMBEDDING, ["A"], store="shared")

    def test_die_grenze_greift_vor_dem_aufruf(
        self, minimal_config_dict: dict[str, Any], models: ModelsConfig
    ) -> None:
        """Eine Grenze, die nach der Antwort greift, hat den Aufruf schon bezahlt."""
        knapp = Settings.model_validate(
            {**minimal_config_dict, "budget": {"max_model_calls_per_run": 0}}
        )
        clients = FakeClients(dim=DIM, chat="egal")

        with pytest.raises(BudgetExceededError):
            router(knapp, models, clients).complete(
                defaults.TASK_SUMMARIZATION,
                prompt=PromptSpec(system=None, user="Fasse zusammen."),
                store="shared",
            )

        assert clients.chat_client.calls == []

    def test_der_verbrauch_eines_laufs_kommt_aus_model_calls(
        self,
        minimal_config_dict: dict[str, Any],
        models: ModelsConfig,
        uow: MemoryUnitOfWorkFactory,
    ) -> None:
        """Ein Lauf kann über Prozesse verteilt sein; ein Zähler im Speicher genügt dafür nicht."""
        knapp = Settings.model_validate(
            {**minimal_config_dict, "budget": {"max_model_calls_per_run": 2}}
        )
        lauf = uuid4()
        dienst = router(knapp, models, FakeClients(dim=DIM), uow=uow)

        for text in ("A", "B"):
            dienst.embed(defaults.TASK_EMBEDDING, [text], store="shared", run_id=lauf)

        with pytest.raises(BudgetExceededError):
            dienst.embed(defaults.TASK_EMBEDDING, ["C"], store="shared", run_id=lauf)

    def test_ein_cache_treffer_zaehlt_nicht_gegen_das_budget(
        self,
        minimal_config_dict: dict[str, Any],
        models: ModelsConfig,
        uow: MemoryUnitOfWorkFactory,
    ) -> None:
        """Sonst brächte ein Wiederholungslauf alles zum Erliegen, ohne etwas zu verbrauchen."""
        knapp = Settings.model_validate(
            {**minimal_config_dict, "budget": {"max_model_calls_per_run": 1}}
        )
        lauf = uuid4()
        dienst = router(knapp, models, FakeClients(dim=DIM), uow=uow, cache=MemoryResponseCache())

        dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared", run_id=lauf)
        wieder = dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared", run_id=lauf)

        assert wieder.cached == 1

    def test_warn_zaehlt_und_laesst_weiterlaufen(
        self, minimal_config_dict: dict[str, Any], models: ModelsConfig
    ) -> None:
        """``warn`` ist keine abgeschwächte Grenze, sondern eine andere Aussage (§11.6)."""
        beobachtend = Settings.model_validate(
            {
                **minimal_config_dict,
                "budget": {"max_model_calls_per_run": 0, "on_exceed": "warn"},
            }
        )

        ergebnis = router(beobachtend, models, FakeClients(dim=DIM)).embed(
            defaults.TASK_EMBEDDING, ["A"], store="shared"
        )

        assert len(ergebnis.vectors) == 1

    def test_ein_abgewiesener_aufruf_wird_verbucht(
        self,
        minimal_config_dict: dict[str, Any],
        models: ModelsConfig,
        uow: MemoryUnitOfWorkFactory,
    ) -> None:
        knapp = Settings.model_validate(
            {**minimal_config_dict, "budget": {"max_model_calls_per_run": 0}}
        )

        with pytest.raises(BudgetExceededError):
            router(knapp, models, FakeClients(dim=DIM), uow=uow).embed(
                defaults.TASK_EMBEDDING, ["A"], store="shared"
            )

        status = [call.status for call in uow.state("shared").model_calls]
        assert status == [defaults.MODEL_CALL_BUDGET_DENIED]

    def test_geschaetzte_kosten_zaehlen_gegen_die_obergrenze(
        self,
        minimal_config_dict: dict[str, Any],
        models: ModelsConfig,
        uow: MemoryUnitOfWorkFactory,
    ) -> None:
        teuer = Settings.model_validate(
            {
                **minimal_config_dict,
                "budget": {
                    "max_model_calls_per_run": 100,
                    "max_estimated_cost_per_run_eur": 0.001,
                },
            }
        )
        lauf = uuid4()
        dienst = router(teuer, models, FakeClients(dim=DIM), uow=uow)

        dienst.embed(
            defaults.TASK_EMBEDDING, ["ein hinreichend langer Text"], store="shared", run_id=lauf
        )

        with pytest.raises(BudgetExceededError, match="cost"):
            dienst.embed(defaults.TASK_EMBEDDING, ["noch einer"], store="shared", run_id=lauf)


# ---------------------------------------------------------------------------
# Fallback, Retries und Bündelung (§11.6)
# ---------------------------------------------------------------------------


class TestKetteUndWiederholung:
    def test_ein_ausgefallener_primary_wird_wiederholt(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Ein kurzzeitig überlasteter Anbieter soll denselben Lauf nicht zum Scheitern bringen."""
        clients = FakeClients(dim=DIM, chat="Antwort.", fehler_bei=1)

        ergebnis = router(settings, models, clients).complete(
            defaults.TASK_SUMMARIZATION,
            prompt=PromptSpec(system=None, user="Fasse zusammen."),
            store="shared",
        )

        assert ergebnis.raw == "Antwort."
        assert len(clients.chat_client.calls) == 2

    def test_nach_erschoepften_versuchen_greift_der_fallback(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        """§11.6: Fallback 'erst nach erschöpften Retries des Primary'."""
        models_dict["defaults"]["max_retries"] = 0
        models_dict["tasks"]["summarization"]["fallback"] = [
            {"provider": "ollama", "model": "lokal"}
        ]
        mit_fallback = ModelsConfig.model_validate(models_dict)

        class Kette:
            """Der erste Anbieter fällt aus, der zweite antwortet."""

            def __init__(self) -> None:
                self.benutzt: list[str] = []

            def chat(self, task: str, route: Any) -> Any:
                self.benutzt.append(route.provider)
                if route.provider == "gemini":
                    return ScriptedChat("egal", fehler_bei=1)
                return ScriptedChat("Antwort des Fallbacks.")

            def embeddings(self, task: str, route: Any) -> Any:  # pragma: no cover
                return FakeEmbeddings(DIM)

        clients = Kette()
        ergebnis = router(settings, mit_fallback, clients).complete(
            defaults.TASK_SUMMARIZATION,
            prompt=PromptSpec(system=None, user="Fasse zusammen."),
            store="shared",
        )

        assert ergebnis.raw == "Antwort des Fallbacks."
        assert clients.benutzt == ["gemini", "ollama"]

    def test_eine_ungueltige_ausgabe_loest_keinen_fallback_aus(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        """Das Modell war erreichbar und hat geantwortet — das ist ein Ergebnis, kein Ausfall."""
        models_dict["tasks"]["relation_extraction"]["fallback"] = [
            {"provider": "ollama", "model": "lokal", "temperature": 0.0}
        ]
        mit_fallback = ModelsConfig.model_validate(models_dict)
        clients = FakeClients(dim=DIM, chat="niemals JSON")

        with pytest.raises(InvalidModelOutputError):
            router(settings, mit_fallback, clients).complete(
                defaults.TASK_RELATION_EXTRACTION,
                prompt=PromptSpec(system=None, user="A und B?"),
                schema=Beziehung,
                store="shared",
            )

    def test_scheitern_aller_routen_meldet_den_letzten_grund(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        class Dauerausfall:
            """Ein Anbieter, der jeden Versuch abweist — nicht nur den ersten."""

            def chat(self, task: str, route: Any) -> Any:
                class Nie:
                    def complete(self, prompt: PromptSpec) -> RawCompletion:
                        raise ModelError("dauerhaft nicht erreichbar")

                return Nie()

            def embeddings(self, task: str, route: Any) -> Any:  # pragma: no cover
                return FakeEmbeddings(DIM)

        with pytest.raises(ModelError, match="Versuche"):
            router(settings, models, Dauerausfall()).complete(
                defaults.TASK_SUMMARIZATION,
                prompt=PromptSpec(system=None, user="Fasse zusammen."),
                store="shared",
            )

    def test_eine_aufgabe_ohne_route_meldet_das_verstaendlich(
        self, settings: Settings, models_dict: dict[str, Any]
    ) -> None:
        models_dict["tasks"] = {}
        leer = ModelsConfig.model_validate(models_dict)

        with pytest.raises(KeyError, match="summarization"):
            router(settings, leer, FakeClients(dim=DIM)).complete(
                defaults.TASK_SUMMARIZATION,
                prompt=PromptSpec(system=None, user="x"),
                store="shared",
            )

    def test_texte_werden_nach_batch_size_gebuendelt(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """§11.6, 'Batching': ``batch_size`` ist hier 2, also drei Texte in zwei Aufrufen."""
        aufrufe: list[int] = []

        class Zaehlend:
            def chat(self, task: str, route: Any) -> Any:  # pragma: no cover
                return ScriptedChat("egal")

            def embeddings(self, task: str, route: Any) -> Any:
                echt = FakeEmbeddings(DIM)

                class Mitzaehlend:
                    def embed(self, texts: Any) -> RawEmbedding:
                        aufrufe.append(len(texts))
                        return echt.embed(texts)

                return Mitzaehlend()

        ergebnis = router(settings, models, Zaehlend()).embed(
            defaults.TASK_EMBEDDING, ["A", "B", "C"], store="shared"
        )

        assert aufrufe == [2, 1]
        assert len(ergebnis.vectors) == 3

    def test_eine_leere_eingabe_ruft_kein_modell_auf(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        clients = FakeClients(dim=DIM)

        ergebnis = router(settings, models, clients).embed(
            defaults.TASK_EMBEDDING, [], store="shared"
        )

        assert ergebnis.vectors == ()
        assert ergebnis.model_key == "gemini:gemini-embedding-2"


class TestProtokollUndAuswertung:
    def test_ein_gelungener_aufruf_wird_mit_token_und_kosten_verbucht(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        router(settings, models, FakeClients(dim=DIM), uow=uow).embed(
            defaults.TASK_EMBEDDING, ["ein hinreichend langer Text"], store="shared"
        )

        eintrag = uow.state("shared").model_calls[0]
        assert eintrag.status == defaults.MODEL_CALL_OK
        assert eintrag.tokens_in and eintrag.tokens_in > 0
        assert eintrag.cost_estimate and eintrag.cost_estimate > 0

    def test_aufrufe_werden_im_store_des_inhalts_verbucht(
        self, minimal_config_dict: dict[str, Any], models_dict: dict[str, Any], uow: Any
    ) -> None:
        """Leitprinzip 2 gilt auch für die Abrechnung."""
        offen = Settings.model_validate(
            {**minimal_config_dict, "personal_allow_remote_models": True}
        )
        models_dict["policies"]["personal"]["allowed_providers"] = ["gemini", "ollama"]
        erlaubt = ModelsConfig.model_validate(models_dict)

        router(offen, erlaubt, FakeClients(dim=DIM), uow=uow).embed(
            defaults.TASK_EMBEDDING, ["eine Notiz"], store="personal"
        )

        assert len(uow.state("personal").model_calls) == 1
        assert uow.state("shared").model_calls == []

    def test_ein_abrechnungsstore_lenkt_die_zeile_um_und_laesst_den_eintrag_gleich(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Der Ausweg für den MCP-Server (§18.3).

        Er hält auf ``shared`` eine nur lesende Verbindung, braucht für eine semantische Suche
        dort aber ein Anfrage-Embedding — also einen Modellaufruf, der nach §11.6 verbucht
        gehört. Ohne diese Umleitung scheiterte ``graph_search`` an der Schreibsperre, und zwar
        in der Datenbank: Die Suche selbst ist fehlerfrei, es ist die Buchführung, die anstößt.

        Umgelenkt wird nur die *Zeile*. ``call.store`` nennt weiterhin ``shared`` — sonst wiese
        die Abrechnung die Kosten dem falschen Store zu.
        """
        ModelRouterService(
            settings,
            models,
            FakeClients(dim=DIM),
            unit_of_work=uow,
            accounting_store="personal",
            sleep=lambda _: None,
        ).embed(defaults.TASK_EMBEDDING, ["eine Anfrage des Agenten"], store="shared")

        assert uow.state("shared").model_calls == []
        eintraege = uow.state("personal").model_calls
        assert len(eintraege) == 1
        assert eintraege[0].store == "shared"

    def test_usage_gruppiert_nach_aufgabe_und_modell(
        self, settings: Settings, models: ModelsConfig, uow: MemoryUnitOfWorkFactory
    ) -> None:
        dienst = router(settings, models, FakeClients(dim=DIM), uow=uow)
        dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared")
        dienst.embed(defaults.TASK_EMBEDDING, ["B"], store="shared")

        zeilen = dienst.usage(store="shared")

        assert len(zeilen) == 1
        assert zeilen[0].calls == 2
        assert zeilen[0].task == defaults.TASK_EMBEDDING

    def test_ohne_persistenz_gibt_es_keine_auswertung_und_keinen_fehler(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        assert router(settings, models, FakeClients(dim=DIM)).usage(store="shared") == ()


class TestFakeProvider:
    """Der Fake ist Teil des Systems (§24) — also wird er auch geprüft."""

    def test_gleiche_texte_ergeben_gleiche_vektoren(self) -> None:
        modell = FakeEmbeddings(DIM)

        assert modell.vector("Partitionierung") == modell.vector("Partitionierung")

    def test_thematisch_nahe_texte_liegen_naeher_beieinander(self) -> None:
        """Ohne diese Eigenschaft prüfte ein Clustering-Test nur seine eigene Vorbereitung."""
        modell = FakeEmbeddings(DIM)

        def kosinus(a: tuple[float, ...], b: tuple[float, ...]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        nah = kosinus(
            modell.vector("Partitionierung der Faktentabellen"),
            modell.vector("Faktentabellen partitionieren im Warehouse"),
        )
        fern = kosinus(
            modell.vector("Partitionierung der Faktentabellen"),
            modell.vector("Urlaubsantrag im Personalportal stellen"),
        )

        assert nah > fern

    def test_ein_text_ohne_woerter_bekommt_trotzdem_einen_einheitsvektor(self) -> None:
        """Ein Nullvektor wäre in der Kosinusähnlichkeit undefiniert."""
        vektor = FakeEmbeddings(DIM).vector("   ")

        assert sum(wert * wert for wert in vektor) == pytest.approx(1.0)

    def test_die_dimension_folgt_der_route(self, models: ModelsConfig) -> None:
        clients = FakeClients(dim=8)
        route = models.task(defaults.TASK_EMBEDDING).primary

        antwort = clients.embeddings(defaults.TASK_EMBEDDING, route).embed(["A"])

        assert len(antwort.vectors[0]) == DIM

    def test_ein_skript_kann_auf_den_prompt_reagieren(self) -> None:
        chat = ScriptedChat(lambda prompt: f"Antwort auf: {prompt.user}")

        antwort = chat.complete(PromptSpec(system=None, user="Frage"))

        assert isinstance(antwort, RawCompletion)
        assert antwort.text == "Antwort auf: Frage"


class TestRouterOhneNetz:
    def test_kein_test_dieser_datei_hat_ein_modell_angesprochen(
        self, settings: Settings, models: ModelsConfig
    ) -> None:
        """Die Fabrik ist ein Port; ohne sie gäbe es keinen Weg, den Router ohne Netz zu prüfen.

        Der Test hält fest, was die ganze Datei voraussetzt: Alles, was den Router ausmacht, liegt
        zwischen Aufgabe und Anbieter — die LangChain-Integration ist austauschbar, ohne dass sich
        an diesem Verhalten etwas ändert.
        """
        clients = FakeClients(dim=DIM)

        dienst = router(settings, models, clients)
        dienst.describe(defaults.TASK_EMBEDDING)

        assert clients.chat_client.calls == []


class TestNoRoute:
    def test_eine_leere_kette_meldet_einen_verstaendlichen_grund(
        self, settings: Settings, models: ModelsConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Der Fall ist selten, aber die Meldung ist der einzige Hinweis, wenn er eintritt."""
        dienst = router(settings, models, FakeClients(dim=DIM))
        monkeypatch.setattr(dienst, "_kette", lambda task, *, store: iter(()))

        with pytest.raises(NoRouteError, match="models describe"):
            dienst.embed(defaults.TASK_EMBEDDING, ["A"], store="shared")


class TestGleichzeitigkeit:
    """``max_concurrency`` je Anbieter (§11.4).

    Der Anlass ist gemessen und nicht theoretisch: Vertex nimmt für die Embedding-Modelle genau
    einen Text je Aufruf entgegen. Ein Lauf über 120 Seiten sind dort 120 Round-Trips
    nacheinander, und was dabei wartet, ist ausschließlich das Netz.
    """

    @staticmethod
    def _messende_clients(dim: int, dauer: float = 0.05) -> Any:
        """Eine Client-Fabrik, die zählt, wie viele Aufrufe sich zeitlich überlappen."""
        import threading

        class Messend:
            def __init__(self) -> None:
                self.gleichzeitig = 0
                self.hoechststand = 0
                self.aufrufe = 0
                self._sperre = threading.Lock()
                self._echt = FakeEmbeddings(dim)

            def embed(self, texts: Any) -> Any:
                with self._sperre:
                    self.gleichzeitig += 1
                    self.aufrufe += 1
                    self.hoechststand = max(self.hoechststand, self.gleichzeitig)
                try:
                    time.sleep(dauer)
                    return self._echt.embed(texts)
                finally:
                    with self._sperre:
                        self.gleichzeitig -= 1

        messend = Messend()

        class Fabrik:
            embeddings_client = messend

            def chat(self, task: str, route: object) -> Any:  # pragma: no cover — ungenutzt
                raise NotImplementedError

            def embeddings(self, task: str, route: object) -> Any:
                return messend

        return Fabrik(), messend

    def test_ohne_angabe_laufen_die_aufrufe_nacheinander(
        self, settings: Settings, models_dict: dict[str, Any], uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Die Vorgabe ändert am bisherigen Verhalten nichts."""
        models_dict["tasks"]["embedding"]["primary"]["batch_size"] = 1
        fabrik, messend = self._messende_clients(DIM)

        router(settings, ModelsConfig.model_validate(models_dict), fabrik, uow=uow).embed(
            defaults.TASK_EMBEDDING, [f"text {i}" for i in range(6)], store="shared"
        )

        assert messend.aufrufe == 6
        assert messend.hoechststand == 1

    def test_mit_max_concurrency_ueberlappen_sich_die_aufrufe(
        self, settings: Settings, models_dict: dict[str, Any], uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Gemessen, nicht behauptet: Der Zähler sieht mehr als einen Aufruf gleichzeitig."""
        models_dict["providers"]["gemini"]["max_concurrency"] = 3
        models_dict["tasks"]["embedding"]["primary"]["batch_size"] = 1
        fabrik, messend = self._messende_clients(DIM)

        router(settings, ModelsConfig.model_validate(models_dict), fabrik, uow=uow).embed(
            defaults.TASK_EMBEDDING, [f"text {i}" for i in range(6)], store="shared"
        )

        assert messend.aufrufe == 6
        assert messend.hoechststand > 1

    def test_die_reihenfolge_der_vektoren_bleibt_die_der_texte(
        self, settings: Settings, models_dict: dict[str, Any], uow: MemoryUnitOfWorkFactory
    ) -> None:
        """Der Punkt, an dem Parallelität still falsch würde.

        Vektor und Text sind über die Position verbunden, sonst nichts. Kämen die Bündel in der
        Reihenfolge ihrer Antworten zurück, bekäme jedes Konzept das Embedding eines anderen —
        und der Graph wäre falsch, ohne dass irgendetwas fehlschlägt.
        """
        models_dict["providers"]["gemini"]["max_concurrency"] = 4
        models_dict["tasks"]["embedding"]["primary"]["batch_size"] = 1
        texte = [f"ganz eigener text {i}" for i in range(8)]
        erlaubt = ModelsConfig.model_validate(models_dict)

        parallel = router(settings, erlaubt, FakeClients(dim=DIM), uow=uow).embed(
            defaults.TASK_EMBEDDING, texte, store="shared"
        )
        models_dict["providers"]["gemini"]["max_concurrency"] = 1
        seriell = router(
            settings, ModelsConfig.model_validate(models_dict), FakeClients(dim=DIM), uow=uow
        ).embed(defaults.TASK_EMBEDDING, texte, store="shared")

        assert parallel.vectors == seriell.vectors

    def test_der_budgetzaehler_verliert_unter_last_keinen_aufruf(
        self, settings: Settings, models_dict: dict[str, Any], uow: MemoryUnitOfWorkFactory
    ) -> None:
        """``+=`` ist nicht unteilbar; ein verlorener Aufruf wäre ein Loch im Wächter (§11.6)."""
        models_dict["providers"]["gemini"]["max_concurrency"] = 8
        models_dict["tasks"]["embedding"]["primary"]["batch_size"] = 1

        router(
            settings, ModelsConfig.model_validate(models_dict), FakeClients(dim=DIM), uow=uow
        ).embed(defaults.TASK_EMBEDDING, [f"text {i}" for i in range(40)], store="shared")

        assert len(uow.state("shared").model_calls) == 40
