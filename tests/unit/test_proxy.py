"""Ein Proxy im Unternehmensnetz darf den internen Verkehr nicht abschneiden (§5.2).

Erreicht ein Container das Internet nur über einen Proxy, steht der als ``HTTP_PROXY`` in der
Umgebung. Jede Bibliothek, die ihre Umgebung liest — httpx tut das —, schickt dann **auch** den
Aufruf an den Nachbarcontainer dorthin. Der Proxy kennt ``mock-sources`` nicht, kann den Namen
nicht auflösen und antwortet mit einem Fehler, der wie ein Ausfall des Nachbarn aussieht.

Das ist der unangenehme Teil: Am Symptom ist die Ursache nicht zu erkennen. Wer den Fehler zum
ersten Mal sieht, sucht beim Nachbarn. Deshalb gibt es hier zwei Absicherungen — die Compose-Datei
setzt die internen Namen selbst, und ``wg doctor`` prüft die *tatsächliche* Umgebung, weil ein
Proxy auch von außerhalb von Compose gesetzt werden kann (etwa über einen Orchestrator).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wissensgraph.config.network import (
    bypasses_proxy,
    no_proxy_entries,
    proxy_configured,
)
from wissensgraph.config.schema import Settings
from wissensgraph.diagnostics import CheckStatus, check_proxy

pytestmark = pytest.mark.unit

WURZEL = Path(__file__).resolve().parents[2]
COMPOSE = WURZEL / "docker-compose.yml"

PROXY = {"HTTP_PROXY": "http://proxy.firma.de:3128"}


@pytest.fixture
def settings(minimal_config_dict: dict[str, Any]) -> Settings:
    """Zwei Stores auf Compose-Servicenamen und ein Broker — die Lage im Container."""
    return Settings.model_validate(
        {
            **minimal_config_dict,
            "stores": {
                "shared": {
                    "dsn": "postgresql+psycopg://wg:wg@db-shared:5432/wg_shared",
                    "allow_remote": True,
                },
                "personal": {
                    "dsn": "postgresql+psycopg://wg:wg@db-personal:5432/wg_personal",
                    "allow_remote": False,
                },
            },
            "broker_url": "redis://broker:6379/0",
        }
    )


class TestAusnahmeliste:
    """Die Regel, nach der ein Host am Proxy vorbeigeht."""

    def test_ein_genauer_treffer_geht_vorbei(self) -> None:
        assert bypasses_proxy("db-shared", ("db-shared", "localhost"))

    def test_gross_und_kleinschreibung_spielen_keine_rolle(self) -> None:
        assert bypasses_proxy("DB-Shared", no_proxy_entries({"NO_PROXY": "db-shared"}))

    def test_ein_eintrag_trifft_auch_als_suffix(self) -> None:
        assert bypasses_proxy("api.firma.de", ("firma.de",))

    def test_ein_fuehrender_punkt_bedeutet_dasselbe(self) -> None:
        assert bypasses_proxy("api.firma.de", (".firma.de",))

    def test_ein_teiltreffer_ohne_punktgrenze_zaehlt_nicht(self) -> None:
        """Sonst ginge 'boesefirma.de' an 'firma.de' vorbei — die Ausnahme wäre zu weit."""
        assert not bypasses_proxy("boesefirma.de", ("firma.de",))

    def test_der_stern_hebt_den_proxy_ganz_auf(self) -> None:
        assert bypasses_proxy("irgendwas.example.com", ("*",))

    def test_ein_unbekannter_host_geht_an_den_proxy(self) -> None:
        assert not bypasses_proxy("mock-sources", ("db-shared",))

    def test_beide_schreibweisen_werden_zusammengefuehrt(self) -> None:
        """Welche Variable eine Bibliothek liest, ist nicht einheitlich — also gelten beide."""
        eintraege = no_proxy_entries({"NO_PROXY": "db-shared", "no_proxy": "broker"})

        assert eintraege == ("broker", "db-shared")

    @pytest.mark.parametrize("name", ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"])
    def test_jede_schreibweise_gilt_als_gesetzter_proxy(self, name: str) -> None:
        assert proxy_configured({name: "http://proxy:3128"}) == "http://proxy:3128"

    def test_ein_leerer_wert_ist_kein_proxy(self) -> None:
        assert proxy_configured({"HTTP_PROXY": "   "}) is None


class TestProxypruefung:
    """``wg doctor`` prüft die Umgebung, in der der Prozess wirklich läuft."""

    def test_ohne_proxy_ist_nichts_zu_melden(self, settings: Settings) -> None:
        (ergebnis,) = check_proxy(settings, env={})

        assert ergebnis.status is CheckStatus.OK
        assert "Kein Proxy" in ergebnis.detail

    def test_ein_proxy_ohne_ausnahmen_ist_ein_fehler(self, settings: Settings) -> None:
        """Der gemeldete Fall: Der Proxy steht, die internen Namen fehlen."""
        (ergebnis,) = check_proxy(settings, env=PROXY)

        assert ergebnis.status is CheckStatus.FAIL
        assert "db-shared" in ergebnis.detail
        assert "broker" in ergebnis.detail

    def test_die_meldung_nennt_die_fehlenden_namen_zum_einsetzen(self, settings: Settings) -> None:
        """Eine Diagnose, die den Betroffenen suchen lässt, hat ihre Arbeit nicht getan."""
        (ergebnis,) = check_proxy(settings, env=PROXY)

        assert "In NO_PROXY aufnehmen:" in ergebnis.detail

    def test_vollstaendige_ausnahmen_sind_in_ordnung(self, settings: Settings) -> None:
        umgebung = {**PROXY, "NO_PROXY": "db-shared,db-personal,broker,mock-sources"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert ergebnis.status is CheckStatus.OK

    def test_eine_unvollstaendige_liste_faellt_auf(self, settings: Settings) -> None:
        """Der eigentliche Zweck: Ein vergessener Name fällt sonst erst im Betrieb auf."""
        umgebung = {**PROXY, "NO_PROXY": "db-shared,db-personal"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert ergebnis.status is CheckStatus.FAIL
        assert "broker" in ergebnis.detail

    def test_der_proxy_erscheint_maskiert(self, settings: Settings) -> None:
        """§20.2: Ein Proxy-URL kann Zugangsdaten enthalten und gehört maskiert in den Bericht."""
        umgebung = {"HTTP_PROXY": "http://benutzer:geheim@proxy.firma.de:3128"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert "geheim" not in str(ergebnis.context)
        assert "geheim" not in ergebnis.detail

    def test_die_geprueften_hosts_stehen_im_bericht(self, settings: Settings) -> None:
        (ergebnis,) = check_proxy(settings, env=PROXY)

        assert "db-shared" in ergebnis.context["interne_hosts"]
        assert "broker" in ergebnis.context["interne_hosts"]

    def test_der_lokale_modellserver_zaehlt_mit(self, settings: Settings, tmp_path: Path) -> None:
        """Leitprinzip 2: Ginge sein Verkehr über den Proxy, verließen persönliche Inhalte den
        Rechner — ein Anbieter mit ``local: true`` wäre dann keiner mehr."""
        datei = tmp_path / "models.yaml"
        datei.write_text(
            yaml.safe_dump(
                {
                    "providers": {
                        "ollama": {
                            "type": "openai_compatible",
                            "base_url": "http://host.docker.internal:11434/v1",
                            "local": True,
                        }
                    },
                    "tasks": {},
                }
            ),
            encoding="utf-8",
        )
        umgebung = {**PROXY, "NO_PROXY": "db-shared,db-personal,broker,mock-sources"}

        (ergebnis,) = check_proxy(settings, env=umgebung, models_path=datei)

        assert ergebnis.status is CheckStatus.FAIL
        assert "host.docker.internal" in ergebnis.detail


class TestGegenrichtung:
    """Die andere Hälfte der Bedingung: Was *durch* den Proxy muss (§5.2, §15.6).

    Beide Richtungen gleichzeitig richtig zu haben ist die eigentliche Vorbedingung im
    Unternehmensnetz. Der Nachbarcontainer muss am Proxy vorbei, sonst versucht dieser einen
    Compose-Dienstnamen aufzulösen. Das Quellsystem im Netz muss umgekehrt hindurch, sonst ist es
    von innen gar nicht erreichbar — und der Fehlschlag ist eine Zeitüberschreitung beim ersten
    Sync, also weit weg von seiner Ursache.
    """

    def externe_quelle(self, tmp_path: Path) -> Path:
        """Eine Quellkonfiguration mit einem Host außerhalb der Maschine."""
        datei = tmp_path / "sources.yaml"
        datei.write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {
                            "name": "jira-live",
                            "adapter": "jira",
                            "id_prefix": "jira",
                            "target": {"scope": "engineering", "default_type": "Jira Issue"},
                            "connection": {"base_url": "https://jira.example"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return datei

    def test_eine_externe_quelle_wird_nicht_in_no_proxy_verlangt(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sie dort zu verlangen hieße, genau den Weg zu sperren, auf dem sie erreichbar ist."""
        monkeypatch.setenv("WG_SOURCES_FILE", str(self.externe_quelle(tmp_path)))
        umgebung = {**PROXY, "NO_PROXY": "db-shared,db-personal,broker"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert ergebnis.status is CheckStatus.OK
        assert "jira.example" not in ergebnis.context["interne_hosts"]
        assert "jira.example" in ergebnis.context["externe_hosts"]

    def test_eine_externe_quelle_in_no_proxy_ist_eine_warnung(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keine Ablehnung: Es gibt Netze, in denen ein externer Host auch direkt erreichbar ist.
        Feststellen lässt sich das von hier aus nicht — der häufigere Fall ist ein zu weit
        gefasster NO_PROXY-Eintrag."""
        monkeypatch.setenv("WG_SOURCES_FILE", str(self.externe_quelle(tmp_path)))
        umgebung = {**PROXY, "NO_PROXY": "db-shared,db-personal,broker,jira.example"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert ergebnis.status is CheckStatus.WARN
        assert "jira.example" in ergebnis.detail

    def test_die_interne_richtung_wiegt_schwerer(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fehlen beide, wird der Fehler gemeldet und nicht die Warnung: Ein abgeschnittener
        interner Verkehr legt das System still, ein umgangener Proxy nur eine Quelle."""
        monkeypatch.setenv("WG_SOURCES_FILE", str(self.externe_quelle(tmp_path)))
        umgebung = {**PROXY, "NO_PROXY": "jira.example"}

        (ergebnis,) = check_proxy(settings, env=umgebung)

        assert ergebnis.status is CheckStatus.FAIL


class TestComposeVorgabe:
    """Die Compose-Datei nimmt dem Betreiber die Liste ab."""

    @staticmethod
    def _no_proxy() -> str:
        inhalt = COMPOSE.read_text(encoding="utf-8")
        for zeile in inhalt.splitlines():
            if zeile.strip().startswith("NO_PROXY:"):
                return zeile.split(":", 1)[1]
        raise AssertionError("Die Compose-Datei setzt kein NO_PROXY.")

    def test_jeder_dienst_steht_in_der_ausnahmeliste(self) -> None:
        """Der Test, der beim nächsten neuen Dienst anschlägt.

        Ein vergessener Name fällt sonst erst auf, wenn genau dieser Dienst angesprochen wird —
        und dann sieht der Fehler wie ein Ausfall des Nachbarn aus.
        """
        dienste = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
        eintraege = self._no_proxy()

        for name in dienste:
            assert name in eintraege, f"Dienst '{name}' fehlt in NO_PROXY."

    def test_der_lokale_modellserver_steht_dabei(self) -> None:
        """Er läuft auf dem Host und ist damit kein Dienst — vergessen wäre er trotzdem fatal."""
        assert "host.docker.internal" in self._no_proxy()

    def test_eigene_ausnahmen_kommen_hinzu_statt_zu_ersetzen(self) -> None:
        """Sonst löschte der erste eigene Eintrag alle internen Namen."""
        assert "${WG_NO_PROXY:+" in self._no_proxy()

    def test_beide_schreibweisen_werden_gesetzt(self) -> None:
        inhalt = COMPOSE.read_text(encoding="utf-8")

        assert "no_proxy: *no-proxy" in inhalt
        assert "http_proxy: ${HTTP_PROXY:-}" in inhalt
