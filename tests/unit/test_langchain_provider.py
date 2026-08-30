"""Die LangChain-Anbieter und der Antwort-Cache (§11.4, §11.6).

Kein Test hier stellt eine Verbindung her. Geprüft wird die Übersetzung: Was aus einer Route in
den Parametern des LangChain-Objekts landet, und was aus dessen Antwort wieder herauskommt. Genau
dort sitzen die Fehler, die sonst erst beim ersten echten Aufruf auffielen — ein vergessener
``max_retries=0`` etwa wäre unsichtbar, bis eine Störung die Versuchszahlen multipliziert.
"""

from __future__ import annotations

from typing import Any

import pytest

from wissensgraph.config.models import ModelsConfig
from wissensgraph.infrastructure.models.cache import MemoryResponseCache, RedisResponseCache
from wissensgraph.infrastructure.models.langchain import (
    LangChainClients,
    ProviderUnavailableError,
)
from wissensgraph.ports.models import ModelError, PromptSpec

pytestmark = pytest.mark.unit


@pytest.fixture
def models() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "providers": {
                "gemini": {"type": "google_genai", "api_key": "test-schluessel"},
                "gemini_ohne": {"type": "google_genai"},
                "vertex": {"type": "vertex", "project": "mein-projekt", "location": "europe-west4"},
                "vertex_ohne": {"type": "vertex"},
                "ollama": {
                    "type": "openai_compatible",
                    "base_url": "http://localhost:11434/v1",
                    "local": True,
                },
            },
            "tasks": {
                "summarization": {
                    "primary": {
                        "provider": "gemini",
                        "model": "gemini-3.5-flash-lite",
                        "temperature": 0.3,
                        "max_tokens": 200,
                    }
                },
                "relation_extraction": {
                    "primary": {
                        "provider": "gemini",
                        "model": "gemini-3.5-flash-lite",
                        "temperature": 0.0,
                        "json_mode": True,
                    }
                },
                "embedding": {
                    "primary": {"provider": "gemini", "model": "gemini-embedding-2", "dim": 768}
                },
                "query_expansion": {
                    "primary": {
                        "provider": "ollama",
                        "model": "llama3",
                        "temperature": 0.2,
                        "max_tokens": 120,
                        "json_mode": True,
                    }
                },
                "cluster_labeling": {
                    "primary": {"provider": "vertex", "model": "gemini-3.5-flash-lite"}
                },
            },
        }
    )


def modell(clients: LangChainClients, models: ModelsConfig, task: str) -> Any:
    """Das rohe LangChain-Objekt hinter der Hülle — nur so lassen sich Parameter prüfen."""
    return clients._bauen_chat(models.task(task).primary)


class TestAufbau:
    def test_google_bekommt_schluessel_modell_und_grenzen(self, models: ModelsConfig) -> None:
        gebaut = modell(LangChainClients(models), models, "summarization")

        assert gebaut.model.endswith("gemini-3.5-flash-lite")
        assert gebaut.temperature == 0.3
        assert gebaut.max_output_tokens == 200

    def test_der_router_wiederholt_selbst_also_das_sdk_nicht(self, models: ModelsConfig) -> None:
        """Zwei Wiederholungsmechanismen übereinander ergäben das Produkt beider Versuchszahlen."""
        gebaut = modell(LangChainClients(models), models, "summarization")

        assert gebaut.max_retries == 0

    def test_json_modus_zwingt_zu_strukturierter_ausgabe(self, models: ModelsConfig) -> None:
        gebaut = modell(LangChainClients(models), models, "relation_extraction")

        assert gebaut.response_mime_type == "application/json"

    def test_openai_kompatible_anbieter_bekommen_die_basis_url(self, models: ModelsConfig) -> None:
        """Ollama und vLLM brauchen keinen eigenen Codepfad — das ist der Punkt von §11.4."""
        gebaut = modell(LangChainClients(models), models, "query_expansion")

        assert str(gebaut.openai_api_base).startswith("http://localhost:11434")
        assert gebaut.max_retries == 0

    def test_vertex_wird_ueber_dieselbe_integration_bedient(self, models: ModelsConfig) -> None:
        """§11.7: 'Provider für dieselbe Modellfamilie -> kein erforderlicher Schritt'."""
        gebaut = modell(LangChainClients(models), models, "cluster_labeling")

        assert gebaut.project == "mein-projekt"
        assert gebaut.location == "europe-west4"

    def test_ohne_schluessel_gibt_es_eine_verstaendliche_meldung(
        self, models: ModelsConfig
    ) -> None:
        clients = LangChainClients(models)
        route = models.task("summarization").primary.model_copy(update={"provider": "gemini_ohne"})

        with pytest.raises(ProviderUnavailableError, match="WG_PROVIDER_GEMINI__API_KEY"):
            clients._bauen_chat(route)

    def test_vertex_ohne_projekt_meldet_das_fehlende_feld(self, models: ModelsConfig) -> None:
        clients = LangChainClients(models)
        route = models.task("cluster_labeling").primary.model_copy(
            update={"provider": "vertex_ohne"}
        )

        with pytest.raises(ProviderUnavailableError, match="project"):
            clients._bauen_chat(route)

    def test_embeddings_bekommen_die_geforderte_dimension(self, models: ModelsConfig) -> None:
        """``output_dimensionality`` ist der Grund, warum ein Modell ins Schema passt (§11.7)."""
        clients = LangChainClients(models)

        gebaut = clients._bauen_embeddings(models.task("embedding").primary)

        assert gebaut.output_dimensionality == 768

    def test_openai_kompatible_embeddings_heissen_das_feld_anders(
        self, models: ModelsConfig
    ) -> None:
        clients = LangChainClients(models)
        route = models.task("embedding").primary.model_copy(
            update={"provider": "ollama", "model": "nomic-embed-text"}
        )

        gebaut = clients._bauen_embeddings(route)

        assert gebaut.dimensions == 768

    def test_ein_client_wird_je_route_wiederverwendet(self, models: ModelsConfig) -> None:
        """Jedes neue Client-Objekt baut einen eigenen HTTP-Pool auf."""
        clients = LangChainClients(models)
        route = models.task("summarization").primary

        assert clients.chat("summarization", route) is clients.chat("summarization", route)

    def test_ein_falscher_typ_faellt_sofort_auf(self, models: ModelsConfig) -> None:
        with pytest.raises(TypeError, match="RouteConfig"):
            LangChainClients(models).chat("summarization", "keine Route")


