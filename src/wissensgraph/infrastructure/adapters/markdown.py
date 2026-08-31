"""Das gemeinsame Ergebnis der Formatumwandlungen (§8.2).

Confluence liefert XHTML, Jira liefert Wiki-Markup, und beide sollen dasselbe hinterlassen:
lesbaren Text plus die Verweise, die darin steckten. Dass beide Umwandlungen denselben Rückgabetyp
haben, ist kein Zufall, sondern die Aussage — der Kern sieht ab hier keinen Unterschied mehr.
"""

from __future__ import annotations

from dataclasses import dataclass

from wissensgraph.domain.references import SourceReference


@dataclass(frozen=True)
class ConvertedBody:
    """Fertiger Markdown-Text und die darin gefundenen Verweise.

    Die Verweise stehen **neben** dem Text und nicht in ihm. Im Markdown bleibt ein gewöhnlicher
    Link auf die Quelle stehen, der auch dann noch funktioniert, wenn das Ziel nie synchronisiert
    wird; die Konzept-ID geht getrennt an den Kern, der daraus nach §8.5 eine Kante macht — sofort
    aufgelöst oder mit ``resolved = false`` und einem neuen Versuch bei jedem Lauf.

    Diese Trennung ist der Grund, warum der ``body`` nach dem ersten Schreiben nie wieder angefasst
    werden muss. Ein nachträgliches Einsetzen von ``[[id]]`` wäre eine inhaltliche Änderung an
    einem gespiegelten Text, und die beantwortet das System bewusst mit einem Konflikt (§10.4).
    """

    markdown: str = ""
    references: tuple[SourceReference, ...] = ()
