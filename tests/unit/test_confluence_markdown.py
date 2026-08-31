"""Storage-Format nach Markdown und die Auflösung der Seitenverweise (§8.2, §8.5).

Diese Datei prüft die Umwandlung, nicht den Adapter: Was hier hineingeht, ist ein Ausschnitt
Storage-Format, wie ihn eine echte Instanz liefert, und was herauskommt, ist der Text, der später
im ``body`` eines Konzepts steht — und damit die Grundlage von Embedding, Clustering und allem,
was ein Mensch in der UI liest.

Der Maßstab ist deshalb nicht "es bricht nicht ab", sondern "es steht das Richtige da". Ein
Parser, der ``<ac:structured-macro>`` stillschweigend als Text durchreicht, wirft nichts — er
verfälscht nur jede Ähnlichkeitsrechnung, die danach kommt.
"""

from __future__ import annotations

import pytest

from wissensgraph.infrastructure.adapters.confluence_links import (
    ConfluenceLinks,
    PageLink,
    link_from_href,
    page_id_from_tiny,
)
from wissensgraph.infrastructure.adapters.confluence_markdown import storage_to_markdown

pytestmark = pytest.mark.unit

WEB = "https://itdoc.example"

#: Die Seiten, die die Titelsuche in diesen Tests kennt.
TITEL = {("ENG", "Ladelauf"): "100002"}


def links(**rest: object) -> ConfluenceLinks:
    """Eine Auflösung mit den Vorgaben dieser Testdatei."""
    argumente: dict[str, object] = {
        "id_prefix": "confluence",
        "web_base_url": WEB,
        "page_id": "100001",
        "space_key": "ENG",
        "lookup": lambda space, titel: TITEL.get((space, titel)),
    }
    argumente.update(rest)
    return ConfluenceLinks(**argumente)  # type: ignore[arg-type]


def markdown(storage: str) -> str:
    """Nur der Text, für die Fälle, in denen die Verweise nicht interessieren."""
    return storage_to_markdown(storage, links()).markdown