class _Antwort:
    """Eine LangChain-Antwort, wie sie ``invoke`` liefert."""

    def __init__(self, content: Any, usage: dict[str, int] | None = None) -> None:
        self.content = content
        self.usage_metadata = usage


class _Modell:
    """Ein LangChain-Chatmodell, das nicht spricht, sondern zurückgibt."""

    def __init__(self, antwort: Any) -> None:
        self.antwort = antwort
        self.gesehen: list[Any] = []

    def invoke(self, nachrichten: Any) -> Any:
        self.gesehen.append(nachrichten)
        if isinstance(self.antwort, Exception):
            raise self.antwort
        return self.antwort


class TestHuellen:
    def _huelle(self, modell: Any) -> Any:
        from wissensgraph.infrastructure.models.langchain import _LangChainChat

        return _LangChainChat(modell)

    def test_system_und_nutzerteil_werden_getrennt_uebergeben(self) -> None:
        roh = _Modell(_Antwort("Antwort", {"input_tokens": 10, "output_tokens": 4}))

        self._huelle(roh).complete(PromptSpec(system="Sei knapp.", user="Frage?"))

        assert roh.gesehen[0] == [("system", "Sei knapp."), ("human", "Frage?")]

    def test_ohne_systemteil_geht_nur_die_frage_hinaus(self) -> None:
        roh = _Modell(_Antwort("Antwort"))

        self._huelle(roh).complete(PromptSpec(system=None, user="Frage?"))

        assert roh.gesehen[0] == [("human", "Frage?")]

    def test_token_werden_aus_den_metadaten_gelesen(self) -> None:
        roh = _Modell(_Antwort("Antwort", {"input_tokens": 10, "output_tokens": 4}))

        antwort = self._huelle(roh).complete(PromptSpec(system=None, user="x"))

        assert (antwort.tokens_in, antwort.tokens_out) == (10, 4)

    def test_fehlende_metadaten_sind_kein_fehler(self) -> None:
        """Nicht jeder Anbieter meldet Token — eine Null ist besser als ein Abbruch."""
        antwort = self._huelle(_Modell(_Antwort("Antwort"))).complete(
            PromptSpec(system=None, user="x")
        )

        assert (antwort.tokens_in, antwort.tokens_out) == (0, 0)

    def test_eine_antwort_aus_bloecken_wird_zusammengesetzt(self) -> None:
        """Sonst käme die Python-Darstellung einer Liste heraus — und damit ungültiges JSON."""
        roh = _Modell(
            _Antwort([{"type": "text", "text": '{"a":'}, {"type": "text", "text": " 1}"}])
        )

        antwort = self._huelle(roh).complete(PromptSpec(system=None, user="x"))

        assert antwort.text == '{"a": 1}'

    def test_jede_sdk_ausnahme_wird_zur_port_ausnahme(self) -> None:
        """Sonst müsste der Router die Ausnahmehierarchie jedes Anbieters kennen."""
        roh = _Modell(RuntimeError("Netz weg"))

        with pytest.raises(ModelError, match="Netz weg"):
            self._huelle(roh).complete(PromptSpec(system=None, user="x"))

    def test_embeddings_schaetzen_ihre_token(self) -> None:
        from wissensgraph.infrastructure.models.langchain import _LangChainEmbeddings

        class Roh:
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return [[0.1, 0.2] for _ in texts]

        antwort = _LangChainEmbeddings(Roh()).embed(["a" * 40, "b" * 40])

        assert len(antwort.vectors) == 2
        assert antwort.tokens_in == 20

    def test_ein_ausfall_beim_einbetten_wird_zur_port_ausnahme(self) -> None:
        from wissensgraph.infrastructure.models.langchain import _LangChainEmbeddings

        class Roh:
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise ConnectionError("nicht erreichbar")

        with pytest.raises(ModelError, match="nicht erreichbar"):
            _LangChainEmbeddings(Roh()).embed(["a"])


class TestCache:
    def test_der_speicher_cache_gibt_zurueck_was_er_bekam(self) -> None:
        cache = MemoryResponseCache()

        cache.set("k", "v", ttl_seconds=60)

        assert cache.get("k") == "v"
        assert len(cache) == 1

    def test_ein_unbekannter_schluessel_ist_kein_fehler(self) -> None:
        assert MemoryResponseCache().get("gibtsnicht") is None

    def test_ein_nicht_erreichbarer_redis_macht_langsamer_nicht_falsch(self) -> None:
        """Ein Lauf, der am Zwischenspeicher abbräche, hätte die Verhältnismäßigkeit verloren."""
        cache = RedisResponseCache("redis://127.0.0.1:59999/0")

        cache.set("k", "v", ttl_seconds=60)

        assert cache.get("k") is None

    def test_schliessen_ohne_verbindung_ist_folgenlos(self) -> None:
        RedisResponseCache("redis://127.0.0.1:59999/0").close()
