"""Adapter für Jira (§8, §9.1).

Derselbe Aufbau wie beim Confluence-Adapter, und das ist die eigentliche Aussage: Zwei Quellen
mit unterschiedlicher Paginierung (``start``/``limit`` gegen ``startAt``/``maxResults``),
unterschiedlicher Verschachtelung und unterschiedlichem Referenzbegriff enden beim selben DTO.
Der Kern sieht zwischen beiden keinen Unterschied.

Die externe ID ist der Vorgangsschlüssel (``TEAM-42``) und nicht die numerische ID. §22.3
verlangt Stabilität über Läufe hinweg, und der Schlüssel ist das, was Menschen in Texten
verlinken — womit er auch der Wert ist, auf den eine Referenz zeigt.

**Die API-Version gehört in die Konfiguration.** Jira Cloud spricht ``/rest/api/3``, Jira Data
Center ``/rest/api/2``; die Antworten unterscheiden sich unter anderem darin, ob ``description``
Wiki-Markup oder ein ADF-Dokument ist. Als Literal im Code wäre der Adapter an eine der beiden
Welten gebunden, und die Wahl fiele beim Programmieren statt beim Anbinden.

**Strukturierte Beziehungen sind Tatsachen, keine Vermutungen.** Was in ``fields.issuelinks`` oder
``fields.subtasks`` steht, hat ein Mensch im Quellsystem gesetzt. Solche Kanten entstehen deshalb
direkt beim Sync mit ihrer richtigen Art — und nicht später aus einer Modellklassifikation, die
dasselbe noch einmal erraten müsste (Leitprinzip 6, §7.7).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.domain.references import SourceReference
from wissensgraph.infrastructure.adapters.base import HttpSourceAdapter
from wissensgraph.infrastructure.adapters.confluence_links import link_from_href
from wissensgraph.infrastructure.adapters.jira_markdown import JiraLinks, wiki_to_markdown
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    SourceDocument,
    SourceError,
    SourceObjectNotFound,
)

_log = get_logger(__name__)

#: Der Agile-Endpunkt liegt außerhalb der versionierten REST-API und ist deshalb kein
#: Bestandteil des konfigurierbaren Präfixes.
PATH_BOARDS = "/rest/agile/1.0/board"

#: Pfade relativ zum konfigurierten Präfix.
PATH_SEARCH = "search"
PATH_DELETED = "deleted"
PATH_ISSUE = "issue"
PATH_REMOTE_LINKS = "remotelink"

#: Vorgabepräfix. ``2`` und nicht ``3``: Data Center ist die Welt, in der dieser Adapter
#: produktiv läuft, und dort gibt es die Version 3 nicht.
DEFAULT_API_PREFIX = "/rest/api/2"

#: Schlüssel der Auswahl in ``sources.yaml`` (§8.4: ``selection.jql_filter``).
SELECTION_JQL = "jql_filter"
SELECTION_FIELDS = "fields"
SELECTION_REMOTE_LINKS = "remote_links"
SELECTION_REMOTE_PREFIX = "remote_link_prefix"


@dataclass(frozen=True)
class Verknuepfungsart:
    """Wie eine Jira-Verknüpfung zu einer Kante wird."""

    kind: str
    """Die Kantenart aus ``edge_kinds`` (§7.7)."""

    seite: str
    """Von welcher Seite aus die Kante geschrieben wird: ``inward``, ``outward`` oder ``beide``.

    Eine Kante entsteht immer *von* dem Vorgang aus, der gerade synchronisiert wird — etwas
    anderes lässt der Kantenabgleich nicht zu, und das aus gutem Grund: Er ersetzt genau die
    Kanten eines Ausgangsknotens (§10.4). Für eine gerichtete Beziehung heißt das, dass nur eine
    der beiden Seiten sie aufschreiben darf. "A blockiert B" wird deshalb von **B** notiert, als
    "B hängt von A ab" — dieselbe Tatsache, von der Seite aus gesehen, die sie schreiben kann.
    """


#: Die Zuordnung von Jira-Verknüpfungstypen auf Kantenarten. Der Schlüssel ist der Typname in
#: Kleinbuchstaben, wie ihn ``fields.issuelinks[].type.name`` führt.
LINK_TYPES: dict[str, Verknuepfungsart] = {
    "blocks": Verknuepfungsart(defaults.EDGE_KIND_DEPENDS_ON, "inward"),
    "relates": Verknuepfungsart(defaults.EDGE_KIND_RELATED, "beide"),
    "duplicate": Verknuepfungsart(defaults.EDGE_KIND_RELATED, "beide"),
    "cloners": Verknuepfungsart(defaults.EDGE_KIND_RELATED, "beide"),
}

#: Kantenart für einen Verknüpfungstyp, den die Tabelle nicht kennt. ``related`` und nicht
#: "übergehen": Dass zwei Vorgänge verknüpft sind, ist auch dann eine Tatsache, wenn wir die
#: genaue Art nicht kennen — und ``related`` behauptet nicht mehr als das.
LINK_TYPE_FALLBACK = Verknuepfungsart(defaults.EDGE_KIND_RELATED, "beide")


class JiraAdapter(HttpSourceAdapter):
    """Liest Vorgänge aus Jira."""

    name = defaults.ADAPTER_JIRA
    default_api_prefix = DEFAULT_API_PREFIX
    capabilities = AdapterCapabilities(
        incremental=True, deletions=True, single_fetch=True, references=True
    )

    def health_path(self) -> str:
        """Die Board-Liste — der billigste Aufruf, der Erreichbarkeit und Auth zugleich prüft."""
        return PATH_BOARDS

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle ausgewählten Vorgänge, seitenweise geholt (§8.2 Regel 1)."""
        since = self.cursor_since(cursor)
        return self._durchreichen(self._vorgaenge(since), since)

    def _vorgaenge(self, since: datetime | None) -> Iterator[SourceDocument]:
        """Blättert durch die Vorgangssuche."""
        limit = self.config.connection.page_size
        auswahl = self.config.selection
        start = 0
        while True:
            parameter: dict[str, Any] = {"startAt": start, "maxResults": limit}
            if auswahl.get(SELECTION_JQL):
                parameter["jql"] = auswahl[SELECTION_JQL]
            if auswahl.get(SELECTION_FIELDS):
                # Eine ausdrückliche Feldliste statt '*all': Sie verkleinert die Antwort und
                # damit alles, was danach über sie läuft — bis hin zu den Token des Embeddings.
                parameter["fields"] = ",".join(str(f) for f in auswahl[SELECTION_FIELDS])
            if since is not None:
                parameter["since"] = since.isoformat()

            antwort = self.get(self.api_path(PATH_SEARCH), parameter)
            treffer = list(antwort.get("issues", []))
            if not treffer:
                return
            for vorgang in treffer:
                yield self._als_dokument(vorgang)
            start += len(treffer)
            if start >= int(antwort.get("total", start)):
                return

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die Schlüssel der gelöschten Vorgänge (``capabilities.deletions``)."""
        antwort = self.get(self.api_path(PATH_DELETED))
        for key in antwort.get("keys", []):
            yield str(key)

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Einen einzelnen Vorgang (``capabilities.single_fetch``)."""
        try:
            vorgang = self.get(self.api_path(PATH_ISSUE, external_id))
        except SourceObjectNotFound:
            return None
        return self._als_dokument(vorgang)

    # -- Übersetzung ---------------------------------------------------------------

    def _als_dokument(self, vorgang: dict[str, Any]) -> SourceDocument:
        """Übersetzt einen Jira-Vorgang in das quellneutrale DTO (§8.2)."""
        schluessel = str(vorgang["key"])
        felder = vorgang.get("fields", {})
        links = self._links()
        inhalt = wiki_to_markdown(_beschreibung(felder), links)

        verweise: list[SourceReference] = [
            *inhalt.references,
            *self._beziehungen(felder, links),
            *self._entfernte_verweise(schluessel),
            *(SourceReference(target=str(ziel)) for ziel in vorgang.get("references", [])),
        ]

        werte: dict[str, Any] = {
            "external_id": schluessel,
            "title": felder.get("summary"),
            "description": None,
            "body": inhalt.markdown or None,
            "resource": links.issue_url(schluessel),
            "tags": [str(label) for label in felder.get("labels", [])],
            "updated_at": felder.get("updated"),
            "references": verweise,
            "extra": {
                "status": felder.get("status", {}).get("name"),
                "issue_type": felder.get("issuetype", {}).get("name"),
                "priority": felder.get("priority", {}).get("name"),
            },
        }
        werte.update(self.mapping.apply(vorgang))
        return SourceDocument.model_validate(werte)

    def _links(self) -> JiraLinks:
        """Die Linkauflösung dieser Quellinstanz."""
        return JiraLinks(
            id_prefix=self.config.id_prefix,
            web_base_url=self.config.connection.web_url,
        )

    # -- Strukturierte Beziehungen -------------------------------------------------

    def _beziehungen(self, felder: dict[str, Any], links: JiraLinks) -> list[SourceReference]:
        """Übersetzt Unteraufgaben, Elternvorgang und Verknüpfungen in typisierte Verweise."""
        verweise: list[SourceReference] = []

        # Unteraufgaben: Der Vorgang ist der Behälter, die Unteraufgabe der Inhalt. Genau diese
        # Richtung meint ``member`` (§12.2 zählt Verweise "auf ein Cluster von z" über sie).
        for unteraufgabe in felder.get("subtasks") or ():
            if schluessel := _schluessel(unteraufgabe):
                verweise.append(
                    SourceReference(
                        target=links.concept_id(schluessel), kind=defaults.EDGE_KIND_MEMBER
                    )
                )

        # Der Elternvorgang wäre die Gegenrichtung derselben Beziehung — und die kann dieser
        # Vorgang nicht schreiben, weil eine Kante immer bei ihm beginnt. Ein ``member`` mit
        # vertauschten Enden wäre keine Notlösung, sondern eine falsche Aussage: Die Abfragen
        # in der Katalogschicht lesen ``from_id`` als den Behälter, das Kind erschiene als
        # Cluster seines eigenen Epics. Deshalb ``related`` — die Verbindung bleibt erhalten,
        # ohne eine Enthaltensein-Behauptung aufzustellen.
        if eltern := _schluessel(felder.get("parent")):
            verweise.append(
                SourceReference(target=links.concept_id(eltern), kind=defaults.EDGE_KIND_RELATED)
            )

        for verknuepfung in felder.get("issuelinks") or ():
            if verweis := _verknuepfung(verknuepfung, links):
                verweise.append(verweis)

        return verweise

    def _entfernte_verweise(self, schluessel: str) -> list[SourceReference]:
        """Die Remote-Links eines Vorgangs — Verweise auf Confluence-Seiten (§8.5).

        Sie kosten **eine zusätzliche Anfrage je Vorgang** und sind deshalb abschaltbar; bei
        zehntausend Vorgängen ist das der Unterschied zwischen Minuten und Stunden. Standardmäßig
        aus: Was Geld und Zeit kostet, soll eine Entscheidung sein und keine Voreinstellung
        (§8.4).
        """
        auswahl = self.config.selection
        if not auswahl.get(SELECTION_REMOTE_LINKS):
            return []
        praefix = str(auswahl.get(SELECTION_REMOTE_PREFIX) or defaults.ADAPTER_CONFLUENCE)

        try:
            antwort = self.get(self.api_path(PATH_ISSUE, schluessel, PATH_REMOTE_LINKS))
        except SourceError as exc:
            # Wie bei der Titelsuche in Confluence: Ein fehlender Remote-Link kostet eine Kante,
            # kein Dokument. Der Lauf geht weiter (§8.5, §21.3).
            _log.info(
                "jira.remotelinks_erfolglos",
                source=self.config.name,
                issue=schluessel,
                error=f"{type(exc).__name__}: {exc}",
            )
            return []

        verweise: list[SourceReference] = []
        for eintrag in antwort if isinstance(antwort, list) else antwort.get("values", []):
            seite = _confluence_seite(eintrag)
            if seite is not None:
                verweise.append(SourceReference(target=f"{praefix}:{seite}"))
        return verweise


