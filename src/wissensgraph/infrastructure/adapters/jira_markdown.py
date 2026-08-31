"""Jira-Wiki-Markup nach Markdown (§8.2).

Jira Data Center speichert Beschreibungen und Kommentare als Wiki-Markup — eine zeilenorientierte
Auszeichnung, die dem Markdown ähnelt und ihm genau dort widerspricht, wo es weh tut: ``*fett*``
statt ``**fett**``, ``#`` für die *nummerierte* statt für die Überschrift, ``-durchgestrichen-``
mit einem Zeichen, das im Markdown eine Aufzählung beginnt. Ungewandelt gelesen ergibt das keinen
kaputten, sondern einen *falschen* Text: Aus einer nummerierten Liste würde eine Reihe von
Überschriften.

**Warum zeilenweise und nicht mit einem Parser.** Wiki-Markup hat keine Grammatik, an die sich
eine gewachsene Instanz hielte; es ist eine Sammlung von Ersetzungsregeln, und genau so wird es
hier behandelt. Die Reihenfolge trägt die Korrektheit: Erst werden Codeblöcke *herausgenommen* und
durch Platzhalter ersetzt, dann läuft alles Weitere, und ganz am Ende kommen sie unverändert
zurück. Ohne diesen Schritt machte die Regel für ``*fett*`` aus einem C-Zeiger im Codebeispiel
eine Auszeichnung.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from wissensgraph.config import defaults
from wissensgraph.domain.ids import source_concept_id
from wissensgraph.domain.references import SourceReference
from wissensgraph.infrastructure.adapters.markdown import ConvertedBody

#: Ein Vorgangsschlüssel, wie ihn Jira vergibt: Projektkürzel, Bindestrich, Nummer.
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9_]{1,15}-\d+)\b")

#: Blockmakros, die zu einem Zitatblock werden. Der Wert ist die Beschriftung.
ADMONITIONS: dict[str, str] = {
    "info": "INFO",
    "tip": "TIPP",
    "note": "HINWEIS",
    "warning": "ACHTUNG",
}

_CODE_BLOCK = re.compile(r"\{(code|noformat)(?::([^}]*))?\}(.*?)\{\1\}", re.DOTALL | re.IGNORECASE)
_PLATZHALTER = "\x00CODE{index}\x00"

_UEBERSCHRIFT = re.compile(r"^h([1-6])\.\s*(.*)$")
_LISTE = re.compile(r"^([*#-]+)\s+(.*)$")
_ZITATZEILE = re.compile(r"^bq\.\s*(.*)$")
_TABELLENKOPF = re.compile(r"^\s*\|\|(.+)\|\|\s*$")
_TABELLENZEILE = re.compile(r"^\s*\|(.+)\|\s*$")
_BLOCKMAKRO = re.compile(r"^\{(quote|panel|info|tip|note|warning)(?::([^}]*))?\}\s*$", re.I)
_BLOCKMAKRO_ENDE = re.compile(r"^\{(quote|panel|info|tip|note|warning)\}\s*$", re.I)

_FARBE = re.compile(r"\{color:[^}]*\}(.*?)\{color\}", re.DOTALL | re.IGNORECASE)
_MONOSPACE = re.compile(r"\{\{(.+?)\}\}")
_BILD = re.compile(r"!([^!|\s]+?)(?:\|[^!]*)?!")
_ERWAEHNUNG = re.compile(r"\[~([^\]]+)\]")
_LINK = re.compile(r"\[([^\]|]*)\|([^\]]+)\]")
# Zwei Absicherungen, beide gegen Schäden am eigenen Ergebnis:
# ``(?!\()`` — ohne es griffe diese Regel auf das Ergebnis der vorigen zu und machte aus
#   ``[Text](url)`` ein ``[Text](Text)(url)``.
# ``(?<!\[)`` und ``(?!\])`` — sie halten die doppelten Klammern der eigenen Referenzsyntax
#   heraus. ``[[confluence:100002]]`` ist kein Jira-Link, sondern ein Verweis nach §7.1, den der
#   Kern später selbst liest; hier angefasst käme er dort nie an.
_NACKTER_LINK = re.compile(r"(?<!\[)\[([^\]|\s]+)\](?!\()(?!\])")

#: Inline-Auszeichnungen als Paare (Muster, Ersetzung). Die Reihenfolge zählt: ``*fett*`` vor
#: ``_kursiv_``, weil ein Unterstrich innerhalb einer fetten Passage sonst zuerst zuschlüge.
_INLINE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])"), r"**\1**"),
    (re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])"), r"*\1*"),
    (re.compile(r"(?<![\w-])-([^\-\n]+)-(?![\w-])"), r"~~\1~~"),
    (re.compile(r"(?<![\w+])\+([^+\n]+)\+(?![\w+])"), r"<u>\1</u>"),
    # Hoch- und Tiefstellung stehen anders als die übrigen Auszeichnungen *mitten im Wort*
    # (``x^2^``, ``H~2~O``); eine Wortgrenze zu verlangen brächte sie um ihren einzigen Zweck.
    # Das Zeichen selbst bleibt trotzdem ausgeschlossen, sonst zerlegte die Tiefstellung das
    # ``~~``, das die Regel darüber gerade erzeugt hat.
    (re.compile(r"(?<!\^)\^([^^\n]+)\^(?!\^)"), r"<sup>\1</sup>"),
    (re.compile(r"(?<!~)~([^~\n]+)~(?!~)"), r"<sub>\1</sub>"),
)

IssueLookup = Callable[[str], bool]
"""Sagt, ob ein Vorgangsschlüssel zu dieser Instanz gehört. Ohne sie gilt jeder als zugehörig."""


class JiraLinks:
    """Die Auflösung der Verweise eines Vorgangs.

    Anders als bei Confluence ist hier keine Suche nötig: Ein Vorgangsschlüssel *ist* die stabile
    ID, mit der §22.3 arbeitet, und die Konzept-ID entsteht daraus durch reines Voranstellen des
    Präfixes. Deshalb kann die Auflösung ohne einen einzigen Netzaufruf laufen.
    """

    def __init__(self, *, id_prefix: str, web_base_url: str) -> None:
        self._prefix = id_prefix
        self._web = web_base_url.rstrip("/")

    def issue_url(self, key: str) -> str:
        """Die Adresse, unter der ein Mensch den Vorgang aufruft."""
        return f"{self._web}/browse/{key}"

    def concept_id(self, key: str) -> str:
        """Die Konzept-ID zu einem Vorgangsschlüssel (§7.5)."""
        return source_concept_id(self._prefix, key)


def wiki_to_markdown(text: str | None, links: JiraLinks) -> ConvertedBody:
    """Wandelt Jira-Wiki-Markup in Markdown und sammelt die verlinkten Vorgänge.

    Args:
        text: Der Rohtext aus ``fields.description`` oder einem Kommentar; ``None`` ist zulässig.
        links: Die Auflösung der Verweise dieses Vorgangs.

    Returns:
        Den Markdown-Text und die Konzept-IDs, auf die er zeigt.
    """
    if not text:
        return ConvertedBody()

    roh, bloecke = _codebloecke_herausnehmen(text)
    verweise: list[str] = []
    zeilen = _bloecke_umformen(roh.replace("\r\n", "\n").split("\n"))
    umgeformt = [_inline(zeile, links, verweise) for zeile in zeilen]
    ergebnis = _codebloecke_einsetzen("\n".join(umgeformt), bloecke)

    return ConvertedBody(
        markdown=re.sub(r"\n{3,}", "\n\n", ergebnis).strip(),
        references=tuple(
            SourceReference(target=ziel, kind=defaults.EDGE_KIND_REFERENCES) for ziel in verweise
        ),
    )


# ---------------------------------------------------------------------------
# Codeblöcke
# ---------------------------------------------------------------------------


def _codebloecke_herausnehmen(text: str) -> tuple[str, list[str]]:
    """Ersetzt ``{code}``- und ``{noformat}``-Blöcke durch Platzhalter.

    Der Platzhalter benutzt das Nullzeichen, weil es in einem Jira-Feld nicht vorkommt und von
    keiner der folgenden Regeln erfasst wird. Ein Platzhalter aus gewöhnlichen Zeichen könnte
    selbst umgeformt werden — und dann käme der Codeblock nicht zurück.
    """
    bloecke: list[str] = []

    def ersetzen(treffer: re.Match[str]) -> str:
        sprache = (treffer.group(2) or "").split("|")[0].strip()
        if "=" in sprache:  # {code:title=X} nennt keine Sprache, sondern einen Titel
            sprache = ""
        inhalt = treffer.group(3).strip("\n")
        bloecke.append(f"```{sprache}\n{inhalt}\n```")
        return _PLATZHALTER.format(index=len(bloecke) - 1)

    return _CODE_BLOCK.sub(ersetzen, text), bloecke


def _codebloecke_einsetzen(text: str, bloecke: list[str]) -> str:
    """Setzt die herausgenommenen Codeblöcke wieder ein."""
    for index, block in enumerate(bloecke):
        text = text.replace(_PLATZHALTER.format(index=index), f"\n{block}\n")
    return text


# ---------------------------------------------------------------------------
# Blockstruktur
# ---------------------------------------------------------------------------


def _bloecke_umformen(zeilen: list[str]) -> list[str]:
    """Überschriften, Listen, Tabellen, Zitate und Blockmakros — Zeile für Zeile."""
    ausgabe: list[str] = []
    zitat: str | None = None

    for zeile in zeilen:
        if (ende := _BLOCKMAKRO_ENDE.match(zeile)) and zitat == ende.group(1).lower():
            zitat = None
            ausgabe.append("")
            continue
        if (start := _BLOCKMAKRO.match(zeile)) and zitat is None:
            zitat = start.group(1).lower()
            ausgabe.append("")
            ueberschrift = _makro_ueberschrift(zitat, start.group(2) or "")
            if ueberschrift:
                ausgabe.append(f"> {ueberschrift}")
            continue

        umgeformt = _zeile_umformen(zeile)
        ausgabe.append(f"> {umgeformt}".rstrip() if zitat is not None else umgeformt)

    return ausgabe


def _makro_ueberschrift(name: str, parameter: str) -> str:
    """Die Beschriftung eines Blockmakros: sein Titel, sonst sein Name."""
    if name == "panel":
        titel = ""
        for teil in parameter.split("|"):
            schluessel, _, wert = teil.partition("=")
            if schluessel.strip().lower() == "title":
                titel = wert.strip()
        return f"**{titel}**" if titel else ""
    if name in ADMONITIONS:
        return f"**{ADMONITIONS[name]}:**"
    return ""


def _zeile_umformen(zeile: str) -> str:
    """Die Blockregeln einer einzelnen Zeile."""
    if treffer := _UEBERSCHRIFT.match(zeile):
        return f"{'#' * int(treffer.group(1))} {treffer.group(2)}"
    if treffer := _ZITATZEILE.match(zeile):
        return f"> {treffer.group(1)}"
    if treffer := _TABELLENKOPF.match(zeile):
        zellen = [zelle.strip() for zelle in treffer.group(1).split("||")]
        kopf = "| " + " | ".join(zellen) + " |"
        return kopf + "\n| " + " | ".join(["---"] * len(zellen)) + " |"
    if treffer := _TABELLENZEILE.match(zeile):
        zellen = [zelle.strip() for zelle in treffer.group(1).split("|")]
        return "| " + " | ".join(zellen) + " |"
    if treffer := _LISTE.match(zeile):
        return _listenpunkt(treffer.group(1), treffer.group(2))
    return zeile


def _listenpunkt(marken: str, inhalt: str) -> str:
    """Übersetzt die Markenfolge einer Wiki-Liste in Einrückung und Markdown-Marke.

    Jira drückt die Tiefe durch Wiederholung aus (``**`` ist die zweite Ebene), Markdown durch
    Einrückung. Maßgeblich für die Art ist das **letzte** Zeichen: ``*#`` ist ein nummerierter
    Punkt innerhalb einer Aufzählung.
    """
    tiefe = len(marken) - 1
    marke = "1." if marken[-1] == "#" else "-"
    return f"{'  ' * tiefe}{marke} {inhalt}"


# ---------------------------------------------------------------------------
# Inline
# ---------------------------------------------------------------------------


def _inline(zeile: str, links: JiraLinks, verweise: list[str]) -> str:
    """Die Inline-Regeln und die Linkauflösung einer Zeile.

    Codeblöcke sind zu diesem Zeitpunkt Platzhalter; eine Zeile, die nur aus einem besteht, bleibt
    deshalb unangetastet, ohne dass es dafür einen Sonderfall bräuchte.
    """
    if "\x00CODE" in zeile:
        return zeile

    zeile = _FARBE.sub(r"\1", zeile)
    zeile = _MONOSPACE.sub(r"`\1`", zeile)
    zeile = _ERWAEHNUNG.sub(r"@\1", zeile)
    zeile = _BILD.sub(r"![\1](\1)", zeile)
    zeile = _links_umformen(zeile, links, verweise)
    for muster, ersatz in _INLINE:
        zeile = muster.sub(ersatz, zeile)
    return zeile


def _links_umformen(zeile: str, links: JiraLinks, verweise: list[str]) -> str:
    """Übersetzt ``[Text|Ziel]`` und ``[Ziel]`` und merkt sich verlinkte Vorgänge.

    Ein Vorgangsschlüssel wird zum Link auf die Weboberfläche und zusätzlich zum Kandidaten für
    eine Kante. Eine externe Adresse wird nur zum Link — sie zeigt aus dem Graphen heraus, und
    eine Kante auf ein Konzept, das es nirgends gibt, wäre eine Behauptung (§8.5).
    """

    def mit_text(treffer: re.Match[str]) -> str:
        text, ziel = treffer.group(1).strip(), treffer.group(2).strip()
        return _link(text or ziel, ziel, links, verweise)

    def ohne_text(treffer: re.Match[str]) -> str:
        ziel = treffer.group(1).strip()
        return _link(ziel, ziel, links, verweise)

    zeile = _LINK.sub(mit_text, zeile)
    return _NACKTER_LINK.sub(ohne_text, zeile)


def _link(text: str, ziel: str, links: JiraLinks, verweise: list[str]) -> str:
    """Ein einzelner Link, samt Kandidatenvermerk, falls er auf einen Vorgang zeigt."""
    if ziel.startswith(("http://", "https://", "mailto:", "#")):
        return f"[{text}]({ziel})"
    if ISSUE_KEY.fullmatch(ziel):
        konzept = links.concept_id(ziel)
        if konzept not in verweise:
            verweise.append(konzept)
        return f"[{text}]({links.issue_url(ziel)})"
    return f"[{text}]({ziel})"
