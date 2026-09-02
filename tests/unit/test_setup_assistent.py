"""Der Einrichtungsassistent (``wg setup``, §6).

Zwei Dinge sind hier zu beweisen, und das zweite ist das wichtigere.

**Der Katalog ist vollständig, ohne gepflegt zu werden.** Eine zweite Liste aller Einstellungen
wäre binnen eines Sprints falsch — wer ein Feld hinzufügt, denkt nicht an sie. Die Tests prüfen
deshalb nicht, dass eine bestimmte Zahl von Einträgen herauskommt, sondern dass jede der drei
Quellen (ENV-Tabelle, Platzhalter, Schema) wirklich durchschlägt.

**Geschrieben wird, ohne etwas zu zerstören.** Beide Zieldateien bestehen zum größeren Teil aus
Kommentaren, und die sind der eigentliche Wert: Neben ``min_similarity`` stehen fünfundzwanzig
Zeilen, die erklären, warum dort 0,80 steht. Ein Assistent, der sie beim Ändern der Zahl
verlöre, wäre schlimmer als gar keiner — der Verlust fiele erst auf, wenn ihn niemand mehr
rückgängig machen kann.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wissensgraph.config import defaults
from wissensgraph.config.env_mapping import ENV_BINDINGS
from wissensgraph.config.wizard import (
    Eintrag,
    baue_katalog,
    finde_yaml_zeile,
    ist_geheim,
    lies_env,
    maskiert,
    platzhalter_von,
    pruefe_gesamt,
    schreibe_env,
    schreibe_yaml,
)

pytestmark = pytest.mark.unit

WURZEL = Path(__file__).resolve().parents[2]


@pytest.fixture
def echte_dateien() -> dict[str, Path]:
    """Die Dateien des Repositories — der Katalog wird gegen die echten Quellen geprüft."""
    return {
        "env_beispiel": WURZEL / ".env.example",
        "config": WURZEL / "config" / "wissensgraph.yaml",
        "models": WURZEL / "config" / "models.yaml",
        "sources": WURZEL / "config" / "sources.yaml",
    }


@pytest.fixture
def katalog(echte_dateien: dict[str, Path]):
    return baue_katalog(
        env_beispiel=echte_dateien["env_beispiel"],
        config_datei=echte_dateien["config"],
        weitere_yaml=[echte_dateien["models"], echte_dateien["sources"]],
    )


class TestPlatzhalter:
    def test_unterscheidet_pflicht_von_kuer(self) -> None:
        """``${X}`` verhindert den Start, ``${X:-}`` sagt "leer ist in Ordnung" (§6.1 Regel 3).

        Beides auf "" abzubilden machte aus jeder optionalen Angabe eine Pflicht — der
        Assistent fragte dann nach einem Dutzend Werte, die niemand braucht.
        """
        gefunden = platzhalter_von("a: ${PFLICHT}\nb: ${KUER:-}\nc: ${MIT:-vorgabe}\n")

        assert gefunden["PFLICHT"] is None
        assert gefunden["KUER"] == ""
        assert gefunden["MIT"] == "vorgabe"

    def test_uebergeht_auskommentierte_beispiele(self) -> None:
        """In ``sources.yaml`` steht der Gateway-Schlüssel als Beispiel hinter einem '#'.

        Als Platzhalter gelesen machte der Assistent daraus eine Pflichtangabe für eine
        Kopfzeile, die niemand eingeschaltet hat — genau das ist beim ersten Rauchtest passiert.
        """
        gefunden = platzhalter_von("#        x-apikey: ${NUR_EIN_BEISPIEL}\necht: ${WIRKLICH:-}\n")

        assert "NUR_EIN_BEISPIEL" not in gefunden
        assert "WIRKLICH" in gefunden


class TestKatalog:
    def test_enthaelt_die_dokumentierte_env_schnittstelle(self, katalog) -> None:
        """Jede Variable aus §6.4 muss im Assistenten auftauchen — sonst ist er unvollständig."""
        namen = {eintrag.schluessel for eintrag in katalog}

        fehlend = sorted(
            binding.variable for binding in ENV_BINDINGS if binding.variable not in namen
        )

        assert not fehlend, f"Nicht im Katalog: {fehlend}"

    def test_enthaelt_die_fachlichen_schwellen_aus_dem_schema(self, katalog) -> None:
        """Sie stehen in keiner ENV-Tabelle: Sie kommen allein aus dem Pydantic-Schema."""
        namen = {eintrag.schluessel for eintrag in katalog}

        assert "clustering.min_similarity" in namen
        assert "traversal.ranking.hop_weight" in namen, "Verschachtelung wird nicht aufgelöst."
        assert "orphans.proximity_top_n" in namen

    def test_schreibt_jeden_wert_genau_einmal(self, katalog) -> None:
        """Ein Wert, der zweimal im Katalog steht, würde zweimal gefragt — und einmal falsch."""
        namen = [eintrag.schluessel for eintrag in katalog]

        assert len(namen) == len(set(namen))

    def test_leitet_das_ziel_am_platzhalter_ab(self, katalog) -> None:
        """Die Regel, nach der der Assistent entscheidet, wohin er schreibt.

        ``api.port`` steht im YAML als ``${WG_API_PORT:-8080}`` — also gehört der Wert in die
        Umgebung und erscheint unter seinem ENV-Namen. ``clustering.neighbors_k`` steht dort als
        Zahl — also gehört er ins YAML.
        """
        assert katalog.get("api.port") is None
        port = katalog.get("WG_API_PORT")
        assert port is not None and port.ziel == "env"

        nachbarn = katalog.get("clustering.neighbors_k")
        assert nachbarn is not None and nachbarn.ziel == "yaml"

    def test_uebernimmt_die_erklaerungen_aus_der_vorlage(self, katalog) -> None:
        """Ohne sie wäre der Assistent eine Liste von Namen — und damit wertlos für den,
        der das System zum ersten Mal aufsetzt."""
        eintrag = katalog.get("WG_EMBEDDING_DIM")

        assert eintrag is not None
        assert "Migrationsschema" in eintrag.beschreibung

    def test_kennt_die_erlaubten_werte_geschlossener_mengen(self, katalog) -> None:
        """Aus dem ``Literal`` im Schema, nicht aus einer zweiten Liste im Assistenten."""
        eintrag = katalog.get("WG_API_AUTH_MODE")

        assert eintrag is not None
        assert set(eintrag.auswahl) == {"none", "token", "oidc"}

    def test_gruppiert_in_der_reihenfolge_der_vorlage(self, katalog) -> None:
        """Wer den Assistenten durchläuft, soll die Dateien wiedererkennen."""
        abschnitte = katalog.abschnitte

        assert abschnitte[0] == "Laufzeit"
        assert any(name.startswith("config/wissensgraph.yaml") for name in abschnitte)


class TestGeheimnisse:
    @pytest.mark.parametrize(
        "name",
        [
            "WG_API_TOKEN",
            "WG_PROVIDER_GEMINI__API_KEY",
            "WG_POSTGRES_PASSWORD",
            "WG_DB_SHARED_DSN",
            "WG_PROVIDER_VERTEX__CREDENTIALS_FILE",
        ],
    )
    def test_erkennt_was_nicht_angezeigt_werden_darf(self, name: str) -> None:
        """Ein DSN zählt mit: Er trägt das Passwort im Klartext."""
        assert ist_geheim(name)

    def test_zeigt_gewoehnliche_werte_im_klartext(self) -> None:
        assert maskiert("WG_API_PORT", "8080") == "8080"

    def test_maskiert_geheimnisse_bis_auf_die_laenge(self) -> None:
        """Die Länge bleibt sichtbar: Sie beantwortet die Frage "steht da überhaupt etwas"
        ohne den Wert zu verraten."""
        angezeigt = maskiert("WG_API_TOKEN", "streng-geheim")

        assert "streng-geheim" not in angezeigt
        assert "13" in angezeigt

    def test_die_maske_bleibt_ascii(self) -> None:
        """Aus demselben Grund wie die Statussymbole der CLI: Eine Windows-Konsole in einer
        Codepage ohne Unicode bricht bei Blockzeichen ab, und ein Einrichtungswerkzeug, das an
        seiner eigenen Ausgabe scheitert, ist wertlos."""
        angezeigt = maskiert("WG_API_TOKEN", "x")

        assert angezeigt.isascii()
        assert defaults.SECRET_MASK in angezeigt


class TestEnvSchreiben:
    def test_ersetzt_an_ort_und_stelle_und_laesst_kommentare_stehen(self, tmp_path: Path) -> None:
        datei = tmp_path / ".env"
        datei.write_text(
            "# Die Erklärung, warum dieser Wert so steht.\nWG_API_PORT=8080\nWG_ENV=dev\n",
            encoding="utf-8",
        )

        geaendert = schreibe_env(datei, {"WG_API_PORT": "9090"})

        inhalt = datei.read_text(encoding="utf-8")
        assert geaendert == 1
        assert "WG_API_PORT=9090" in inhalt
        assert "# Die Erklärung, warum dieser Wert so steht." in inhalt
        assert "WG_ENV=dev" in inhalt

    def test_zaehlt_nur_echte_aenderungen(self, tmp_path: Path) -> None:
        """Sonst meldete der Assistent Arbeit, die er nicht getan hat."""
        datei = tmp_path / ".env"
        datei.write_text("WG_ENV=dev\n", encoding="utf-8")

        assert schreibe_env(datei, {"WG_ENV": "dev"}) == 0

    def test_haengt_unbekannte_werte_erkennbar_an(self, tmp_path: Path) -> None:
        datei = tmp_path / ".env"
        datei.write_text("WG_ENV=dev\n", encoding="utf-8")

        schreibe_env(datei, {"WG_NEU": "wert"})

        inhalt = datei.read_text(encoding="utf-8")
        assert "WG_NEU=wert" in inhalt
        assert "Einrichtungsassistenten" in inhalt

    def test_legt_die_datei_aus_der_vorlage_an(self, tmp_path: Path) -> None:
        """Eine frische Installation soll nicht mit einer nackten Liste beginnen, sondern mit
        derselben Anleitung, die im Repository liegt."""
        vorlage = tmp_path / ".env.example"
        vorlage.write_text("# Anleitung\nWG_ENV=dev\n", encoding="utf-8")
        ziel = tmp_path / ".env"

        schreibe_env(ziel, {"WG_ENV": "prod"}, vorlage=vorlage)

        inhalt = ziel.read_text(encoding="utf-8")
        assert "# Anleitung" in inhalt
        assert "WG_ENV=prod" in inhalt

    def test_liest_zurueck_was_es_geschrieben_hat(self, tmp_path: Path) -> None:
        datei = tmp_path / ".env"
        datei.write_text("# nichts\n", encoding="utf-8")

        schreibe_env(datei, {"WG_A": "1", "WG_B": "zwei"})

        assert lies_env(datei) == {"WG_A": "1", "WG_B": "zwei"}


class TestYamlSchreiben:
    @staticmethod
    def _datei(tmp_path: Path) -> Path:
        pfad = tmp_path / "wissensgraph.yaml"
        pfad.write_text(
            "clustering:\n"
            "  # Fünfundzwanzig Zeilen Begründung, warum hier 0.80 steht.\n"
            "  min_similarity: 0.80\n"
            "  neighbors_k: 8\n"
            "traversal:\n"
            "  max_nodes: 400\n"
            "  ranking:\n"
            "    hop_weight: 0.5\n",
            encoding="utf-8",
        )
        return pfad

    def test_aendert_die_zeile_und_sonst_nichts(self, tmp_path: Path) -> None:
        """Der Kern der Sache: Die Begründung neben dem Wert überlebt seine Änderung."""
        datei = self._datei(tmp_path)

        geaendert, fehlend = schreibe_yaml(datei, {("clustering", "min_similarity"): "0.75"})

        inhalt = datei.read_text(encoding="utf-8")
        assert (geaendert, fehlend) == (1, [])
        assert "  min_similarity: 0.75" in inhalt
        assert "Fünfundzwanzig Zeilen Begründung" in inhalt
        assert "  neighbors_k: 8" in inhalt

    def test_findet_auch_verschachtelte_pfade(self, tmp_path: Path) -> None:
        datei = self._datei(tmp_path)

        geaendert, _ = schreibe_yaml(datei, {("traversal", "ranking", "hop_weight"): "0.9"})

        assert geaendert == 1
        assert "    hop_weight: 0.9" in datei.read_text(encoding="utf-8")

    def test_verwechselt_gleichnamige_schluessel_nicht(self, tmp_path: Path) -> None:
        """``max_nodes`` gibt es unter ``traversal``; ein Treffer unter ``clustering`` wäre
        die falsche Zeile — und der Fehler fiele erst im Betrieb auf."""
        datei = tmp_path / "c.yaml"
        datei.write_text(
            "a:\n  wert: 1\nb:\n  wert: 2\n",
            encoding="utf-8",
        )

        schreibe_yaml(datei, {("b", "wert"): "99"})

        inhalt = datei.read_text(encoding="utf-8")
        assert "a:\n  wert: 1\n" in inhalt
        assert "b:\n  wert: 99\n" in inhalt

    def test_legt_einen_fehlenden_abschnitt_an(self, tmp_path: Path) -> None:
        """``config/wissensgraph.yaml`` nennt nicht jedes Feld des Schemas: Was seine Vorgabe
        behält, steht dort gar nicht — ``search`` etwa fehlt vollständig. Ohne das Anlegen
        könnte der Assistent keinen einzigen Suchparameter setzen und wäre damit keiner für
        *alle* Einstellungen."""
        datei = self._datei(tmp_path)

        geaendert, fehlend = schreibe_yaml(datei, {("search", "limit"): "25"})

        inhalt = datei.read_text(encoding="utf-8")
        assert (geaendert, fehlend) == (1, [])
        assert "search:\n  limit: 25" in inhalt
        # Jeder andere Wert in dieser Datei trägt eine Begründung; eine Zeile ohne wäre für den
        # nächsten Leser nicht einzuordnen.
        assert "Einrichtungsassistenten" in inhalt
        assert "Fünfundzwanzig Zeilen Begründung" in inhalt

    def test_ergaenzt_einen_wert_in_einem_vorhandenen_block(self, tmp_path: Path) -> None:
        datei = self._datei(tmp_path)

        geaendert, fehlend = schreibe_yaml(datei, {("clustering", "stability_runs"): "3"})

        inhalt = datei.read_text(encoding="utf-8")
        assert (geaendert, fehlend) == (1, [])
        assert "  stability_runs: 3" in inhalt
        assert "  neighbors_k: 8" in inhalt

    def test_schreibt_nicht_durch_einen_wert_hindurch(self, tmp_path: Path) -> None:
        """Etwas unter einen Skalar einzurücken ergäbe kein gültiges YAML mehr — und eine
        kaputte Konfigurationsdatei ist der eine Schaden, den ein Einrichtungswerkzeug
        niemals anrichten darf."""
        datei = self._datei(tmp_path)
        vorher = datei.read_text(encoding="utf-8")

        geaendert, fehlend = schreibe_yaml(datei, {("clustering", "neighbors_k", "tiefer"): "1"})

        assert (geaendert, fehlend) == (0, ["clustering.neighbors_k.tiefer"])
        assert datei.read_text(encoding="utf-8") == vorher

    def test_findet_nichts_ausserhalb_seines_blocks(self, tmp_path: Path) -> None:
        datei = self._datei(tmp_path)

        assert finde_yaml_zeile(
            datei.read_text(encoding="utf-8").splitlines(), ("clustering", "max_nodes")
        ) is None


class TestEintragPruefen:
    def test_weist_einen_wert_ausserhalb_der_auswahl_zurueck(self) -> None:
        eintrag = Eintrag(
            schluessel="WG_API_AUTH_MODE",
            abschnitt="A",
            beschreibung="",
            ziel="env",
            auswahl=("none", "token", "oidc"),
        )

        assert eintrag.pruefen("vielleicht") is not None
        assert eintrag.pruefen("token") is None

    def test_weist_eine_zahl_zurueck_die_keine_ist(self) -> None:
        eintrag = Eintrag(
            schluessel="WG_API_PORT", abschnitt="A", beschreibung="", ziel="env", typ="ganzzahl"
        )

        assert eintrag.pruefen("achttausend") is not None
        assert eintrag.pruefen("8080") is None

    def test_besteht_auf_einem_pflichtwert(self) -> None:
        eintrag = Eintrag(
            schluessel="WG_EMBEDDING_DIM",
            abschnitt="A",
            beschreibung="",
            ziel="env",
            pflicht=True,
        )

        assert eintrag.pruefen("") is not None

    def test_laesst_eine_kuer_leer(self) -> None:
        eintrag = Eintrag(schluessel="WG_BROKER_URL", abschnitt="A", beschreibung="", ziel="env")

        assert eintrag.pruefen("") is None


class TestPruefung:
    def test_meldet_ein_token_das_noch_der_platzhalter_ist(
        self, echte_dateien: dict[str, Path]
    ) -> None:
        """Der häufigste Fehler beim ersten Aufsetzen: Die Zeile steht da, aber unverändert.

        Ohne diese Prüfung startete die API mit einem Token, das in jedem Repository steht.
        """
        befund = pruefe_gesamt(
            config_datei=echte_dateien["config"],
            env={
                "WG_EMBEDDING_DIM": "768",
                "WG_API_AUTH_MODE": "token",
                "WG_API_TOKEN": defaults.API_TOKEN_PLATZHALTER,
            },
        )

        assert not befund.in_ordnung
        assert any(defaults.API_TOKEN_PLATZHALTER in zeile for zeile in befund.fehler)

    def test_meldet_ein_fehlendes_token_bei_token_modus(
        self, echte_dateien: dict[str, Path]
    ) -> None:
        befund = pruefe_gesamt(
            config_datei=echte_dateien["config"],
            env={"WG_EMBEDDING_DIM": "768", "WG_API_AUTH_MODE": "token", "WG_API_TOKEN": ""},
        )

        assert not befund.in_ordnung

    def test_haelt_eine_taugliche_konfiguration_fuer_tauglich(
        self, echte_dateien: dict[str, Path]
    ) -> None:
        befund = pruefe_gesamt(
            config_datei=echte_dateien["config"],
            env={
                "WG_EMBEDDING_DIM": "768",
                "WG_API_AUTH_MODE": "token",
                "WG_API_TOKEN": "ein-echtes-geheimnis",
            },
        )

        assert befund.in_ordnung, befund.fehler

    def test_stuerzt_bei_kaputter_konfiguration_nicht_selbst_ab(self, tmp_path: Path) -> None:
        """Ein Assistent, der an der Datei scheitert, die er reparieren soll, hilft niemandem."""
        kaputt = tmp_path / "kaputt.yaml"
        kaputt.write_text("das: [ist kein: gültiges yaml\n", encoding="utf-8")

        befund = pruefe_gesamt(config_datei=kaputt, env={})

        assert not befund.in_ordnung
        assert befund.fehler