# ---------------------------------------------------------------------------
# Lesehilfen für die Antwortstruktur
# ---------------------------------------------------------------------------


def _beschreibung(felder: dict[str, Any]) -> str | None:
    """Der Beschreibungstext, sofern er einer ist.

    Jira Cloud liefert unter ``description`` ein ADF-Dokument als verschachteltes Mapping. Dieser
    Adapter ist für Data Center gebaut, wo dort Wiki-Markup steht; ein Mapping wird deshalb
    übergangen statt halb ausgewertet. Ein halb ausgewerteter Text wäre schlimmer als keiner —
    er sähe vollständig aus.
    """
    wert = felder.get("description")
    return wert if isinstance(wert, str) else None


def _schluessel(vorgang: Any) -> str | None:
    """Der Vorgangsschlüssel aus einem eingebetteten Vorgangsverweis."""
    if not isinstance(vorgang, dict):
        return None
    schluessel = vorgang.get("key")
    return str(schluessel) if schluessel else None


def _verknuepfung(verknuepfung: Any, links: JiraLinks) -> SourceReference | None:
    """Übersetzt einen Eintrag aus ``fields.issuelinks`` in einen typisierten Verweis."""
    if not isinstance(verknuepfung, dict):
        return None
    typ = str(verknuepfung.get("type", {}).get("name", "")).strip().lower()
    art = LINK_TYPES.get(typ, LINK_TYPE_FALLBACK)

    nach_innen = _schluessel(verknuepfung.get("inwardIssue"))
    nach_aussen = _schluessel(verknuepfung.get("outwardIssue"))

    if art.seite == "inward":
        ziel = nach_innen
    elif art.seite == "outward":
        ziel = nach_aussen
    else:
        ziel = nach_innen or nach_aussen

    if ziel is None:
        return None
    return SourceReference(target=links.concept_id(ziel), kind=art.kind)


