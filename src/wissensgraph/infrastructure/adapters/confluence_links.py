"""Verweise im Confluence-Storage-Format erkennen und auflösen (§8.5).

Eine Confluence-Seite verlinkt eine andere auf mindestens vier Weisen, und keine davon nennt die
Seiten-ID so, wie der Graph sie braucht:

* als Makro-Element ``<ri:page ri:space-key="ENG" ri:content-title="Titel"/>`` — Space und Titel,
  keine ID,
* als Anzeigepfad ``/display/ENG/Titel`` — dasselbe in einer URL,
* als Kurzlink ``/x/AwCd`` — die ID, aber base64-kodiert,
* als Seitenaufruf ``/pages/viewpage.action?pageId=123456`` oder als Cloud-Pfad
  ``/spaces/ENG/pages/123456/Titel`` — die ID im Klartext.

Dieses Modul übersetzt alle vier in dieselbe Form. Drei davon sind reine Formatumwandlung; nur
der Weg über Space und Titel braucht einen Blick in die Instanz, und dafür gibt es die
Titelsuche, die der Adapter einmal je Lauf aufbaut und zwischenspeichert.

**Der Text wird dabei nicht zur Kandidatenliste.** Im Markdown bleibt ein gewöhnlicher, klickbarer
Link auf die Quellseite stehen — er soll auch dann noch funktionieren, wenn die Zielseite nie
synchronisiert wird. Die Konzept-ID geht getrennt davon als Referenz an den Kern, wo sie nach der
üblichen Regel aus §8.5 zur Kante wird: Gibt es das Ziel, ist sie aufgelöst; gibt es es nicht,
entsteht die Kante trotzdem, mit ``resolved = false``, und jeder folgende Lauf versucht es erneut.
Ein nachträgliches Umschreiben des ``body`` — wie es eine ``[[id]]``-Ersetzung nötig machte —
findet nicht statt; §7.6 und §10.4 stünden dem entgegen.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from wissensgraph.domain.ids import source_concept_id

#: Pfadmuster, aus denen sich eine Seiten-ID direkt ablesen lässt.
_VIEWPAGE = re.compile(r"[?&]pageId=(\d+)")
_CLOUD_PAGES = re.compile(r"/pages/(\d+)(?:/|$)")
_DISPLAY = re.compile(r"/display/([^/?#]+)/([^/?#]+)")
_TINY = re.compile(r"/x/([A-Za-z0-9_+/=-]+)")

#: Länge des Ganzzahlpuffers, aus dem Confluence einen Kurzlink baut: acht Bytes, little-endian.
_TINY_BYTES = 8


@dataclass(frozen=True)
class PageLink:
    """Ein im Storage-Format gefundener Verweis, vor der Auflösung.

    Alle Felder sind optional, weil jede der Linkformen etwas anderes weiß. Genau deshalb gibt es
    diesen Zwischenschritt: Der Parser trägt zusammen, was dasteht, und entscheidet nichts.
    """

    page_id: str | None = None
    space_key: str | None = None
    title: str | None = None
    url: str | None = None
    anchor: str | None = None

    @property
    def is_external(self) -> bool:
        """Ob der Verweis aus der Instanz herausführt und deshalb nur ein Link bleibt."""
        return self.page_id is None and self.title is None


@dataclass(frozen=True)
class ResolvedLink:
    """Das Ergebnis der Auflösung: ein Link für den Text, eine ID für den Graphen."""

    url: str
    concept_id: str | None = None


TitleLookup = Callable[[str, str], str | None]
"""Sucht die Seiten-ID zu Space und Titel; ``None``, wenn es sie nicht gibt."""


def page_id_from_tiny(token: str) -> str | None:
    """Dekodiert einen Confluence-Kurzlink (``/x/AwCd``) zur Seiten-ID.

    Confluence bildet ihn, indem es die ID als acht Bytes little-endian schreibt, die hinteren
    Nullbytes abschneidet und den Rest URL-sicher base64-kodiert. Der Rückweg ist derselbe Weg
    rückwärts — und er kommt ohne Netzzugriff aus, was ihn hier wertvoll macht: Ein Kurzlink lässt
    sich auflösen, ohne die Instanz zu fragen.

    Returns:
        Die Seiten-ID, oder ``None``, wenn der Token keiner ist. Ein unlesbarer Verweis ist kein
        Fehler (§8.5) — er bleibt einfach ein Link ohne Kante.
    """
    roh = token.replace("-", "+").replace("_", "/")
    roh += "=" * (-len(roh) % 4)
    try:
        rohbytes = base64.b64decode(roh, validate=True)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None
    if not rohbytes or len(rohbytes) > _TINY_BYTES:
        return None
    gefuellt = rohbytes.ljust(_TINY_BYTES, b"\x00")
    wert = int.from_bytes(gefuellt, "little")
    return str(wert) if wert > 0 else None


def link_from_href(href: str) -> PageLink:
    """Liest aus einer URL heraus, auf welche Seite sie zeigt.

    Die Reihenfolge der Muster ist die ihrer Aussagekraft: Eine ausgeschriebene ID schlägt einen
    Kurzlink, und beide schlagen den Weg über Space und Titel, der als einziger eine Suche
    kostet.
    """
    teile = urlsplit(href)
    pfad = teile.path
    anker = teile.fragment or None

    if treffer := _VIEWPAGE.search(href):
        return PageLink(page_id=treffer.group(1), url=href, anchor=anker)
    if treffer := _CLOUD_PAGES.search(pfad):
        return PageLink(page_id=treffer.group(1), url=href, anchor=anker)
    if treffer := _TINY.search(pfad):
        seite = page_id_from_tiny(treffer.group(1))
        if seite is not None:
            return PageLink(page_id=seite, url=href, anchor=anker)
    if treffer := _DISPLAY.search(pfad):
        return PageLink(
            space_key=unquote(treffer.group(1)),
            title=unquote(treffer.group(2)).replace("+", " "),
            url=href,
            anchor=anker,
        )
    if teile.query and (seiten := parse_qs(teile.query).get("pageId")):
        return PageLink(page_id=seiten[0], url=href, anchor=anker)
    return PageLink(url=href, anchor=anker)


class ConfluenceLinks:
    """Die Auflösung der Verweise *einer* Seite (§8.5, Phase A).

    Sie ist bewusst an eine Seite gebunden: Anhänge hängen an einer Seiten-ID, und relative Links
    beziehen sich auf sie. Der Aufbau ist billig, die teure Hälfte — die Titelsuche — steckt in
    der übergebenen Funktion und wird vom Adapter über den ganzen Lauf hinweg geteilt.
    """

    def __init__(
        self,
        *,
        id_prefix: str,
        web_base_url: str,
        page_id: str | None = None,
        space_key: str | None = None,
        lookup: TitleLookup | None = None,
    ) -> None:
        """
        Args:
            id_prefix: Das ``id_prefix`` der Quelle — damit wird aus einer Seiten-ID eine
                Konzept-ID (§7.5).
            web_base_url: Die Adresse, unter der ein Mensch die Instanz aufruft. Sie ist nicht
                die ``base_url`` der API: Hinter einem Gateway sind das zwei verschiedene Hosts,
                und ein Link auf die API-Adresse führte für einen Leser ins Leere.
            page_id: Die Seite, um deren Inhalt es geht — Bezugspunkt für Anhänge.
            space_key: Der Space dieser Seite; Vorgabe für Verweise, die keinen nennen.
            lookup: Sucht die Seiten-ID zu Space und Titel. Ohne sie bleiben solche Verweise
                Links ohne Kante — der Text ist trotzdem vollständig.
        """
        self._prefix = id_prefix
        self._web = web_base_url.rstrip("/")
        self._page_id = page_id
        self._space = space_key
        self._lookup = lookup

    # -- Auflösung ---------------------------------------------------------------

    def resolve(self, link: PageLink) -> ResolvedLink:
        """Macht aus einem gefundenen Verweis einen Link und, wenn möglich, eine Konzept-ID."""
        seite = self._seiten_id(link)
        url = self._url(link, seite)
        if seite is None:
            return ResolvedLink(url=url)
        return ResolvedLink(url=url, concept_id=source_concept_id(self._prefix, seite))

    def attachment_url(self, filename: str, page_id: str | None = None) -> str:
        """Die Adresse eines Seitenanhangs.

        Sie zeigt auf die Weboberfläche und nicht auf die API: Der Anhang soll sich anklicken
        lassen, und der API-Pfad verlangte Zugangsdaten, die in einem Markdown-Link nichts zu
        suchen haben (§20.2).
        """
        bezug = page_id or self._page_id or ""
        return f"{self._web}/download/attachments/{bezug}/{filename}"

    def page_url(self, page_id: str) -> str:
        """Die Adresse einer Seite zu ihrer ID."""
        return f"{self._web}/pages/viewpage.action?pageId={page_id}"

    # -- innere Schritte ----------------------------------------------------------

    def _seiten_id(self, link: PageLink) -> str | None:
        """Die Seiten-ID eines Verweises; ``None``, wenn er aus der Instanz herausführt."""
        if link.page_id is not None:
            return link.page_id
        if link.title is None or self._lookup is None:
            return None
        space = link.space_key or self._space
        if not space:
            return None
        return self._lookup(space, link.title)

    def _url(self, link: PageLink, page_id: str | None) -> str:
        """Die Adresse, die im Markdown stehen bleibt.

        Für eine bekannte Seite die kanonische Adresse — sie überlebt eine Umbenennung, ein
        Titelpfad tut das nicht. Sonst das, was in der Quelle stand.
        """
        anker = f"#{link.anchor}" if link.anchor else ""
        if page_id is not None:
            return f"{self.page_url(page_id)}{anker}"
        if link.url:
            return self._absolut(link.url)
        if link.title and (link.space_key or self._space):
            space = link.space_key or self._space
            return f"{self._web}/display/{space}/{link.title.replace(' ', '+')}{anker}"
        return link.title or ""

    def _absolut(self, url: str) -> str:
        """Ergänzt einen instanzrelativen Pfad um die Weboberflächen-Adresse."""
        if url.startswith(("http://", "https://", "mailto:", "#")):
            return url
        return f"{self._web}/{url.lstrip('/')}"
