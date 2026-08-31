"""Jira-Wiki-Markup nach Markdown (§8.2).

Der Grund für diese Datei ist nicht, dass die Umwandlung schwierig wäre, sondern dass ihr
Scheitern *unauffällig* ist. Wiki-Markup und Markdown sehen einander ähnlich genug, dass ein
unbehandelter Text vollständig aussieht — nur bedeutet er etwas anderes. ``# Ursache eingrenzen``
ist in Jira der erste Punkt einer nummerierten Liste und in Markdown eine Überschrift erster
Ordnung. Ein Embedding über den falsch gelesenen Text wirft keinen Fehler; es misst nur etwas
anderes.
"""

from __future__ import annotations

import pytest

from wissensgraph.infrastructure.adapters.jira_markdown import JiraLinks, wiki_to_markdown

pytestmark = pytest.mark.unit

WEB = "https://jira.example"
LINKS = JiraLinks(id_prefix="jira", web_base_url=WEB)


def markdown(text: str) -> str:
    """Nur der Text, für die Fälle, in denen die Verweise nicht interessieren."""
    return wiki_to_markdown(text, LINKS).markdown


class TestBloecke:
    """Zeilenorientierte Regeln."""

    def test_ueberschriften(self) -> None:
        assert markdown("h1. Eins\nh3. Drei") == "# Eins\n### Drei"

    def test_die_raute_ist_eine_liste_und_keine_ueberschrift(self) -> None:
        """Der Fall, der ohne Umwandlung falsch statt kaputt wäre."""
        assert markdown("# erst\n# dann") == "1. erst\n1. dann"

    def test_verschachtelung_wird_einrueckung(self) -> None:
        assert markdown("* a\n** a1\n* b") == "- a\n  - a1\n- b"

    def test_die_letzte_marke_bestimmt_die_art(self) -> None:
        """``*#`` ist ein nummerierter Punkt innerhalb einer Aufzählung."""
        assert markdown("* a\n*# a1") == "- a\n  1. a1"

    def test_eine_tabelle_bekommt_ihre_trennzeile(self) -> None:
        ergebnis = markdown("|| A || B ||\n| 1 | 2 |")

        assert ergebnis == "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def test_bq_wird_ein_zitat(self) -> None:
        assert markdown("bq. Gemessen: 6 h.") == "> Gemessen: 6 h."

    def test_ein_quote_block_zitiert_jede_zeile(self) -> None:
        assert markdown("{quote}\na\nb\n{quote}") == "> a\n> b"

    def test_ein_panel_behaelt_seinen_titel(self) -> None:
        assert markdown("{panel:title=Merke}\nKurz.\n{panel}") == "> **Merke**\n> Kurz."

    @pytest.mark.parametrize(
        ("makro", "beschriftung"),
        [("info", "INFO"), ("tip", "TIPP"), ("note", "HINWEIS"), ("warning", "ACHTUNG")],
    )
    def test_hinweismakros(self, makro: str, beschriftung: str) -> None:
        ergebnis = markdown(f"{{{makro}}}\nText\n{{{makro}}}")

        assert ergebnis == f"> **{beschriftung}:**\n> Text"


class TestInline:
    """Auszeichnungen innerhalb einer Zeile."""

    @pytest.mark.parametrize(
        ("wiki", "erwartet"),
        [
            ("*fett*", "**fett**"),
            ("_kursiv_", "*kursiv*"),
            ("-weg-", "~~weg~~"),
            ("+neu+", "<u>neu</u>"),
            ("{{code}}", "`code`"),
            ("{color:red}rot{color}", "rot"),
            ("[~mneff]", "@mneff"),
        ],
    )
    def test_auszeichnungen(self, wiki: str, erwartet: str) -> None:
        assert markdown(wiki) == erwartet

    def test_hoch_und_tiefstellung_stehen_mitten_im_wort(self) -> None:
        """Eine Wortgrenze zu verlangen brächte diese beiden um ihren einzigen Zweck."""
        assert markdown("x^2^ und H~2~O") == "x<sup>2</sup> und H<sub>2</sub>O"

    def test_ein_bild_wird_eingebunden(self) -> None:
        assert markdown("!plan.png|width=300!") == "![plan.png](plan.png)"


