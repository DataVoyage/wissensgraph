"""Anbieter-Clients über LangChain (§11.4).

Hier — und nur hier — entsteht ein Modell-SDK. Der Router darüber kennt Aufgaben und Stores, die
Dienste darüber kennen nicht einmal das; was ein Anbieter für ein Paket benutzt, endet an dieser
Datei.

**Warum LangChain und nicht das SDK des Anbieters.** §11.1 verlangt, dass ein Modellwechsel eine
Änderung in ``models.yaml`` ist, "inklusive: anderer Anbieter". Eine eigene Hülle je Anbieter
könnte das auch leisten, aber jede neue Integration wäre neuer Code. Mit LangChain sind
``ChatGoogleGenerativeAI`` und ``ChatOpenAI`` zwei Zeilen in derselben Fallunterscheidung, und
``openai_compatible`` deckt Ollama und vLLM ohne eine weitere ab.

**Was LangChain hier ausdrücklich nicht tut.** Es routet nicht, es wiederholt nicht, es speichert
nicht zwischen und es validiert keine Struktur. All das steht in
:mod:`wissensgraph.services.router`, weil es dort protokolliert, budgetiert und geprüft werden
muss. Die Clients werden deshalb mit ``max_retries=0`` gebaut: Zwei Wiederholungsmechanismen
übereinander ergäben im ungünstigen Fall das Produkt beider Versuchszahlen, und keiner der
zusätzlichen Aufrufe stünde in ``model_calls``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from wissensgraph.config import defaults
from wissensgraph.config.models import ModelsConfig, ProviderConfig, RouteConfig
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import (
    ChatClient,
    EmbeddingClient,
    ModelError,
    PromptSpec,
    RawCompletion,
    RawEmbedding,
)

_log = get_logger(__name__)

#: Grobe Umrechnung von Zeichen in Token, wenn ein Anbieter keine Zahlen mitliefert. Sie ist
#: bewusst grob und wird nirgends als Tatsache ausgegeben: Der Budget-Wächter braucht eine
#: Größenordnung, keine Abrechnung. Vier Zeichen je Token ist der übliche Daumenwert für
#: europäische Sprachen.
_ZEICHEN_JE_TOKEN = 4


class ProviderUnavailableError(ModelError):
    """Ein Anbieter lässt sich nicht bauen — fehlender Schlüssel, fehlendes Paket, falscher Typ."""


class LangChainClients:
    """Die Fabrik aus §11.4: zu einer Route der passende LangChain-Client.

    Gebaute Clients werden je Route zwischengespeichert. Das ist keine Mikrooptimierung: Ein
    Embedding-Lauf über tausend Konzepte ruft die Fabrik je Bündel auf, und jedes neue
    Client-Objekt baut einen eigenen HTTP-Pool auf.
    """

    def __init__(self, models: ModelsConfig) -> None:
        self._models = models
        self._chat: dict[str, ChatClient] = {}
        self._embeddings: dict[str, EmbeddingClient] = {}

    def chat(self, task: str, route: object) -> ChatClient:
        """Ein generatives Modell für diese Route."""
        strecke = _als_route(route)
        schluessel = f"{task}\x1e{strecke.model_key}"
        if schluessel not in self._chat:
            self._chat[schluessel] = _LangChainChat(self._bauen_chat(strecke))
        return self._chat[schluessel]

    def embeddings(self, task: str, route: object) -> EmbeddingClient:
        """Ein Embedding-Modell für diese Route."""
        strecke = _als_route(route)
        schluessel = f"{task}\x1e{strecke.model_key}"
        if schluessel not in self._embeddings:
            self._embeddings[schluessel] = _LangChainEmbeddings(self._bauen_embeddings(strecke))
        return self._embeddings[schluessel]

    # -- Aufbau ------------------------------------------------------------------

    def _bauen_chat(self, route: RouteConfig) -> Any:
        """Baut das LangChain-Chatmodell einer Route."""
        provider = self._models.provider(route.provider)
        gemeinsam: dict[str, Any] = {
            "model": route.model,
            "timeout": self._models.defaults.timeout_seconds,
            # Der Router wiederholt selbst und protokolliert dabei jeden Versuch (§11.6).
            "max_retries": 0,
        }
        if route.temperature is not None:
            gemeinsam["temperature"] = route.temperature

        if provider.type == defaults.PROVIDER_TYPE_OPENAI_COMPATIBLE:
            from langchain_openai import ChatOpenAI

            if route.max_tokens is not None:
                gemeinsam["max_tokens"] = route.max_tokens
            if route.json_mode:
                gemeinsam["model_kwargs"] = {"response_format": {"type": "json_object"}}
            return ChatOpenAI(
                base_url=provider.base_url,
                # Ein lokaler Server verlangt keinen Schlüssel, das OpenAI-SDK aber einen Wert.
                api_key=SecretStr(provider.api_key or "not-needed"),
                **gemeinsam,
                **provider.options,
            )

        from langchain_google_genai import ChatGoogleGenerativeAI

        if route.max_tokens is not None:
            gemeinsam["max_output_tokens"] = route.max_tokens
        if route.json_mode:
            gemeinsam["response_mime_type"] = "application/json"
        return ChatGoogleGenerativeAI(**gemeinsam, **_google_zugang(provider), **provider.options)

    def _bauen_embeddings(self, route: RouteConfig) -> Any:
        """Baut das LangChain-Embeddingmodell einer Route."""
        provider = self._models.provider(route.provider)

        if provider.type == defaults.PROVIDER_TYPE_OPENAI_COMPATIBLE:
            from langchain_openai import OpenAIEmbeddings

            zusatz: dict[str, Any] = {}
            if route.dim is not None:
                zusatz["dimensions"] = route.dim
            return OpenAIEmbeddings(
                model=route.model,
                base_url=provider.base_url,
                api_key=SecretStr(provider.api_key or "not-needed"),
                **zusatz,
                **provider.options,
            )

        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        # 'output_dimensionality' ist der Grund, warum ein Modell mit anderer nativer Dimension
        # trotzdem in das mit WG_EMBEDDING_DIM migrierte Schema passt (§11.7). Ohne diese Angabe
        # bliebe als einziger Weg eine neue Migration samt vollständigem Neuaufbau.
        return GoogleGenerativeAIEmbeddings(
            model=route.model,
            output_dimensionality=route.dim,
            **_google_zugang(provider),
            **provider.options,
        )


def _google_zugang(provider: ProviderConfig) -> dict[str, Any]:
    """Die Zugangsparameter der Google-Integration — Studio-Schlüssel oder Vertex-Projekt.

    Beide Betriebsarten liegen in derselben LangChain-Integration; der Unterschied ist ein Flag.
    Deshalb gibt es hier auch keinen zweiten Codepfad für ``vertex``: Der Wechsel von der
    Developer-API auf Vertex ist genau das, was §11.7 verspricht — "Provider für dieselbe
    Modellfamilie: kein erforderlicher Schritt".

    Den Endpunkt bildet die Google-Bibliothek aus ``location``: ``global`` und die Mehrregionen
    ``eu``/``us`` haben je einen eigenen Hostnamen, jede andere Angabe gilt als Region. Der
    Standort wird deshalb immer mitgegeben und nicht nur, wenn er gesetzt ist — ohne ihn entstünde
    ein Hostname aus einer fehlenden Angabe.
    """
    if provider.type == defaults.PROVIDER_TYPE_VERTEX:
        if not provider.project:
            raise ProviderUnavailableError(
                "Ein Provider vom Typ 'vertex' braucht 'project' (WG_PROVIDER_VERTEX__PROJECT)."
            )
        if not provider.location:
            raise ProviderUnavailableError(
                "Ein Provider vom Typ 'vertex' braucht 'location' "
                "(WG_PROVIDER_VERTEX__LOCATION). Zulässig sind eine Region wie 'europe-west4', "
                f"eine Mehrregion ({', '.join(defaults.VERTEX_MULTI_REGIONS)}) oder "
                f"'{defaults.VERTEX_GLOBAL_LOCATION}'. Der Standort ist keine Feinabstimmung: "
                "Aus ihm folgt der Endpunkt und damit der Ort der Verarbeitung."
            )
        zugang: dict[str, Any] = {
            "vertexai": True,
            "project": provider.project,
            "location": provider.location,
        }
        if provider.credentials_file:
            zugang["credentials"] = _dienstkonto(provider.credentials_file)
        return zugang

    if not provider.api_key:
        raise ProviderUnavailableError(
            "Für die Gemini-Developer-API fehlt der Zugangsschlüssel. Er gehört in die "
            "git-ignorierte .env unter WG_PROVIDER_GEMINI__API_KEY und niemals in eine "
            "Config-Datei (§20.2)."
        )
    return {"google_api_key": provider.api_key}


def _dienstkonto(pfad: str) -> Any:
    """Lädt einen Dienstkonto-Schlüssel von der Platte (§11.4, Ablage unter ``./secrets``).

    Der Scope wird **mitgegeben** und ist der Grund, warum diese Funktion mehr ist als ein
    Einzeiler: Ein ohne Scope geladener Schlüssel ist ein gültiges Objekt, das erst bei der ersten
    Tokenanforderung scheitert — also im ersten echten Lauf und nicht beim Start. Die
    Google-Bibliothek ergänzt den Scope nur auf ihrem eigenen Weg über die Standard-Anmeldung der
    Umgebung; übergebene Zugangsdaten reicht sie unverändert weiter.

    Der Pfad wird relativ zum Arbeitsverzeichnis aufgelöst. ``./secrets/vertex-sa.json`` trifft
    deshalb auf dem Host wie im Container dieselbe Datei: Compose bindet ``./secrets`` nach
    ``/app/secrets`` ein, und ``/app`` ist dort das Arbeitsverzeichnis.
    """
    datei = Path(pfad)
    if not datei.is_file():
        raise ProviderUnavailableError(
            f"Der Dienstkonto-Schlüssel '{pfad}' wurde nicht gefunden "
            f"(gesucht unter '{datei.resolve()}'). Erwartet wird eine JSON-Schlüsseldatei; im "
            "Container liegt sie unter '/app/secrets', weil Compose './secrets' dorthin "
            "einbindet. Ohne 'credentials_file' gilt die Standard-Anmeldung der Umgebung."
        )
    try:
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover — nur ohne installiertes google-auth
        raise ProviderUnavailableError(
            "Für 'credentials_file' wird 'google-auth' benötigt; ohne die Datei genügt die "
            "Standard-Anmeldung der Umgebung."
        ) from exc
    lader: Any = service_account.Credentials.from_service_account_file
    try:
        return lader(str(datei), scopes=[defaults.GOOGLE_CLOUD_SCOPE])
    except (ValueError, KeyError) as exc:
        raise ProviderUnavailableError(
            f"Der Dienstkonto-Schlüssel '{pfad}' ließ sich nicht lesen: "
            f"{str(exc).rstrip('.')}. Erwartet wird die unveränderte JSON-Datei, die Google beim "
            "Anlegen des Schlüssels ausgibt."
        ) from exc


class _LangChainChat:
    """Hüllt ein LangChain-Chatmodell in den schmalen Port aus §11.2."""

    def __init__(self, modell: Any) -> None:
        self._modell = modell

    def complete(self, prompt: PromptSpec) -> RawCompletion:
        """Ruft das Modell auf und liest Text und Token-Verbrauch ab."""
        nachrichten: list[tuple[str, str]] = []
        if prompt.system:
            nachrichten.append(("system", prompt.system))
        nachrichten.append(("human", prompt.user))

        try:
            antwort = self._modell.invoke(nachrichten)
        except Exception as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        verbrauch = getattr(antwort, "usage_metadata", None) or {}
        return RawCompletion(
            text=_text_von(antwort),
            tokens_in=int(verbrauch.get("input_tokens", 0)),
            tokens_out=int(verbrauch.get("output_tokens", 0)),
        )


class _LangChainEmbeddings:
    """Hüllt ein LangChain-Embeddingmodell in den schmalen Port aus §11.2."""

    def __init__(self, modell: Any) -> None:
        self._modell = modell

    def embed(self, texts: Sequence[str]) -> RawEmbedding:
        """Bettet einen Stapel Texte ein.

        Die Token-Zahl ist geschätzt: Kein Embedding-Anbieter meldet sie über die
        LangChain-Schnittstelle zurück. Sie geht ausschließlich in den Budget-Wächter, und der
        braucht eine Größenordnung — eine geschätzte Zahl ist dort besser als eine fehlende, die
        jeden Embedding-Lauf für den Wächter kostenlos aussehen ließe.
        """
        try:
            vektoren = self._modell.embed_documents(list(texts))
        except Exception as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        return RawEmbedding(
            vectors=tuple(tuple(float(zahl) for zahl in vektor) for vektor in vektoren),
            tokens_in=sum(len(text) for text in texts) // _ZEICHEN_JE_TOKEN,
        )


def _text_von(antwort: Any) -> str:
    """Der Textinhalt einer LangChain-Antwort, auch wenn sie aus mehreren Blöcken besteht.

    Neuere Modelle liefern ``content`` als Liste von Blöcken statt als String — etwa wenn ein
    Denkschritt mitgeschickt wird. Ein Aufrufer, der ``str(antwort.content)`` nähme, bekäme dann
    die Python-Repräsentation einer Liste und wunderte sich über ungültiges JSON.
    """
    inhalt = getattr(antwort, "content", "")
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        teile = [
            block.get("text", "") if isinstance(block, dict) else str(block) for block in inhalt
        ]
        return "".join(teile)
    return str(inhalt)  # pragma: no cover — kein bekannter Anbieter liefert etwas anderes


def _als_route(route: object) -> RouteConfig:
    """Engt den Port-Typ auf die Konfiguration ein, die diese Fabrik wirklich braucht."""
    if not isinstance(route, RouteConfig):  # pragma: no cover — nur bei Programmierfehler
        raise TypeError(f"Erwartet wurde eine RouteConfig, erhalten: {type(route).__name__}.")
    return route


__all__ = ["LangChainClients", "ProviderUnavailableError"]