class TestBloecke:
    """Überschriften, Absätze, Listen, Tabellen."""

    def test_ueberschriften_bekommen_ihre_ebene(self) -> None:
        assert markdown("<h1>Eins</h1><h3>Drei</h3>") == "# Eins\n\n### Drei"

    def test_absaetze_werden_durch_leerzeilen_getrennt(self) -> None:
        assert markdown("<p>Eins</p><p>Zwei</p>") == "Eins\n\nZwei"

    def test_verschachtelte_listen_werden_eingerueckt(self) -> None:
        """Die Einrückung *ist* die Verschachtelung.

        Sie ist damit das einzige, was in dieser Zeile inhaltlich zählt — und zugleich das, was
        eine gutgemeinte Leerzeichen-Normalisierung als Erstes wegräumt.
        """
        ergebnis = markdown("<ul><li>a<ul><li>a1</li></ul></li><li>b</li></ul>")

        assert ergebnis == "- a\n  - a1\n- b"

    def test_eine_unterliste_reisst_den_folgepunkt_nicht_heraus(self) -> None:
        """Zwei Zeilenumbrüche hintereinander wären eine Leerzeile — und zwei Listen."""
        ergebnis = markdown("<ul><li>a<ul><li>a1</li></ul></li><li>b</li></ul>")

        assert "\n\n" not in ergebnis

    def test_nummerierte_listen_zaehlen(self) -> None:
        assert markdown("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"

    def test_eine_tabelle_bekommt_ihre_trennzeile(self) -> None:
        """Markdown kennt keine Tabelle ohne Kopfzeile; ohne die Trennzeile ist es keine."""
        ergebnis = markdown(
            "<table><tbody><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></tbody></table>"
        )

        assert ergebnis == "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def test_ein_senkrechter_strich_in_der_zelle_wird_entschaerft(self) -> None:
        """Sonst zerfiele eine Zelle in zwei, und die Tabelle verlöre ihre Form."""
        ergebnis = markdown("<table><tr><td>a|b</td></tr></table>")

        assert "a\\|b" in ergebnis


class TestInline:
    """Auszeichnungen innerhalb einer Zeile."""

    @pytest.mark.parametrize(
        ("storage", "erwartet"),
        [
            ("<strong>x</strong>", "**x**"),
            ("<b>x</b>", "**x**"),
            ("<em>x</em>", "*x*"),
            ("<code>x</code>", "`x`"),
            ("<del>x</del>", "~~x~~"),
            ("<u>x</u>", "<u>x</u>"),
            ("<sup>x</sup>", "<sup>x</sup>"),
        ],
    )
    def test_auszeichnungen(self, storage: str, erwartet: str) -> None:
        assert markdown(f"<p>{storage}</p>") == erwartet

    def test_entities_werden_aufgeloest(self) -> None:
        """``&nbsp;`` im Text ist einer der Gründe für den toleranten Parser."""
        assert markdown("<p>a&nbsp;b &amp; c</p>") == "a b & c"

    def test_unbekanntes_markup_faellt_als_text_durch(self) -> None:
        """Der Leitsatz des Moduls: Unbekanntes verschwindet, sein Inhalt bleibt."""
        assert markdown("<p><ac:irgendwas>Inhalt</ac:irgendwas></p>") == "Inhalt"


class TestMakros:
    """``<ac:structured-macro>`` — der Teil, den kein HTML-Parser von sich aus kennt."""

    def test_code_wird_ein_codeblock_mit_sprache(self) -> None:
        ergebnis = markdown(
            '<ac:structured-macro ac:name="code">'
            '<ac:parameter ac:name="language">python</ac:parameter>'
            "<ac:plain-text-body><![CDATA[def f():\n    return 1]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

        assert ergebnis == "```python\ndef f():\n    return 1\n```"

    def test_die_sprache_landet_nicht_im_code(self) -> None:
        """Der Parameterwert wird sonst wörtlich mitgenommen — als erste Zeile des Blocks."""
        ergebnis = markdown(
            '<ac:structured-macro ac:name="code">'
            '<ac:parameter ac:name="language">sql</ac:parameter>'
            "<ac:plain-text-body><![CDATA[SELECT 1]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

        assert "sqlSELECT" not in ergebnis
        assert ergebnis == "```sql\nSELECT 1\n```"

    def test_die_einrueckung_im_code_bleibt(self) -> None:
        """In einem Codeblock ist jedes Leerzeichen Inhalt."""
        ergebnis = markdown(
            '<ac:structured-macro ac:name="noformat">'
            "<ac:plain-text-body><![CDATA[a\n    b]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

        assert "\n    b" in ergebnis

    @pytest.mark.parametrize(
        ("name", "beschriftung"),
        [("info", "INFO"), ("tip", "TIPP"), ("note", "HINWEIS"), ("warning", "ACHTUNG")],
    )
    def test_hinweismakros_werden_zitatbloecke(self, name: str, beschriftung: str) -> None:
        ergebnis = markdown(
            f'<ac:structured-macro ac:name="{name}">'
            "<ac:rich-text-body><p>Text</p></ac:rich-text-body></ac:structured-macro>"
        )

        assert ergebnis == f"> **{beschriftung}:**\n> Text"

    def test_ein_panel_behaelt_seinen_titel(self) -> None:
        ergebnis = markdown(
            '<ac:structured-macro ac:name="panel">'
            '<ac:parameter ac:name="title">Merke</ac:parameter>'
            "<ac:rich-text-body><p>Kurz.</p></ac:rich-text-body></ac:structured-macro>"
        )

        assert ergebnis == "> **Merke**\n> Kurz."


class TestBilderUndAnhaenge:
    """``<ri:attachment>`` in seinen beiden Rollen."""

    def test_ein_bild_wird_eingebunden(self) -> None:
        ergebnis = markdown('<ac:image><ri:attachment ri:filename="plan.png"/></ac:image>')

        assert ergebnis == f"![plan.png]({WEB}/download/attachments/100001/plan.png)"

    def test_eine_datei_wird_ein_link(self) -> None:
        """Ein PDF im Bild-Syntax wäre in jeder Ansicht ein kaputtes Bild."""
        ergebnis = markdown('<ac:image><ri:attachment ri:filename="bericht.pdf"/></ac:image>')

        assert ergebnis.startswith("📎 [bericht.pdf](")

    def test_ein_anhang_im_link_behaelt_seinen_text(self) -> None:
        """Der Text steht im Storage-Format *hinter* dem Ziel — er darf nicht verlorengehen."""
        ergebnis = markdown(
            '<ac:link><ri:attachment ri:filename="bericht.pdf"/>'
            "<ac:plain-text-link-body><![CDATA[Bericht]]></ac:plain-text-link-body></ac:link>"
        )

        assert ergebnis == f"📎 [Bericht]({WEB}/download/attachments/100001/bericht.pdf)"


class TestVerweise:
    """Die vier Linkformen und was aus ihnen wird (§8.5)."""

    def test_ein_seitenverweis_wird_link_und_kandidat(self) -> None:
        """Beides zugleich: Der Text bleibt lesbar, der Graph bekommt seine Kante."""
        ergebnis = storage_to_markdown(
            '<ac:link><ri:page ri:space-key="ENG" ri:content-title="Ladelauf"/>'
            "<ac:plain-text-link-body><![CDATA[dort]]></ac:plain-text-link-body></ac:link>",
            links(),
        )

        assert ergebnis.markdown == f"[dort]({WEB}/pages/viewpage.action?pageId=100002)"
        assert [(v.target, v.kind) for v in ergebnis.references] == [
            ("confluence:100002", "references")
        ]

    def test_ein_unauffindbarer_titel_bleibt_ein_link_ohne_kante(self) -> None:
        """§8.5: kaputte Referenzen sind kein Fehler. Der Leser kommt trotzdem hin."""
        ergebnis = storage_to_markdown(
            '<ac:link><ri:page ri:space-key="ENG" ri:content-title="Gibt es nicht"/>'
            "<ac:plain-text-link-body><![CDATA[dort]]></ac:plain-text-link-body></ac:link>",
            links(),
        )

        assert ergebnis.references == ()
        assert "Gibt+es+nicht" in ergebnis.markdown

    def test_ohne_titelsuche_bleibt_der_text_vollstaendig(self) -> None:
        """Die Suche ist eine Zutat, keine Voraussetzung."""
        ergebnis = storage_to_markdown(
            '<ac:link><ri:page ri:space-key="ENG" ri:content-title="Ladelauf"/></ac:link>',
            links(lookup=None),
        )

        assert ergebnis.references == ()
        assert "Ladelauf" in ergebnis.markdown

    def test_ein_seitenaufruf_im_href_wird_erkannt(self) -> None:
        ergebnis = storage_to_markdown(
            '<a href="/pages/viewpage.action?pageId=100007">dort</a>', links()
        )

        assert [v.target for v in ergebnis.references] == ["confluence:100007"]

    def test_eine_externe_adresse_wird_kein_kandidat(self) -> None:
        """Eine Kante auf ein Konzept, das es nie geben wird, wäre eine Behauptung."""
        ergebnis = storage_to_markdown('<a href="https://example.org/x">dort</a>', links())

        assert ergebnis.references == ()
        assert ergebnis.markdown == "[dort](https://example.org/x)"

    def test_dasselbe_ziel_erscheint_einmal(self) -> None:
        """Zwei Erwähnungen ergeben eine Kante — ``ux_edges_triple`` ließe die zweite nicht zu."""
        ergebnis = storage_to_markdown(
            '<a href="?pageId=100007">a</a><a href="?pageId=100007">b</a>', links()
        )

        assert len(ergebnis.references) == 1

    def test_ein_anker_bleibt_erhalten(self) -> None:
        ergebnis = storage_to_markdown('<a href="?pageId=100007#abschnitt">x</a>', links())

        assert ergebnis.markdown.endswith("#abschnitt)")


class TestLeereEingaben:
    """Die Fälle, in denen es nichts zu tun gibt."""

    @pytest.mark.parametrize("wert", [None, "", "   "])
    def test_ohne_inhalt_kommt_nichts_heraus(self, wert: str | None) -> None:
        ergebnis = storage_to_markdown(wert, links())

        assert ergebnis.markdown == ""
        assert ergebnis.references == ()


class TestWeitereElemente:
    """Der Rest des Storage-Formats, der im Betrieb vorkommt."""

    def test_ein_zeilenumbruch(self) -> None:
        assert markdown("<p>a<br/>b</p>") == "a\nb"

    def test_eine_trennlinie(self) -> None:
        assert markdown("<p>a</p><hr/><p>b</p>") == "a\n\n---\n\nb"

    def test_ein_zitatblock(self) -> None:
        assert markdown("<blockquote><p>zitiert</p></blockquote>") == "> zitiert"

    def test_vorformatierter_text(self) -> None:
        assert markdown("<pre>roh</pre>") == "```\nroh\n```"

    def test_ein_bild_von_ausserhalb(self) -> None:
        ergebnis = markdown('<ac:image><ri:url ri:value="https://example.org/b.png"/></ac:image>')

        assert ergebnis == "![](https://example.org/b.png)"

    def test_eine_benutzererwaehnung(self) -> None:
        assert markdown('<ac:link><ri:user ri:userkey="mneff"/></ac:link>') == "@mneff"

    def test_skript_und_stil_verschwinden_samt_inhalt(self) -> None:
        """Anders als unbekanntes Markup: Ihr Inhalt ist kein Text, sondern Anweisung."""
        assert markdown("<p>a</p><script>alert(1)</script><style>p{}</style>") == "a"

    def test_eine_tabelle_ohne_kopfzeile_leiht_sich_die_erste(self) -> None:
        """Markdown kennt keine Tabelle ohne Kopfzeile; eine leere läse sich schlechter."""
        ergebnis = markdown("<table><tr><td>a</td></tr><tr><td>b</td></tr></table>")

        assert ergebnis == "| a |\n| --- |\n| b |"

    def test_eine_leere_tabelle_hinterlaesst_nichts(self) -> None:
        assert markdown("<p>x</p><table></table>") == "x"

    def test_ungleich_lange_zeilen_werden_aufgefuellt(self) -> None:
        """Eine Zeile mit weniger Zellen risse die Tabelle sonst auf."""
        ergebnis = markdown("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>")

        assert ergebnis.endswith("| c | |")

    def test_ein_listenpunkt_ohne_liste(self) -> None:
        """Kommt in gewachsenen Inhalten vor und darf nichts kosten."""
        assert markdown("<li>allein</li>") == "- allein"

    def test_cdata_ausserhalb_eines_makros_bleibt_text(self) -> None:
        assert markdown("<p><![CDATA[roher Text]]></p>") == "roher Text"

    def test_ein_makro_ohne_inhalt_hinterlaesst_nichts(self) -> None:
        ergebnis = markdown(
            '<p>x</p><ac:structured-macro ac:name="info">'
            "<ac:rich-text-body></ac:rich-text-body></ac:structured-macro>"
        )

        assert ergebnis == "x"

    def test_ein_panel_ohne_titel(self) -> None:
        ergebnis = markdown(
            '<ac:structured-macro ac:name="panel">'
            "<ac:rich-text-body><p>Kurz.</p></ac:rich-text-body></ac:structured-macro>"
        )

        assert ergebnis == "> Kurz."

    def test_ein_unbekanntes_makro_gibt_seinen_text_frei(self) -> None:
        ergebnis = markdown(
            '<ac:structured-macro ac:name="expand"><ac:rich-text-body><p>Inhalt</p>'
            "</ac:rich-text-body></ac:structured-macro>"
        )

        assert ergebnis == "Inhalt"

    def test_ein_anhang_ohne_dateinamen_wird_uebergangen(self) -> None:
        assert markdown("<p>x</p><ac:image><ri:attachment/></ac:image>") == "x"

    def test_ein_geschuetztes_leerzeichen_wird_ein_gewoehnliches(self) -> None:
        """Sonst fände eine Suche nach dem Titel die Seite nicht — und niemand sähe warum."""
        assert "\xa0" not in markdown("<p>Nächtlicher&nbsp;ETL-Lauf</p>")


class TestKurzlink:
    """Der Kurzlink ist die einzige Linkform, die gerechnet statt gelesen wird."""

    def test_hin_und_zurueck(self) -> None:
        """Die Gegenprobe zur Kodierung, die Confluence beim Erzeugen anwendet."""
        import base64

        for seiten_id in (1, 12345, 100001, 987654321):
            roh = seiten_id.to_bytes(8, "little").rstrip(b"\x00")
            token = base64.urlsafe_b64encode(roh).decode().rstrip("=")

            assert page_id_from_tiny(token) == str(seiten_id)

    @pytest.mark.parametrize("token", ["", "!!!", "AAAAAAAAAAAAAAAA", "AAAA"])
    def test_unbrauchbare_token_ergeben_nichts(self, token: str) -> None:
        """Ein unlesbarer Verweis ist kein Fehler — er bleibt ein Link ohne Kante."""
        assert page_id_from_tiny(token) is None


class TestLinkErkennung:
    """:func:`link_from_href` allein — sie entscheidet, welche Auflösung überhaupt greift."""

    def test_die_ausgeschriebene_id_schlaegt_alles(self) -> None:
        gefunden = link_from_href("/display/ENG/Titel?pageId=42")

        assert gefunden.page_id == "42"

    def test_der_cloud_pfad(self) -> None:
        gefunden = link_from_href("https://x/wiki/spaces/ENG/pages/100010/Titel")

        assert gefunden.page_id == "100010"

    def test_der_anzeigepfad_liefert_space_und_titel(self) -> None:
        gefunden = link_from_href("/display/ENG/Ein+Titel")

        assert (gefunden.space_key, gefunden.title) == ("ENG", "Ein Titel")

    def test_eine_fremde_adresse_bleibt_ohne_ziel(self) -> None:
        gefunden = link_from_href("https://example.org/seite")

        assert gefunden.is_external


class TestAdressen:
    """Die Adressen, die :class:`ConfluenceLinks` baut."""

    def test_ein_relativer_pfad_wird_absolut(self) -> None:
        """Ein relativer Link im ``body`` zeigt nach dem Speichern ins Leere."""
        aufgeloest = links().resolve(PageLink(url="/x/y"))

        assert aufgeloest.url == f"{WEB}/x/y"

    def test_eine_absolute_adresse_bleibt(self) -> None:
        aufgeloest = links().resolve(PageLink(url="https://example.org/x"))

        assert aufgeloest.url == "https://example.org/x"

    def test_die_kanonische_adresse_schlaegt_den_titelpfad(self) -> None:
        """Sie überlebt eine Umbenennung der Zielseite; ein Titelpfad tut das nicht."""
        aufgeloest = links().resolve(PageLink(page_id="100002", url="/display/ENG/Alter+Titel"))

        assert aufgeloest.url == f"{WEB}/pages/viewpage.action?pageId=100002"
