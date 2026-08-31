"""Confluence-Storage-Format nach Markdown (§8.2).

Was Confluence als ``body.storage.value`` liefert, ist XHTML mit zwei eigenen Namensräumen:
``ac:`` für Makros, Layout und Links, ``ri:`` für Verweise auf Seiten, Anhänge und Benutzer.
Unverändert in den ``body`` eines Konzepts geschrieben, wäre das für alles unbrauchbar, was
danach kommt — ein Embedding über ``<ac:structured-macro ac:name="info">`` misst Auszeichnung
statt Inhalt, und ein Mensch, der die Seite in der UI liest, sieht Markup.

**Warum ein toleranter HTML-Parser und kein XML-Parser.** Das Storage-Format ist als XML
*gemeint*, aber was aus einer gewachsenen Instanz kommt, ist es oft nicht: undeklarierte
Namensräume, HTML-Entities wie ``&nbsp;``, nicht geschlossene ``<br>``. Ein XML-Parser bricht
daran ab, und ein Seiteninhalt, der sich nicht parsen lässt, darf keinen Sync-Lauf verlieren.
:class:`html.parser.HTMLParser` verarbeitet all das, ohne zu urteilen, und behandelt ``ac:image``
einfach als Tag mit einem Doppelpunkt im Namen. Der Preis ist, dass wir die Baumstruktur selbst
mitführen; der Gewinn ist, dass unbekanntes Markup als Text durchfällt statt als Ausnahme.

**Verweise werden hier gefunden, aber nicht bewertet.** Der Parser gibt jeden erkannten
Seitenverweis an :class:`~wissensgraph.infrastructure.adapters.confluence_links.ConfluenceLinks`
weiter und schreibt das Ergebnis als gewöhnlichen Markdown-Link in den Text. Ob daraus eine Kante
wird, entscheidet später der Kern (§8.5) — hier entsteht nur die Kandidatenliste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

from wissensgraph.config import defaults
from wissensgraph.domain.references import SourceReference
from wissensgraph.infrastructure.adapters.confluence_links import (
    ConfluenceLinks,
    PageLink,
    link_from_href,
)
from wissensgraph.infrastructure.adapters.markdown import ConvertedBody

#: Makros, die zu einem Zitatblock mit Beschriftung werden. Der Wert ist die Beschriftung.
ADMONITIONS: dict[str, str] = {
    "info": "INFO",
    "tip": "TIPP",
    "note": "HINWEIS",
    "warning": "ACHTUNG",
}

#: Makros, deren Inhalt wörtlich als Codeblock übernommen wird.
CODE_MACROS: frozenset[str] = frozenset({"code", "noformat"})

#: Dateiendungen, die als Bild eingebunden werden; alles andere wird ein Anhang-Link.
IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

#: Inline-Auszeichnungen: Tag -> (Auftakt, Abschluss).
INLINE_MARKS: dict[str, tuple[str, str]] = {
    "strong": ("**", "**"),
    "b": ("**", "**"),
    "em": ("*", "*"),
    "i": ("*", "*"),
    "code": ("`", "`"),
    "s": ("~~", "~~"),
    "del": ("~~", "~~"),
    "strike": ("~~", "~~"),
    "u": ("<u>", "</u>"),
    "sup": ("<sup>", "</sup>"),
    "sub": ("<sub>", "</sub>"),
}

#: Tags, deren Inhalt vollständig verworfen wird.
DROPPED: frozenset[str] = frozenset({"script", "style"})

_MEHRFACHE_LEERZEILEN = re.compile(r"\n{3,}")

#: Das geschützte Leerzeichen (U+00A0) steht hier mit Absicht. Confluence-Inhalte sind voll von
#: ``&nbsp;``, und nach dem Auflösen der Entity sieht es aus wie ein Leerzeichen, ist aber keines:
#: Eine Suche nach "Nächtlicher ETL-Lauf" fände die Seite dann nicht, und niemand sähe warum.
_LEERZEICHEN = re.compile("[ \t\xa0]+")

#: Zeichen, die am Zeilenanfang als Einrückung gelten.
_EINZUG = " \t\xa0"


@dataclass
class _Makro:
    """Ein offenes ``<ac:structured-macro>`` mit seinen Parametern."""

    name: str
    parameter: dict[str, str] = field(default_factory=dict)
    aktueller_parameter: str | None = None
    klartext: list[str] = field(default_factory=list)


@dataclass
class _Tabelle:
    """Eine offene Tabelle, bis sie am ``</table>`` als Markdown ausgegeben wird."""

    zeilen: list[list[str]] = field(default_factory=list)
    kopfzeile: bool = False


def storage_to_markdown(storage: str | None, links: ConfluenceLinks) -> ConvertedBody:
    """Wandelt einen Storage-Format-Rumpf in Markdown und sammelt die Seitenverweise.

    Args:
        storage: Der Inhalt von ``body.storage.value``; ``None`` und leer sind zulässig.
        links: Die Auflösung der Verweise dieser Seite.

    Returns:
        Den Markdown-Text und die Konzept-IDs, auf die er zeigt — ohne Dubletten und in der
        Reihenfolge ihres ersten Vorkommens.
    """
    if not storage:
        return ConvertedBody()
    parser = _StorageParser(links)
    parser.feed(storage)
    parser.close()
    return parser.ergebnis()


class _StorageParser(HTMLParser):
    """Der Zustandsautomat hinter :func:`storage_to_markdown`.

    Die Ausgabe entsteht in einem Stapel von Puffern statt in einem einzigen. Das ist der Kniff,
    mit dem verschachtelte Blöcke ohne Rückwärtssuche funktionieren: Ein ``info``-Makro schiebt
    einen Puffer auf den Stapel, sein Inhalt läuft dort hinein, und beim Schließen wird das
    Gesammelte zeilenweise mit ``> `` versehen und in den darunterliegenden Puffer gelegt. Für
    Tabellenzellen und Zitatblöcke gilt dasselbe.
    """

    def __init__(self, links: ConfluenceLinks) -> None:
        super().__init__(convert_charrefs=True)
        self._links = links
        self._puffer: list[list[str]] = [[]]
        self._listen: list[tuple[str, int]] = []
        self._makros: list[_Makro] = []
        self._tabellen: list[_Tabelle] = []
        self._verweise: list[SourceReference] = []
        self._verwerfen = 0
        self._offener_link: PageLink | None = None
        self._link_text: list[str] = []
        self._offener_anhang: str | None = None

    # -- Ergebnis ----------------------------------------------------------------

    def ergebnis(self) -> ConvertedBody:
        """Der aufgeräumte Text und die gefundenen Verweise.

        Aufgeräumt wird zurückhaltend, weil im Markdown zwei Arten von Leerzeichen bedeutungslos
        aussehen und es nicht sind: Die Einrückung einer verschachtelten Liste trägt die
        Verschachtelung, und in einem Codeblock ist jedes Leerzeichen Inhalt. Deshalb bleibt der
        Zeilenanfang unberührt, und zwischen zwei ``​```-Zeilen wird gar nichts angefasst.
        """
        zeilen: list[str] = []
        im_code = False
        for zeile in "".join(self._puffer[0]).split("\n"):
            if zeile.lstrip().startswith("```"):
                im_code = not im_code
                zeilen.append(zeile.rstrip())
                continue
            if im_code:
                zeilen.append(zeile.rstrip())
                continue
            ohne_einzug = zeile.lstrip(_EINZUG)
            einzug = zeile[: len(zeile) - len(ohne_einzug)]
            zeilen.append((einzug + _LEERZEICHEN.sub(" ", ohne_einzug)).rstrip())
        text = _MEHRFACHE_LEERZEILEN.sub("\n\n", "\n".join(zeilen)).strip()
        return ConvertedBody(markdown=text, references=tuple(self._verweise))

    # -- Ausgabe -----------------------------------------------------------------

    def _schreiben(self, text: str) -> None:
        """Legt Text in den obersten Puffer — es sei denn, gerade wird verworfen."""
        if self._verwerfen:
            return
        if self._offener_link is not None:
            self._link_text.append(text)
            return
        if self._makros and self._makros[-1].aktueller_parameter is not None:
            makro = self._makros[-1]
            makro.parameter[makro.aktueller_parameter or ""] = (
                makro.parameter.get(makro.aktueller_parameter or "", "") + text
            )
            return
        self._puffer[-1].append(text)

    def _block(self) -> None:
        """Beginnt einen neuen Block, ohne Leerzeilen zu häufen."""
        vorhanden = "".join(self._puffer[-1])
        if not vorhanden.strip():
            return
        if not vorhanden.endswith("\n\n"):
            self._puffer[-1].append("\n" if vorhanden.endswith("\n") else "\n\n")

    def _puffer_auf(self) -> None:
        self._puffer.append([])

    def _puffer_ab(self) -> str:
        return "".join(self._puffer.pop()).strip()

    # -- Tags --------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        merkmale = {name: (wert or "") for name, wert in attrs}
        if tag in DROPPED:
            self._verwerfen += 1
            return
        if self._verwerfen:
            return

        if tag in INLINE_MARKS:
            self._schreiben(INLINE_MARKS[tag][0])
        elif tag == "p":
            self._block()
        elif tag == "br":
            self._schreiben("\n")
        elif tag == "hr":
            self._block()
            self._schreiben("---\n\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._block()
            self._schreiben(f"{'#' * int(tag[1])} ")
        elif tag in {"ul", "ol"}:
            # Nur die äußerste Liste beginnt einen Block. Eine verschachtelte gehört zum
            # Listenpunkt darüber; eine Leerzeile davor risse sie aus ihm heraus.
            if not self._listen:
                self._block()
            self._listen.append((tag, 0))
        elif tag == "li":
            self._listenpunkt()
        elif tag == "blockquote":
            self._block()
            self._puffer_auf()
        elif tag == "pre":
            self._block()
            self._schreiben("```\n")
        elif tag == "table":
            self._block()
            self._tabellen.append(_Tabelle())
        elif tag == "tr" and self._tabellen:
            self._tabellen[-1].zeilen.append([])
        elif tag in {"td", "th"} and self._tabellen:
            self._tabellen[-1].kopfzeile = self._tabellen[-1].kopfzeile or tag == "th"
            self._puffer_auf()
        elif tag == "a":
            self._link_beginnen(link_from_href(merkmale.get("href", "")))
        elif tag == "ac:structured-macro":
            self._makro_beginnen(merkmale.get("ac:name", ""))
        elif tag == "ac:parameter":
            if self._makros:
                self._makros[-1].aktueller_parameter = merkmale.get("ac:name", "")
        elif tag == "ac:link":
            self._link_beginnen(PageLink(anchor=merkmale.get("ac:anchor") or None))
        elif tag == "ri:page":
            self._ri_page(merkmale)
        elif tag == "ri:attachment":
            self._ri_attachment(merkmale)
        elif tag == "ri:url":
            self._ri_url(merkmale)
        elif tag == "ri:user":
            self._schreiben(f"@{merkmale.get('ri:userkey', merkmale.get('ri:account-id', ''))}")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED:
            self._verwerfen = max(0, self._verwerfen - 1)
            return
        if self._verwerfen:
            return

        if tag in INLINE_MARKS:
            self._schreiben(INLINE_MARKS[tag][1])
        elif tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._schreiben("\n\n")
        elif tag in {"ul", "ol"}:
            if self._listen:
                self._listen.pop()
            if not self._listen:
                self._schreiben("\n\n")
        elif tag == "li":
            # Nur, wenn nicht schon eine dasteht: Ein Punkt mit verschachtelter Unterliste
            # schließt zwei Ebenen hintereinander, und zwei Zeilenumbrüche wären eine Leerzeile
            # mitten in der Liste — die den Punkt darunter aus ihr herauslöste.
            if not "".join(self._puffer[-1]).endswith("\n"):
                self._schreiben("\n")
        elif tag == "blockquote":
            self._zitat_schliessen()
        elif tag == "pre":
            self._schreiben("\n```\n\n")
        elif tag in {"td", "th"} and self._tabellen:
            zelle = self._puffer_ab().replace("\n", " ").replace("|", "\\|")
            if self._tabellen[-1].zeilen:
                self._tabellen[-1].zeilen[-1].append(zelle)
        elif tag == "table":
            self._tabelle_schliessen()
        elif tag in {"a", "ac:link"}:
            self._link_schliessen()
        elif tag == "ac:structured-macro":
            self._makro_schliessen()
        elif tag == "ac:parameter" and self._makros:
            self._makros[-1].aktueller_parameter = None

    def handle_data(self, data: str) -> None:
        if self._verwerfen:
            return
        # Die Abfrage nach dem Parameter steht *vor* der nach dem Makro: Ein Code-Makro nimmt
        # jeden Text wörtlich — auch den Wert seines eigenen 'language'-Parameters, der dann als
        # erste Zeile im Codeblock stünde.
        if self._makros and self._makros[-1].aktueller_parameter is not None:
            self._schreiben(data)
            return
        if self._makros and self._makros[-1].name in CODE_MACROS:
            self._makros[-1].klartext.append(data)
            return
        text = data if self._in_klartext() else _LEERZEICHEN.sub(" ", data.replace("\n", " "))
        if text:
            self._schreiben(text)

    def unknown_decl(self, data: str) -> None:
        """Fängt ``<![CDATA[…]]>`` ein — dort steht der Inhalt der Code-Makros.

        ``html.parser`` kennt CDATA nicht als Konstrukt und meldet es als unbekannte Deklaration.
        Ohne diese Methode fiele der gesamte Inhalt jedes Codeblocks stillschweigend weg.
        """
        if data.startswith("CDATA["):
            inhalt = data[len("CDATA[") :]
            if self._makros:
                self._makros[-1].klartext.append(inhalt)
            else:
                self._schreiben(unescape(inhalt))

    # -- Listen, Zitate, Tabellen -------------------------------------------------

    def _in_klartext(self) -> bool:
        """Ob der gerade gelesene Text wörtlich zu übernehmen ist."""
        return bool(self._makros) and self._makros[-1].name in CODE_MACROS

    def _listenpunkt(self) -> None:
        """Schreibt die Marke eines Listenpunkts, eingerückt nach Verschachtelungstiefe."""
        if not self._listen:
            self._listen.append(("ul", 0))
        art, zaehler = self._listen[-1]
        zaehler += 1
        self._listen[-1] = (art, zaehler)
        einzug = "  " * (len(self._listen) - 1)
        marke = f"{zaehler}." if art == "ol" else "-"
        vorhanden = "".join(self._puffer[-1])
        if vorhanden and not vorhanden.endswith("\n"):
            self._schreiben("\n")
        self._schreiben(f"{einzug}{marke} ")

    def _zitat_schliessen(self) -> None:
        """Setzt jeder Zeile des gesammelten Inhalts ein ``> `` voran."""
        inhalt = self._puffer_ab()
        if inhalt:
            self._schreiben("\n".join(f"> {zeile}" for zeile in inhalt.split("\n")))
            self._schreiben("\n\n")

    def _tabelle_schliessen(self) -> None:
        """Gibt die gesammelte Tabelle als Markdown aus.

        Ohne Kopfzeile wird die erste Zeile zur Kopfzeile: Markdown kennt keine Tabelle ohne sie,
        und eine leere Kopfzeile läse sich schlechter als eine geliehene.
        """
        if not self._tabellen:
            return
        tabelle = self._tabellen.pop()
        zeilen = [zeile for zeile in tabelle.zeilen if zeile]
        if not zeilen:
            return
        breite = max(len(zeile) for zeile in zeilen)
        gefuellt = [zeile + [""] * (breite - len(zeile)) for zeile in zeilen]
        ausgabe = ["| " + " | ".join(gefuellt[0]) + " |"]
        ausgabe.append("| " + " | ".join(["---"] * breite) + " |")
        ausgabe.extend("| " + " | ".join(zeile) + " |" for zeile in gefuellt[1:])
        self._schreiben("\n".join(ausgabe) + "\n\n")

    # -- Makros -------------------------------------------------------------------

    def _makro_beginnen(self, name: str) -> None:
        self._makros.append(_Makro(name=name))
        if name in ADMONITIONS or name == "panel":
            self._block()
            self._puffer_auf()

    def _makro_schliessen(self) -> None:
        if not self._makros:
            return
        makro = self._makros.pop()
        if makro.name in CODE_MACROS:
            sprache = makro.parameter.get("language", "").strip()
            inhalt = "".join(makro.klartext).strip("\n")
            self._block()
            self._schreiben(f"```{sprache}\n{inhalt}\n```\n\n")
        elif makro.name in ADMONITIONS:
            inhalt = self._puffer_ab()
            beschriftung = ADMONITIONS[makro.name]
            self._zitat_mit_titel(f"**{beschriftung}:**", inhalt)
        elif makro.name == "panel":
            inhalt = self._puffer_ab()
            titel = makro.parameter.get("title", "").strip()
            self._zitat_mit_titel(f"**{titel}**" if titel else "", inhalt)

    def _zitat_mit_titel(self, titel: str, inhalt: str) -> None:
        """Gibt einen Zitatblock mit optionaler Beschriftung aus.

        Ohne Inhalt bleibt er weg — auch mit Beschriftung. Ein leeres ``info``-Makro kommt in
        gewachsenen Seiten vor, und ein alleinstehendes "**INFO:**" trägt keine Aussage; im
        Embedding wäre es reines Rauschen.
        """
        if not inhalt:
            return
        zeilen = [titel] if titel else []
        zeilen.extend(inhalt.split("\n") if inhalt else [])
        if not zeilen:
            return
        self._schreiben("\n".join(f"> {zeile}".rstrip() for zeile in zeilen) + "\n\n")

    # -- Verweise, Bilder, Anhänge ------------------------------------------------

    def _link_beginnen(self, link: PageLink) -> None:
        """Öffnet einen Link; sein Text wird bis zum Abschluss gesammelt."""
        if self._offener_link is not None:
            self._link_schliessen()
        self._offener_link = link
        self._link_text = []
        self._offener_anhang = None

    def _ri_page(self, merkmale: dict[str, str]) -> None:
        """``<ri:page>`` innerhalb eines ``<ac:link>`` — der Verweis über Space und Titel."""
        anker = self._offener_link.anchor if self._offener_link is not None else None
        if self._offener_link is None:
            self._link_beginnen(PageLink())
        self._offener_link = PageLink(
            space_key=merkmale.get("ri:space-key") or None,
            title=merkmale.get("ri:content-title") or None,
            anchor=anker,
        )

    def _ri_attachment(self, merkmale: dict[str, str]) -> None:
        """Ein Anhang — als Bild, wenn die Endung eines ist, sonst als Büroklammer-Link."""
        name = merkmale.get("ri:filename", "")
        if not name:
            return
        if self._offener_link is not None:
            # Der Linktext steht im Storage-Format *hinter* dem Ziel; ausgegeben wird deshalb
            # erst beim Schliessen, wenn beides vorliegt.
            self._offener_anhang = name
            return
        adresse = self._links.attachment_url(name)
        if name.lower().endswith(IMAGE_SUFFIXES):
            self._schreiben(f"![{name}]({adresse})")
        else:
            self._schreiben(f"📎 [{name}]({adresse})")

    def _ri_url(self, merkmale: dict[str, str]) -> None:
        """``<ri:url>`` — ein Bild oder Link auf eine Adresse außerhalb der Instanz."""
        adresse = merkmale.get("ri:value", "")
        if adresse:
            self._schreiben(f"![]({adresse})")

    def _link_schliessen(self) -> None:
        """Schreibt den Markdown-Link und merkt sich die Konzept-ID als Kandidaten."""
        link = self._offener_link
        if link is None:
            return
        self._offener_link = None
        text = "".join(self._link_text).strip()
        self._link_text = []
        anhang, self._offener_anhang = self._offener_anhang, None

        if anhang is not None:
            adresse = self._links.attachment_url(anhang)
            self._schreiben(f"📎 [{text or anhang}]({adresse})")
            return

        aufgeloest = self._links.resolve(link)
        beschriftung = text or link.title or aufgeloest.url
        if aufgeloest.url:
            self._schreiben(f"[{beschriftung}]({aufgeloest.url})")
        else:
            self._schreiben(beschriftung)

        if aufgeloest.concept_id is not None:
            self._merken(aufgeloest.concept_id)

    def _merken(self, concept_id: str) -> None:
        """Nimmt eine Konzept-ID einmal in die Kandidatenliste auf."""
        if all(vorhanden.target != concept_id for vorhanden in self._verweise):
            self._verweise.append(
                SourceReference(target=concept_id, kind=defaults.EDGE_KIND_REFERENCES)
            )
