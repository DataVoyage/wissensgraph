"""Herkunft der Images und Pakete ist umschaltbar (§5.3).

In einer abgeschlossenen Umgebung gibt es keinen Weg zu Docker Hub, ghcr.io, PyPI oder npm. Der
Stack muss dort aus einer eigenen Registry und einem eigenen Paketindex bauen — und das ist eine
Eigenschaft, die still verloren geht: Wer später einen Dienst ergänzt und ``image: redis:7`` fest
hinschreibt, bricht sie, ohne dass ein Test rot wird. In der Entwicklung fällt es nie auf, weil
dort der öffentliche Weg offen ist.

Deshalb prüfen diese Tests die Bau- und Compose-Dateien als Text. Sie starten nichts und bauen
nichts; sie halten eine Zusage fest, die man einer laufenden Umgebung nicht ansieht.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WURZEL = Path(__file__).resolve().parents[2]
COMPOSE = WURZEL / "docker-compose.yml"
API_DOCKERFILE = WURZEL / "docker" / "api.Dockerfile"
UI_DOCKERFILE = WURZEL / "docker" / "ui.Dockerfile"
ENV_BEISPIEL = WURZEL / ".env.example"

#: Die Variable, die jedem Image von Docker Hub vorangestellt wird.
REGISTRY = "${WG_DOCKER_REGISTRY:-}"


def _compose() -> dict:
    """Die Compose-Datei als Datenstruktur — ohne Auflösung der Variablen."""
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _image_zeilen(text: str) -> list[str]:
    return [zeile.strip() for zeile in text.splitlines() if zeile.strip().startswith("image:")]


class TestComposeImages:
    def test_jedes_fremde_image_traegt_das_registry_praefix(self) -> None:
        """Sonst zieht genau dieser eine Dienst weiter von Docker Hub."""
        zeilen = _image_zeilen(COMPOSE.read_text(encoding="utf-8"))
        fremd = [zeile for zeile in zeilen if "wissensgraph-" not in zeile]

        assert fremd, "Ohne fremde Images prüfte dieser Test nichts."
        for zeile in fremd:
            assert REGISTRY in zeile, f"Image ohne Registry-Präfix: {zeile}"

    def test_die_eigenen_images_sind_benennbar(self) -> None:
        """Die andere Richtung: Wohin das selbst Gebaute gehört (§5.3)."""
        zeilen = _image_zeilen(COMPOSE.read_text(encoding="utf-8"))
        eigen = [zeile for zeile in zeilen if "wissensgraph-" in zeile]

        assert len(eigen) == 2, "Erwartet werden genau das Anwendungs- und das UI-Image."
        for zeile in eigen:
            assert "${WG_IMAGE_PREFIX:-}" in zeile
            assert "${WG_IMAGE_TAG:-local}" in zeile

    def test_ohne_gesetzte_variablen_bleibt_der_oeffentliche_weg(self) -> None:
        """Die Vorgabe muss die sein, die ohne jede Einrichtung funktioniert.

        Geprüft wird die Ersetzungsregel selbst: ``${NAME:-}`` liefert ohne Wert die leere
        Zeichenkette, das Image heißt dann unverändert wie auf Docker Hub.
        """
        zeilen = _image_zeilen(COMPOSE.read_text(encoding="utf-8"))

        for zeile in zeilen:
            for platzhalter in re.findall(r"\$\{[^}]+\}", zeile):
                assert ":-" in platzhalter, (
                    f"{platzhalter} hat keinen Rückfallwert — ohne .env bräche der Start."
                )

    def test_alle_bauenden_dienste_reichen_die_bauargumente_durch(self) -> None:
        """Ein Dienst, der sie nicht bekommt, baut gegen die öffentlichen Quellen weiter."""
        dienste = _compose()["services"]
        bauend = {
            name: dienst
            for name, dienst in dienste.items()
            if isinstance(dienst.get("build"), dict)
        }

        assert bauend, "Ohne bauende Dienste prüfte dieser Test nichts."
        for name, dienst in bauend.items():
            args = dienst["build"].get("args")
            assert args, f"Dienst '{name}' reicht keine Bauargumente durch."
            assert "WG_DOCKER_REGISTRY" in args, f"Dienst '{name}' ohne WG_DOCKER_REGISTRY."


class TestDockerfiles:
    def test_die_basis_images_sind_umschaltbar(self) -> None:
        for datei in (API_DOCKERFILE, UI_DOCKERFILE):
            inhalt = datei.read_text(encoding="utf-8")
            froms = [zeile for zeile in inhalt.splitlines() if zeile.startswith("FROM ")]
            assert froms, f"{datei.name} hat keine FROM-Zeile."
            for zeile in froms:
                assert "${" in zeile, f"Festes Basis-Image in {datei.name}: {zeile}"

    def test_das_uv_image_hat_einen_eigenen_schalter(self) -> None:
        """'ghcr.io' ist in einem Artifactory ein anderes Remote-Repository als 'docker.io'."""
        inhalt = API_DOCKERFILE.read_text(encoding="utf-8")

        assert "ARG WG_UV_IMAGE=ghcr.io/astral-sh/uv:" in inhalt
        assert "FROM ${WG_UV_IMAGE} AS uv" in inhalt

    def test_der_paketindex_ist_ein_bauargument(self) -> None:
        inhalt = API_DOCKERFILE.read_text(encoding="utf-8")

        assert "ARG UV_DEFAULT_INDEX=" in inhalt
        assert "ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX}" in inhalt

    def test_der_npm_spiegel_ist_ein_bauargument(self) -> None:
        inhalt = UI_DOCKERFILE.read_text(encoding="utf-8")

        assert "ARG NPM_CONFIG_REGISTRY=" in inhalt
        assert "ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}" in inhalt

    @pytest.mark.parametrize(
        ("datei", "secret"),
        [(API_DOCKERFILE, "netrc"), (UI_DOCKERFILE, "npmrc")],
    )
    def test_zugangsdaten_kommen_als_secret_und_nicht_als_argument(
        self, datei: Path, secret: str
    ) -> None:
        """§20.2: Ein Build-Argument steht in der Image-Historie und wäre damit lesbar."""
        inhalt = datei.read_text(encoding="utf-8")

        assert f"--mount=type=secret,id={secret}" in inhalt
        for verboten in ("ARG UV_INDEX_", "ARG NPM_TOKEN", "ARG UV_PASSWORD"):
            assert verboten not in inhalt, f"Zugangsdaten als Bauargument in {datei.name}."


class TestDokumentation:
    def test_die_einschraenkung_der_sperrdatei_ist_benannt(self) -> None:
        """Die eine Sache, die man nicht raten kann.

        ``UV_DEFAULT_INDEX`` steuert die Auflösung, nicht die Installation: ``uv sync --frozen``
        lädt von den absoluten Adressen in ``uv.lock``. Wer das nicht weiß, setzt die Variable,
        sieht einen erfolgreichen Build und glaubt, hinter der Firewall zu bauen — dabei hing der
        Bau weiterhin am öffentlichen Netz. Nachgemessen, nicht vermutet.
        """
        for datei in (ENV_BEISPIEL, API_DOCKERFILE, WURZEL / "README.md"):
            inhalt = datei.read_text(encoding="utf-8")
            assert "dev.py lock" in inhalt, (
                f"{datei.name} nennt den nötigen Schritt zum Neuerzeugen der Sperrdatei nicht."
            )


class TestEigeneZertifizierungsstellen:
    """Eigene CA-Zertifikate lassen sich optional einbauen (Unternehmensnetz mit TLS-Inspektion).

    Der Mechanismus ist bewusst dateibasiert: Wer ``.crt``-Dateien nach
    ``docker/ca-certificates/`` legt, hat alles getan. Genau deshalb braucht er einen Wächter —
    an einem Bauargument fiele ein Wegfall sofort auf, an einem stillen ``if`` in einem
    Dockerfile nicht. Und die Reihenfolge ist keine Kosmetik: Greift die Inspektion schon beim
    Herunterladen der Abhängigkeiten, kommt ein später installiertes Zertifikat zu spät.
    """

    ABLAGE = WURZEL / "docker" / "ca-certificates"

    def test_das_verzeichnis_existiert_mit_anleitung(self) -> None:
        """Ohne die Datei schlüge der COPY im Dockerfile auf einem frischen Klon fehl."""
        assert (self.ABLAGE / "README.md").is_file()

    def test_kein_zertifikat_liegt_im_repository(self) -> None:
        """Das Repository ist öffentlich; die Ausstellerkette einer Firma gehört nicht hinein."""
        gefunden = [
            pfad.name
            for pfad in self.ABLAGE.iterdir()
            if pfad.suffix.lower() in {".crt", ".pem", ".cer", ".der", ".key"}
        ]

        assert gefunden == [], (
            f"Zertifikatsdateien in {self.ABLAGE.name}: {gefunden}. Sie sind über .gitignore "
            f"ausgeschlossen — dieser Test schlägt an, falls jemand die Regel umgeht."
        )

    def test_die_gitignore_schliesst_zertifikate_aus_und_haelt_den_readme(self) -> None:
        regeln = (WURZEL / ".gitignore").read_text(encoding="utf-8")

        assert "docker/ca-certificates/*" in regeln
        assert "!docker/ca-certificates/README.md" in regeln

    @pytest.mark.parametrize("datei", [API_DOCKERFILE, UI_DOCKERFILE])
    def test_beide_images_nehmen_die_ablage_auf(self, datei: Path) -> None:
        inhalt = datei.read_text(encoding="utf-8")

        assert "COPY docker/ca-certificates/ /usr/local/share/ca-certificates/" in inhalt
        assert "update-ca-certificates" in inhalt

    @staticmethod
    def _zeile_mit(datei: Path, marke: str) -> int:
        """Die erste **Anweisungs**zeile mit dieser Zeichenkette.

        Kommentare zählen nicht: Beide Dockerfiles erklären ``uv sync`` und ``npm ci`` ausführlich,
        bevor sie sie ausführen, und ein Vergleich über den rohen Text verglich deshalb eine
        Anweisung mit einer Erklärung.
        """
        for nummer, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines()):
            if not zeile.lstrip().startswith("#") and marke in zeile:
                return nummer
        raise AssertionError(f"'{marke}' kommt in {datei.name} als Anweisung nicht vor.")

    @pytest.mark.parametrize(
        ("datei", "installation"),
        [(API_DOCKERFILE, "uv sync"), (UI_DOCKERFILE, "npm ci")],
    )
    def test_die_zertifikate_kommen_vor_der_paketinstallation(
        self, datei: Path, installation: str
    ) -> None:
        """Die eine Eigenschaft, die man dem gebauten Image nicht ansieht."""
        assert self._zeile_mit(datei, "COPY docker/ca-certificates/") < self._zeile_mit(
            datei, installation
        )

    def test_python_bekommt_sie_zusaetzlich_ueber_certifi(self) -> None:
        """Der Systemspeicher allein genügt nicht.

        ``httpx``, das Gemini-SDK und praktisch jede Python-Bibliothek, die HTTP spricht, lesen
        das Bündel von ``certifi`` und nicht ``/etc/ssl/certs``. Ein Zertifikat, das nur im
        Systemspeicher steht, ist für sie unsichtbar — und der Fehler sieht aus wie ein
        Netzproblem.
        """
        inhalt = API_DOCKERFILE.read_text(encoding="utf-8")

        assert "certifi.where()" in inhalt
        # Angehängt, nicht ersetzt: Die öffentlichen Wurzeln müssen gültig bleiben.
        assert '>> "$buendel"' in inhalt
        # Und *nach* der Installation, weil es certifi vorher nicht gibt.
        assert self._zeile_mit(API_DOCKERFILE, "uv sync --frozen --no-dev") < self._zeile_mit(
            API_DOCKERFILE, "certifi.where()"
        )

    def test_node_bekommt_sie_ueber_seine_eigene_variable(self) -> None:
        """Node hat einen eingebauten Speicher und liest '/etc/ssl/certs' nicht."""
        inhalt = UI_DOCKERFILE.read_text(encoding="utf-8")

        assert "ENV NODE_EXTRA_CA_CERTS=" in inhalt
