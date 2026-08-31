"""Vertex AI als Anbieter des Betriebs: Standort, Endpunkt und Dienstkonto (§11.4).

Diese Datei prüft die drei Dinge, die zwischen der Entwicklungsumgebung (ein API-Schlüssel für die
Gemini-Developer-API) und dem Betrieb (ein GCP-Projekt, ein Mehrregion-Endpunkt, ein
Dienstkonto-Schlüssel) tatsächlich verschieden sind. Alle drei haben gemeinsam, dass ihr Fehler
sich **nicht von selbst meldet**:

* Ein Dienstkonto-Schlüssel ohne OAuth-Scope ist ein gültiges Objekt. Es scheitert erst bei der
  ersten Tokenanforderung — also im ersten echten Lauf, mitten in der Nacht, nach dem Deployment.
  Die Google-Bibliothek ergänzt den Scope nur auf ihrem eigenen Weg über die Standard-Anmeldung
  der Umgebung; übergebene Zugangsdaten reicht sie unverändert weiter.
* Ein Tippfehler im Standort erzeugt keine Fehlermeldung, sondern einen anderen Hostnamen. ``eu``
  und ``europe-west4`` sind beide gültig und bedeuten verschiedene Orte der Verarbeitung.
* Ein fehlender Standort ergäbe einen Hostnamen aus einer leeren Angabe.

Keiner dieser Tests stellt eine Verbindung her. Geprüft wird, was an das SDK übergeben wird — der
Schlüssel in den Fixtures ist echt erzeugt, aber wertlos: Er gehört zu keinem Google-Konto.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from wissensgraph.config import defaults
from wissensgraph.config.models import ModelsConfig
from wissensgraph.infrastructure.models.langchain import (
    LangChainClients,
    ProviderUnavailableError,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def schluesselinhalt() -> str:
    """Ein syntaktisch vollwertiger Dienstkonto-Schlüssel ohne jede Berechtigung.

    Er muss echt erzeugt sein, weil ``google-auth`` den privaten Schlüssel beim Laden entschlüsselt
    — ein Platzhaltertext scheitert dort und würde am eigentlichen Prüfgegenstand vorbeigehen.
    Modulweit, weil das Erzeugen eines RSA-Schlüssels spürbar Zeit kostet.
    """
    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "mein-projekt",
            "private_key_id": "abc",
            "private_key": pem,
            "client_email": "wissensgraph@mein-projekt.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture
def schluesseldatei(tmp_path: Path, schluesselinhalt: str) -> Path:
    datei = tmp_path / "vertex-sa.json"
    datei.write_text(schluesselinhalt, encoding="utf-8")
    return datei


def _models(**vertex: Any) -> ModelsConfig:
    """Eine Konfiguration mit genau einem Vertex-Anbieter und einer Aufgabe darauf."""
    return ModelsConfig.model_validate(
        {
            "providers": {"vertex": {"type": "vertex", **vertex}},
            "tasks": {
                "cluster_labeling": {
                    "primary": {"provider": "vertex", "model": "gemini-3.5-flash-lite"}
                }
            },
        }
    )


def _bauen(models: ModelsConfig) -> Any:
    return LangChainClients(models)._bauen_chat(models.task("cluster_labeling").primary)


class TestStandortUndEndpunkt:
    """Aus dem Standort folgt der Ort der Verarbeitung — er darf nicht geraten werden."""

    @pytest.mark.parametrize(
        ("location", "erwartet"),
        [
            ("europe-west4", "europe-west4-aiplatform.googleapis.com"),
            ("us-central1", "us-central1-aiplatform.googleapis.com"),
            ("eu", "aiplatform.eu.rep.googleapis.com"),
            ("us", "aiplatform.us.rep.googleapis.com"),
            ("global", "aiplatform.googleapis.com"),
        ],
    )
    def test_der_endpunkt_folgt_dem_standort(self, location: str, erwartet: str) -> None:
        """Die Ableitung bildet die Regel der Google-Bibliothek nach und macht sie sichtbar."""
        provider = _models(project="p", location=location).provider("vertex")

        assert provider.endpoint == erwartet

    def test_eine_mehrregion_ist_etwas_anderes_als_eine_region(self) -> None:
        """'eu' und 'europe-west4' sind beide gültig und führen an verschiedene Orte."""
        mehrregion = _models(project="p", location="eu").provider("vertex")
        region = _models(project="p", location="europe-west4").provider("vertex")

        assert mehrregion.endpoint != region.endpoint

    def test_nur_vertex_hat_ueberhaupt_einen_endpunkt(self) -> None:
        models = ModelsConfig.model_validate(
            {"providers": {"g": {"type": "google_genai", "api_key": "k"}}, "tasks": {}}
        )

        assert models.provider("g").endpoint is None

    def test_ohne_standort_gibt_es_keinen_endpunkt(self) -> None:
        assert _models(project="p").provider("vertex").endpoint is None

    def test_der_standort_wird_immer_uebergeben(self) -> None:
        """Und nicht nur, wenn er gesetzt ist: Sonst entstünde ein Host aus einer Leerstelle."""
        gebaut = _bauen(_models(project="mein-projekt", location="eu"))

        assert gebaut.project == "mein-projekt"
        assert gebaut.location == "eu"
        assert gebaut.vertexai is True

    def test_ein_fehlender_standort_bricht_verstaendlich_ab(self) -> None:
        with pytest.raises(ProviderUnavailableError, match="WG_PROVIDER_VERTEX__LOCATION"):
            _bauen(_models(project="mein-projekt"))

    def test_ohne_standort_gilt_der_anbieter_als_unvollstaendig(self) -> None:
        """``is_configured`` trägt ``wg models describe`` — es darf nicht zu früh 'ja' sagen."""
        assert _models(project="p", location="eu").provider("vertex").is_configured
        assert not _models(project="p").provider("vertex").is_configured
        assert not _models(location="eu").provider("vertex").is_configured


class TestDienstkonto:
    """Der Schlüssel aus einer Datei — der Weg außerhalb von Google-Infrastruktur."""

    def test_der_schluessel_bekommt_den_noetigen_scope(self, schluesseldatei: Path) -> None:
        """Der Kern dieser Datei.

        Ohne Scope meldet ``google-auth`` ``requires_scopes`` und die Zugangsdaten scheitern erst
        bei der ersten Tokenanforderung. Der Fehler läge damit nicht im Start, sondern im ersten
        Lauf — und dort sieht er aus wie ein Netzproblem.
        """
        gebaut = _bauen(_models(project="p", location="eu", credentials_file=str(schluesseldatei)))

        assert gebaut.credentials is not None
        assert not gebaut.credentials.requires_scopes
        assert defaults.GOOGLE_CLOUD_SCOPE in gebaut.credentials.scopes

    def test_ohne_schluesseldatei_bleiben_die_zugangsdaten_offen(self) -> None:
        """Dann gilt die Standard-Anmeldung der Umgebung — auf GCP der Regelfall."""
        gebaut = _bauen(_models(project="p", location="eu"))

        assert gebaut.credentials is None

    def test_eine_fehlende_datei_nennt_den_gesuchten_pfad(self, tmp_path: Path) -> None:
        fehlt = tmp_path / "gibtsnicht.json"

        with pytest.raises(ProviderUnavailableError, match="nicht gefunden"):
            _bauen(_models(project="p", location="eu", credentials_file=str(fehlt)))

    def test_eine_unlesbare_datei_sagt_das_offen(self, tmp_path: Path) -> None:
        """Ein abgeschnittener oder umformatierter Schlüssel ist der häufigste Kopierfehler."""
        kaputt = tmp_path / "kaputt.json"
        kaputt.write_text('{"type": "service_account"}', encoding="utf-8")

        with pytest.raises(ProviderUnavailableError, match="sich nicht lesen"):
            _bauen(_models(project="p", location="eu", credentials_file=str(kaputt)))

    def test_auch_embeddings_bekommen_den_zugang(self, schluesseldatei: Path) -> None:
        """Zwei Aufbaupfade, eine Zugangsquelle — sonst driftet einer von beiden ab."""
        models = ModelsConfig.model_validate(
            {
                "providers": {
                    "vertex": {
                        "type": "vertex",
                        "project": "p",
                        "location": "eu",
                        "credentials_file": str(schluesseldatei),
                    }
                },
                "tasks": {
                    "embedding": {
                        "primary": {
                            "provider": "vertex",
                            "model": "gemini-embedding-2",
                            "dim": 768,
                        }
                    }
                },
            }
        )

        gebaut = LangChainClients(models)._bauen_embeddings(models.task("embedding").primary)

        assert gebaut.location == "eu"
        assert gebaut.output_dimensionality == 768
        assert not gebaut.credentials.requires_scopes
