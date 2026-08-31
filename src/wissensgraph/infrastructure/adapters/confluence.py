"""Adapter für Confluence (§8, §9.1).

Er spricht in der Entwicklung mit dem Mock-Server und im Betrieb mit der echten Instanz — der
Unterschied ist ``connection.base_url`` und ein gültiges Token (§9.4). Genau das ist der Sinn der
Übung: Paginierung, Fehlerbehandlung und Rate-Limit-Logik laufen in der Entwicklung wirklich, und
nicht erst dann, wenn niemand mehr zusieht.

Der Adapter kennt die Struktur der Confluence-Antwort und setzt daraus seine Vorgaben. Was in der
``mapping:``-Sektion einer Quelle steht, schlägt sie (§8.4) — eine Instanz mit abweichender
Konfiguration, etwa einer eigenen Ausgabe für ``description``, braucht dafür keinen neuen Adapter.

**Drei Dinge, die erst der Betrieb gegen eine echte Instanz gelehrt hat.**

*Der Pfad ist nicht überall derselbe.* Eine Standardinstallation antwortet unter ``/rest/api``.
Ein API-Gateway davor bietet dieselben Endpunkte ohne dieses Präfix an, weil seine eigene
``base_url`` bereits auf die API zeigt. Deshalb ist das Präfix konfigurierbar (§6.1 Regel 1) und
steht nicht als Literal in diesem Modul.

*Das Feld ``expand`` entscheidet, ob überhaupt Inhalt kommt.* Ohne es liefert Confluence eine
Seite ohne ``body``, ohne ``version`` und ohne Labels — technisch eine gültige Antwort, inhaltlich
eine leere. Ein Sync-Lauf darüber meldete lauter erfolgreich angelegte Konzepte ohne Text.

*Labels stehen an zwei Stellen.* Die Datacenter-API verschachtelt sie unter
``metadata.labels.results``, ältere und nachgebildete Antworten führen ``metadata.labels`` direkt
als Liste. Beide zu lesen kostet drei Zeilen; nur eine zu lesen kostet still sämtliche Tags.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.infrastructure.adapters.base import HttpSourceAdapter
from wissensgraph.infrastructure.adapters.confluence_links import ConfluenceLinks
from wissensgraph.infrastructure.adapters.confluence_markdown import storage_to_markdown
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    SourceDocument,
    SourceError,
    SourceObjectNotFound,
)

_log = get_logger(__name__)

#: Pfade der benutzten Endpunkte, relativ zum konfigurierten Präfix. Sie sind die Schnittstelle
#: zur Quelle und stehen deshalb beieinander.
PATH_SPACES = "space"
PATH_CONTENT = "content"
PATH_SEARCH = "content/search"
PATH_DELETED = "content/deleted"

#: Vorgabepräfix einer Standardinstallation.
DEFAULT_API_PREFIX = "/rest/api"

#: Was Confluence mitliefern soll. Ohne diese Angabe kommt eine Seite ohne Inhalt zurück.
EXPAND = "body.storage,version,space,metadata.labels"

#: Schlüssel der Auswahl in ``sources.yaml`` (§8.4: ``selection.spaces``).
SELECTION_SPACES = "spaces"
SELECTION_EXCLUDE_LABELS = "exclude_labels"


class ConfluenceAdapter(HttpSourceAdapter):
    """Liest Seiten aus Confluence."""

    name = defaults.ADAPTER_CONFLUENCE
    default_api_prefix = DEFAULT_API_PREFIX
    capabilities = AdapterCapabilities(
        incremental=True, deletions=True, single_fetch=True, references=True
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Die Titelsuche ist der einzige Schritt der Linkauflösung, der die Instanz fragen muss.
        # Der Zwischenspeicher lebt so lange wie der Adapter, also einen Lauf lang: Innerhalb
        # eines Laufs verweisen viele Seiten auf dieselben wenigen Zielseiten, über Läufe hinweg
        # dürfte sich die Zuordnung geändert haben.
        self._titel_cache: dict[tuple[str, str], str | None] = {}

    def health_path(self) -> str:
        """Die Space-Liste ist der billigste Aufruf, der Erreichbarkeit *und* Auth prüft."""
        return self.api_path(PATH_SPACES)

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle ausgewählten Seiten, seitenweise geholt (§8.2 Regel 1).

        Ein Generator über einen Generator: Die innere Funktion holt Seite für Seite, die äußere
        (:meth:`~BaseAdapter._durchreichen`) filtert nach dem Cursor und schreibt die neue Marke
        erst fort, wenn wirklich alles gelesen wurde.
        """
        since = self.cursor_since(cursor)
        return self._durchreichen(self._seiten(since), since)

    def _seiten(self, since: datetime | None) -> Iterator[SourceDocument]:
        """Blättert durch ``content`` und wandelt jede Seite in ein Dokument."""
        auswahl = self.config.selection
        limit = self.config.connection.page_size
        start = 0
        while True:
            parameter: dict[str, Any] = {"start": start, "limit": limit, "expand": EXPAND}
            if auswahl.get(SELECTION_SPACES):
                parameter["spaceKey"] = list(auswahl[SELECTION_SPACES])
            if since is not None:
                parameter["since"] = since.isoformat()

            antwort = self.get(self.api_path(PATH_CONTENT), parameter)
            treffer = list(antwort.get("results", []))
            if not treffer:
                return
            for seite in treffer:
                if not _ausgeschlossen(seite, auswahl.get(SELECTION_EXCLUDE_LABELS, ())):
                    yield self._als_dokument(seite)
            start += len(treffer)
            if start >= int(antwort.get("totalSize", antwort.get("size", start))):
                return

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die IDs der gelöschten Seiten (``capabilities.deletions``)."""
        antwort = self.get(self.api_path(PATH_DELETED))
        for eintrag in antwort.get("results", []):
            yield str(eintrag["id"])

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Eine einzelne Seite (``capabilities.single_fetch``)."""
        try:
            seite = self.get(self.api_path(PATH_CONTENT, external_id), {"expand": EXPAND})
        except SourceObjectNotFound:
            return None
        return self._als_dokument(seite)

    # -- Übersetzung ---------------------------------------------------------------

    def _als_dokument(self, seite: dict[str, Any]) -> SourceDocument:
        """Übersetzt eine Confluence-Seite in das quellneutrale DTO (§8.2)."""
        seiten_id = str(seite["id"])
        inhalt = storage_to_markdown(_storage(seite), self._links(seite))

        # Zwei Herkünfte, eine Liste: Was der Parser im Text gefunden hat, und was die Antwort
        # als Verweisliste mitbringt. Der Kern entdoppelt sie ohnehin (§8.5); sie hier zu
        # trennen brächte nichts, sie hier zu verlieren aber schon.
        verweise = [*inhalt.references, *_gemeldete_verweise(seite)]

        werte: dict[str, Any] = {
            "external_id": seiten_id,
            "title": seite.get("title"),
            "description": seite.get("excerpt"),
            "body": inhalt.markdown or None,
            "resource": self._seiten_adresse(seite, seiten_id),
            "tags": _labels(seite),
            "updated_at": seite.get("version", {}).get("when"),
            "references": verweise,
            "extra": {"space": _space_key(seite)},
        }
        werte.update(self.mapping.apply(seite))
        return SourceDocument.model_validate(werte)

    def _links(self, seite: dict[str, Any]) -> ConfluenceLinks:
        """Die Linkauflösung für genau diese Seite."""
        return ConfluenceLinks(
            id_prefix=self.config.id_prefix,
            web_base_url=self.config.connection.web_url,
            page_id=str(seite["id"]),
            space_key=_space_key(seite),
            lookup=self._titel_suchen,
        )

    def _seiten_adresse(self, seite: dict[str, Any], seiten_id: str) -> str | None:
        """Die Adresse, unter der ein Mensch die Seite aufruft (``resource``, §7.1)."""
        webui = seite.get("links", {}).get("webui") or seite.get("_links", {}).get("webui")
        basis = self.config.connection.web_url
        if webui:
            return f"{basis}{webui}" if str(webui).startswith("/") else str(webui)
        return f"{basis}/pages/viewpage.action?pageId={seiten_id}" if basis else None

    def _titel_suchen(self, space: str, titel: str) -> str | None:
        """Sucht die Seiten-ID zu Space und Titel — einmal je Paar und Lauf.

        Die Suche läuft **faul**: Erst wenn ein Verweis über Space und Titel wirklich vorkommt,
        wird gefragt. Die naheliegende Alternative — einmal je Lauf sämtliche Titel aller Spaces
        holen und eine Tabelle aufbauen — kostet bei einem großen Space hunderte Anfragen, von
        denen die allermeisten nie gebraucht werden. Die Kosten stehen hier in Verhältnis zur
        Zahl der tatsächlich verlinkten Seiten und nicht zur Größe des Bestands.

        Eine erfolglose Suche wird ebenfalls gemerkt. Sonst fragte eine Seite, die zwanzigmal auf
        eine gelöschte Zielseite verweist, zwanzigmal danach.
        """
        schluessel = (space, titel)
        if schluessel in self._titel_cache:
            return self._titel_cache[schluessel]

        gefunden: str | None = None
        try:
            antwort = self.get(
                self.api_path(PATH_SEARCH),
                {"cql": f'space="{space}" and title="{_cql_escape(titel)}"', "limit": 1},
            )
            treffer = list(antwort.get("results", []))
            if treffer:
                gefunden = str(treffer[0]["id"])
        except (SourceError, KeyError, TypeError) as exc:
            # Eine gescheiterte Titelsuche darf keinen Lauf kosten: Sie entscheidet über eine
            # Kante, nicht über den Inhalt. §8.5 ist hier eindeutig — kaputte Referenzen sind
            # kein Fehler. Der Link im Text bleibt, nur die Kante entsteht nicht.
            _log.info(
                "confluence.titelsuche_erfolglos",
                source=self.config.name,
                space=space,
                error=f"{type(exc).__name__}: {exc}",
            )

        self._titel_cache[schluessel] = gefunden
        return gefunden