class TestCodebloecke:
    """Der Grund, warum die Reihenfolge der Schritte die Korrektheit trägt."""

    def test_sprache_und_einrueckung_bleiben(self) -> None:
        ergebnis = markdown("{code:python}\ndef f():\n    return 1\n{code}")

        assert ergebnis == "```python\ndef f():\n    return 1\n```"

    def test_ein_titel_ist_keine_sprache(self) -> None:
        """``{code:title=X}`` nennt keine Sprache — sonst stünde ``title=X`` als Sprachangabe da."""
        assert markdown("{code:title=Beispiel}\na\n{code}") == "```\na\n```"

    def test_auszeichnungen_greifen_nicht_in_den_code(self) -> None:
        """Ohne das Herausnehmen machte die Fett-Regel aus einem C-Zeiger eine Auszeichnung."""
        ergebnis = markdown("{code}\nint *p = &x;\nvoid *q;\n{code}")

        assert "int *p = &x;" in ergebnis
        assert "**" not in ergebnis

    def test_noformat_ohne_sprache(self) -> None:
        assert markdown("{noformat}\nroh\n{noformat}") == "```\nroh\n```"


class TestVerweise:
    """Was zu einer Kante wird und was nur ein Link bleibt (§8.5)."""

    def test_ein_vorgangslink_wird_link_und_kandidat(self) -> None:
        ergebnis = wiki_to_markdown("Siehe [den Fall|KFLWOPS-42].", LINKS)

        assert ergebnis.markdown == f"Siehe [den Fall]({WEB}/browse/KFLWOPS-42)."
        assert [(v.target, v.kind) for v in ergebnis.references] == [
            ("jira:KFLWOPS-42", "references")
        ]

    def test_ein_nackter_schluessel_bekommt_seinen_schluessel_als_text(self) -> None:
        ergebnis = wiki_to_markdown("Siehe [KFLWOPS-7].", LINKS)

        assert ergebnis.markdown == f"Siehe [KFLWOPS-7]({WEB}/browse/KFLWOPS-7)."

    def test_eine_externe_adresse_wird_kein_kandidat(self) -> None:
        ergebnis = wiki_to_markdown("[extern|https://example.org]", LINKS)

        assert ergebnis.markdown == "[extern](https://example.org)"
        assert ergebnis.references == ()

    def test_ein_fertiger_link_wird_nicht_noch_einmal_umgeformt(self) -> None:
        """Die zweite Regel griffe sonst auf das Ergebnis der ersten zu."""
        ergebnis = wiki_to_markdown("[extern|https://example.org]", LINKS)

        assert ergebnis.markdown.count("(") == 1

    def test_die_eigene_referenzsyntax_bleibt_unangetastet(self) -> None:
        """``[[id]]`` gehört dem Kern (§7.1). Hier angefasst, käme es dort nie an."""
        ergebnis = wiki_to_markdown("Kontext steht in [[confluence:100002]].", LINKS)

        assert "[[confluence:100002]]" in ergebnis.markdown

    def test_dasselbe_ziel_erscheint_einmal(self) -> None:
        ergebnis = wiki_to_markdown("[a|KF-1] und [b|KF-1]", LINKS)

        assert len(ergebnis.references) == 1


class TestLeereEingaben:
    @pytest.mark.parametrize("wert", [None, "", "  \n "])
    def test_ohne_inhalt_kommt_nichts_heraus(self, wert: str | None) -> None:
        ergebnis = wiki_to_markdown(wert, LINKS)

        assert ergebnis.markdown == ""
        assert ergebnis.references == ()