def _confluence_seite(eintrag: Any) -> str | None:
    """Die Seiten-ID hinter einem Remote-Link, falls er auf eine Confluence-Seite zeigt.

    Ein Remote-Link kann auf alles zeigen — ein Ticket in einem anderen System, eine Datei, eine
    Webseite. Nur der Fall mit einer erkennbaren Confluence-Seiten-ID ergibt eine Kante; alles
    andere bleibt außen vor, weil eine Kante auf ein Konzept, das es nie geben wird, eine
    Behauptung wäre (§8.5).
    """
    if not isinstance(eintrag, dict):
        return None
    objekt = eintrag.get("object", {})
    if not isinstance(objekt, dict):
        return None

    # Der zuverlässigste Weg ist die globale ID: Confluence trägt dort 'appId=…&pageId=123' ein.
    aus_global = eintrag.get("globalId")
    if isinstance(aus_global, str) and "pageid=" in aus_global.lower():
        for teil in aus_global.replace("&", "%").split("%"):
            schluessel, _, wert = teil.partition("=")
            if schluessel.strip().lower() == "pageid" and wert.strip().isdigit():
                return wert.strip()

    adresse = objekt.get("url")
    if isinstance(adresse, str) and adresse:
        return link_from_href(adresse).page_id
    return None