# ---------------------------------------------------------------------------
# Lesehilfen für die Antwortstruktur
# ---------------------------------------------------------------------------


def _storage(seite: dict[str, Any]) -> str | None:
    """Der Rohinhalt einer Seite im Storage-Format."""
    wert = seite.get("body", {}).get("storage", {}).get("value")
    return None if wert is None else str(wert)


def _space_key(seite: dict[str, Any]) -> str | None:
    """Der Space-Schlüssel einer Seite."""
    schluessel = seite.get("space", {}).get("key")
    return None if schluessel is None else str(schluessel)


def _labels(seite: dict[str, Any]) -> list[str]:
    """Die Label-Namen einer Seite, aus beiden vorkommenden Antwortformen.

    Confluence Data Center liefert ``metadata.labels.results``; die nachgebildete und die ältere
    Form führen ``metadata.labels`` direkt als Liste. Welche kommt, hängt an der Version und am
    ``expand`` — und ein Adapter, der nur eine kennt, verliert im anderen Fall alle Tags, ohne
    dass irgendetwas fehlschlüge.
    """
    roh = seite.get("metadata", {}).get("labels", [])
    eintraege = roh.get("results", []) if isinstance(roh, dict) else roh
    return [
        str(label["name"])
        for label in eintraege
        if isinstance(label, dict) and label.get("name") is not None
    ]


def _gemeldete_verweise(seite: dict[str, Any]) -> list[str]:
    """Verweise, die die Antwort ausdrücklich als Liste mitbringt.

    Die echte API tut das nicht — der Mock-Server tut es (§9.2), und ein Gateway kann es tun.
    Beides zu berücksichtigen kostet nichts und hält den Mock beim vollen Codepfad.
    """
    return [str(ziel) for ziel in seite.get("links", {}).get("internal", [])]


def _ausgeschlossen(seite: dict[str, Any], ausgeschlossene_labels: Any) -> bool:
    """Ob eine Seite wegen eines Labels übergangen wird (§8.4: ``selection.exclude_labels``)."""
    verboten = {str(label) for label in ausgeschlossene_labels or ()}
    return bool(verboten & set(_labels(seite)))


def _cql_escape(wert: str) -> str:
    """Entschärft Anführungszeichen in einem CQL-Literal.

    Ein Seitentitel darf ein Anführungszeichen enthalten. Ungeschützt eingesetzt bräche es die
    Abfrage auf — im besten Fall mit einem Fehler, im schlechteren mit einem anderen Ergebnis.
    """
    return wert.replace("\\", "\\\\").replace('"', '\\"')
