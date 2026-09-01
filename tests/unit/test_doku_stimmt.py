"""Die Architekturskizze beschreibt, was tatsächlich läuft (§5.1, §12, §18, §19).

Der Anlass ist ein echter Befund: Die Skizze nannte für den MCP-Dienst über Monate Port 8081,
während der Stack auf 8800 lief — und sie führte für ``db-personal`` eine Port-Freigabe auf,
die es bewusst nicht gibt (§5.2). Beides fiel niemandem auf, weil Prosa nicht ausgeführt wird.

Diese Tests führen sie aus. Sie lesen die Spezifikation als Text und vergleichen sie mit den
Stellen, die im Betrieb wirklich zählen: den Vorgabewerten in ``defaults.py``, der
``docker-compose.yml`` und der Kommandoliste der CLI. Sie prüfen nicht, ob die Doku *gut*
ist — nur, ob sie an den nachprüfbaren Stellen *stimmt*.

Eine bewusste Grenze: Geprüft werden Zahlen und Namen, nicht Formulierungen. Ein Test, der
Sätze festnagelt, macht das Umschreiben teuer und die Doku dadurch schlechter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wissensgraph.config import defaults

pytestmark = pytest.mark.unit

WURZEL = Path(__file__).resolve().parents[2]
SPEC = WURZEL / "docs" / "architektur-spec-wissensgraph.md"
COMPOSE = WURZEL / "docker-compose.yml"


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _env_tabellenzeile(name: str) -> str:
    """Die Zeile der ENV-Tabelle, die eine Variable beschreibt."""
    for zeile in _spec().splitlines():
        if zeile.startswith("|") and f"`{name}`" in zeile:
            return zeile
    raise AssertionError(f"{name} kommt in der Spezifikation nicht vor.")


class TestPortsStimmenMitDenVorgaben:
    """Was die Skizze als Port nennt, muss der Vorgabewert im Code sein."""

    def test_mcp_port(self) -> None:
        assert defaults.MCP_PORT == 8800
        zeile = _env_tabellenzeile("WG_MCP_PORT")
        assert str(defaults.MCP_PORT) in zeile
        # Der alte Wert darf nirgends mehr stehen — auch nicht in der Diensttabelle.
        assert "8081" not in _spec()

    def test_api_und_mock_port(self) -> None:
        assert str(defaults.API_PORT) in _env_tabellenzeile("WG_API_HOST")
        assert f"| {defaults.MOCK_PORT} → {defaults.MOCK_PORT} |" in _spec()

    def test_mcp_transport_vorgabe(self) -> None:
        """Die Vorgabe ist HTTP; als ``stdio`` stand sie jahrelang falsch in der Tabelle."""
        assert defaults.MCP_TRANSPORT == "http"
        zeile = _env_tabellenzeile("WG_MCP_TRANSPORT")
        assert f"`{defaults.MCP_TRANSPORT}`" in zeile


class TestDiensttabelle:
    """Die Tabelle in §5.1 nennt dieselben Dienste wie die Compose-Datei."""

    def test_jeder_dienst_steht_in_der_skizze(self) -> None:
        # Über die Datenstruktur und nicht über die Einrückung: Netze und Volumes stehen auf
        # derselben Ebene wie die Dienste und wären sonst welche.
        import yaml

        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        dienste = sorted(compose["services"])
        assert dienste, "Keine Dienste in der Compose-Datei gefunden."
        text = _spec()
        for dienst in dienste:
            assert f"| `{dienst}` |" in text, f"Dienst '{dienst}' fehlt in der Diensttabelle §5.1."

    def test_ohne_freigabe_wird_als_solches_ausgewiesen(self) -> None:
        """`db-personal` und `broker` haben bewusst keinen Host-Port (§5.2, §20.1).

        Eine Tabelle, die dort eine Portnummer nennt, ist nicht nur veraltet — sie widerspricht
        dem Sicherheitsprinzip, das dieselbe Spezifikation aufstellt.
        """
        text = _spec()
        for dienst in ("db-personal", "broker"):
            zeile = next(z for z in text.splitlines() if z.startswith(f"| `{dienst}` |"))
            assert "keine Freigabe" in zeile, f"'{dienst}' hat keine Host-Freigabe (§5.2)."
            assert not re.search(r"\d{4}\s*→", zeile), f"'{dienst}' bekommt keinen Host-Port."


class TestWerkzeugtabelle:
    """§18.1 nennt dieselben MCP-Werkzeuge, die der Server anbietet.

    Auch das war gedriftet: Die Tabelle kannte sieben Werkzeuge, angeboten wurden acht —
    ``graph_schema`` fehlte, ausgerechnet das, welches dem Agenten das Raten abnimmt.
    """

    def _angeboten(self) -> set[str]:
        """Die Werkzeugnamen aus der Registry — als Text gelesen.

        Sie zur Laufzeit abzufragen hieße, eine ganze ``Runtime`` samt Datenbankverbindungen
        aufzubauen; das ist für einen Unit-Test zu teuer. Die Namen stehen in der Registry an
        genau einer Stelle (``name="…"``), und ein Umbenennen fällt hier trotzdem auf.
        """
        quelle = WURZEL / "src" / "wissensgraph" / "mcp" / "tools.py"
        namen = set(re.findall(r'name="([a-z_]+)"', quelle.read_text(encoding="utf-8")))
        assert namen, "Keine Werkzeugnamen in der Registry gefunden."
        return namen

    def test_jedes_werkzeug_steht_in_der_tabelle(self) -> None:
        text = _spec()
        fehlend = [name for name in self._angeboten() if f"| `{name}` |" not in text]
        assert not fehlend, f"§18.1 kennt diese Werkzeuge nicht: {sorted(fehlend)}."

    def test_die_tabelle_erfindet_keine_werkzeuge(self) -> None:
        """Der umgekehrte Fall wiegt schwerer: Ein Agent ruft auf, was er dort liest."""
        beginn = _spec().index("### 18.1 Werkzeuge")
        ende = _spec().index("### 18.2", beginn)
        genannt = set(re.findall(r"^\| `([a-z_]+)` \|", _spec()[beginn:ende], re.M))
        assert genannt <= self._angeboten(), (
            f"§18.1 nennt Werkzeuge, die es nicht gibt: {sorted(genannt - self._angeboten())}."
        )


class TestCliKapitel:
    """§19 zeigt Kommandos, die es gibt — und keine, die es nicht gibt."""

    def _kommandos(self) -> set[str]:
        """Die Kommandonamen aus den ``bash``-Blöcken der Spezifikation.

        Nur die Blöcke: Im Fließtext darf ein Kommando genannt werden, das es (noch) nicht
        gibt — ``wg export`` steht dort ausdrücklich als Ausblick. Ein Beispiel im Codeblock
        dagegen soll man abtippen können.
        """
        bloecke = re.findall(r"```bash\n(.*?)```", _spec(), re.S)
        return set(re.findall(r"\bwg ([a-z][a-z-]*)", "\n".join(bloecke)))

    def test_jedes_gezeigte_kommando_existiert(self) -> None:
        from wissensgraph.cli import app

        vorhanden = {befehl.name or "" for befehl in app.registered_commands}
        # Untergruppen (`wg graph …`, `wg models …`) tragen ihren Namen eine Ebene tiefer.
        for gruppe in app.registered_groups:
            unter = gruppe.typer_instance
            if unter is not None and isinstance(unter.info.name, str):
                vorhanden.add(unter.info.name)
        unbekannt = self._kommandos() - vorhanden
        assert not unbekannt, (
            f"§19 zeigt Kommandos, die die CLI nicht kennt: {sorted(unbekannt)}. "
            f"Bekannt sind: {sorted(vorhanden)}."
        )

    def test_die_startbefehle_der_container_sind_dokumentiert(self) -> None:
        """Wer einen Container von Hand nachfahren will, findet den Befehl in §19."""
        assert self._kommandos() >= {"serve", "worker", "mcp"}
