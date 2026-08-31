"""Die sieben Werkzeuge aus §18.1 — als Aufrufe derselben Dienste wie CLI und API.

Drei Dinge machen diesen Layer aus, und alle drei stehen in §18:

**Die Beschreibungen schreiben die Reihenfolge fest.** §18.2 nennt das "die einzige wirksame
Stelle, an der sich das Verhalten des Agenten steuern lässt", und das ist keine Übertreibung: Ein
Agent liest keine Architekturdokumente, er liest Werkzeugbeschreibungen. ``graph_search`` trägt
deshalb ausdrücklich den Hinweis, dass es der *Fallback* ist — nicht als Höflichkeit, sondern
damit eine Sitzung nicht mit einer Suche beginnt, wo eine Übersicht genügt hätte.

**Schreiben geht nur nach ``personal``.** §17.4: "Ein Mensch darf die geteilte Struktur ordnen;
ein Agent nicht." Durchgesetzt wird das nicht hier, sondern in der Datenbank — der Server hält
auf ``shared`` eine nur lesende Rolle (§18.3). Was hier steht, ist die verständliche Meldung
davor; die Absicherung liegt eine Ebene tiefer und hält auch dann, wenn dieser Code sie vergisst.

**Jede Antwort ist gedeckelt.** Ein Werkzeug, das einen Fließtext von 200 kB zurückgibt, macht
das Kontextfenster des Agenten zunichte, ohne dass jemand es bemerkt. Gekürzt wird deshalb
sichtbar, mit ``truncated: true``.

Der Zuschnitt ist Absicht: Dieses Modul kennt **kein** MCP-SDK. Es beschreibt Werkzeuge als
Daten — Name, Beschreibung, Schema, Aufruf —, und :mod:`wissensgraph.mcp.server` bindet sie an
den Transport. So sind die Werkzeuge ohne Server prüfbar, und ein Wechsel der SDK-Version ist
eine Änderung an einer Datei.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.ports.models import ModelError
from wissensgraph.services.catalog import CatalogService
from wissensgraph.services.clustering import ClusterService
from wissensgraph.services.curation import CurationError, CurationService, NotFoundError
from wissensgraph.services.graph import GraphService, UnknownStartError
from wissensgraph.services.serialization import kante_dict, konzept_dict

#: Schlüssel, unter dem eine gekürzte Antwort das kenntlich macht (§18.3).
TRUNCATED_KEY = "truncated"

#: Felder, die eine Kürzung nie anfasst. Es sind die, mit denen ein Agent weiterarbeitet: Ohne
#: ``id`` gibt es kein ``graph_traverse`` und kein zweites ``concept_get``, ohne ``store`` weiß er
#: nicht, wo er nachfragen muss. Sie sind kurz — sie einzusparen bringt fast nichts und kostet
#: alles.
KENNFELDER: frozenset[str] = frozenset({"id", "store", "scope", "type"})


class ToolError(RuntimeError):
    """Ein Werkzeugaufruf ist nicht zulässig oder findet sein Ziel nicht.

    Eine eigene Klasse, damit der Server sie von einem Programmfehler unterscheiden kann: Das
    eine ist eine Auskunft an den Agenten, das andere gehört ins Log.
    """


@dataclass(frozen=True)
class ToolSpec:
    """Ein Werkzeug als Daten: Name, Beschreibung, Eingabeschema, Aufruf (§18.1)."""

    name: str
    description: str
    input_schema: dict[str, Any]
    call: Callable[[Mapping[str, Any]], dict[str, Any]]


def _schema(properties: dict[str, Any], *, required: Sequence[str] = ()) -> dict[str, Any]:
    """Ein JSON-Schema-Objekt für die Eingabe eines Werkzeugs."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}


class Toolbox:
    """Die sieben Werkzeuge, gebunden an eine Sitzung (§18.1, §18.3)."""

    def __init__(
        self,
        settings: Settings,
        *,
        graph: GraphService,
        catalog: CatalogService,
        curation: CurationService,
        clusters: ClusterService,
        session: str = defaults.MCP_DEFAULT_SESSION,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration; liefert Scopes, Grenzen und den Antwortdeckel.
            graph: Der Graphdienst — Traversierung und Suche (§12).
            catalog: Der Lesepfad (§16.2).
            curation: Der Schreibpfad. Er landet über die Arbeitseinheit auf der nur lesenden
                Verbindung, sobald der Store ``shared`` ist (§18.3).
            clusters: Der Clustering-Dienst für ``cluster_project`` (§18.1).
            session: Die Sitzungskennung. Sie steht als ``agent:<session>`` in jedem
                Journaleintrag (§18.3) — ohne sie ließe sich später nicht sagen, welcher Agent
                eine Notiz angelegt hat.
        """
        self._settings = settings
        self._graph = graph
        self._catalog = catalog
        self._curation = curation
        self._clusters = clusters
        self._session = session

    @property
    def actor(self) -> str:
        """Der Akteur dieser Sitzung im Änderungsjournal (§7.4, §18.3)."""
        return f"{defaults.MCP_ACTOR_PREFIX}{self._session}"

    def _persoenliche_typen(self) -> tuple[str, ...]:
        """Die Konzepttypen, die im persönlichen Store zulässig sind (§7.2)."""
        return tuple(
            eintrag.name
            for eintrag in self._settings.concept_types
            if defaults.STORE_PERSONAL in eintrag.stores
            # 'Cluster' steht in der Taxonomie auch für ``personal``, aber ein Cluster entsteht
            # aus dem Clustering-Lauf und nicht von Hand. Böte man ihn dem Agenten an, legte er
            # eine Themengruppe an, die keine Mitglieder hat und die der nächste Lauf nicht
            # kennt — ``cluster_project`` ist der Weg dorthin, nicht ``concept_upsert``.
            and eintrag.name != defaults.CONCEPT_TYPE_CLUSTER
        )

    def _typ_feld(self) -> dict[str, Any]:
        """Das ``type``-Feld von ``concept_upsert`` — mit den Typen dieser Installation.

        Die Aufzählung kommt aus der Taxonomie und steht nicht als Liste im Code. Das ist der
        Punkt: §7.2 sagt, ein Typ gehört in ``config/wissensgraph.yaml``, und die Prüfung dort ist
        exakt — Groß- und Kleinschreibung eingeschlossen. Ein Agent, der die erlaubten Werte nicht
        kennt, rät ``note`` und bekommt "Unbekannter Typ 'note'" zurück; er hat keine Möglichkeit,
        auf ``Note`` zu kommen, ohne es zu probieren. Steht die Aufzählung im Schema, entfällt das
        Raten — und wer die Taxonomie erweitert, muss diese Datei nicht anfassen.
        """
        typen = self._persoenliche_typen()
        return {
            **_STR,
            "enum": list(typen),
            "description": (
                "Der Konzepttyp aus der Taxonomie dieser Installation. Genau so geschrieben: "
                f"{', '.join(typen)}."
            ),
        }

    # -- Die Werkzeuge -----------------------------------------------------------

    def specs(self) -> tuple[ToolSpec, ...]:
        """Alle Werkzeuge in der Reihenfolge, in der §18.1 sie aufführt.

        Die Reihenfolge ist nicht gleichgültig: Ein Agent, der eine Werkzeugliste bekommt, liest
        sie von oben. ``graph_overview`` steht deshalb zuerst und ``graph_search`` an dritter
        Stelle — genau die Reihenfolge, die §18.2 als bevorzugt festschreibt.
        """
        return (
            ToolSpec(
                name="graph_overview",
                description=(
                    "Günstiger Einstieg: die Themengruppen (Cluster) des Wissensgraphen mit "
                    "Titel, Beschreibung und Mitgliederzahl. **Dies ist der erste Aufruf einer "
                    "Sitzung.** Er kostet wenig und beantwortet die Frage, worum es in diesem "
                    "Graphen überhaupt geht — benutze ihn, bevor du suchst."
                ),
                input_schema=_schema(
                    {
                        "scope": {**_STR, "description": "Nur Cluster dieses Scopes."},
                        "store": {**_STR, "description": "'shared' (Vorgabe) oder 'personal'."},
                        "limit": {
                            **_INT,
                            "minimum": 1,
                            "description": (
                                f"Höchstzahl Themengruppen, Vorgabe {defaults.MCP_OVERVIEW_LIMIT}."
                            ),
                        },
                        "cursor": {
                            **_STR,
                            "description": (
                                "Zum Weiterblättern: der 'next_cursor' aus der vorigen Antwort."
                            ),
                        },
                    }
                ),
                call=self.graph_overview,
            ),
            ToolSpec(
                name="graph_traverse",
                description=(
                    "Bewege dich vom einem Konzept aus über echte Kanten weiter. Nach dem ersten "
                    "Anker ist dies der Aufruf, den du am häufigsten brauchst. "
                    "'member' führt abwärts in ein Cluster hinein, 'related' und die semantischen "
                    "Kantenarten führen seitwärts zu verwandten Themen. Kanten über die "
                    "Store-Grenze werden mitverfolgt."
                ),
                input_schema=_schema(
                    {
                        "concept_id": {**_STR, "description": "Ausgangspunkt."},
                        "hops": {**_INT, "minimum": 1, "description": "Tiefe, Vorgabe 1."},
                        "kinds": {
                            "type": "array",
                            "items": _STR,
                            "description": "Nur diesen Kantenarten folgen.",
                        },
                        "store": _STR,
                    },
                    required=["concept_id"],
                ),
                call=self.graph_traverse,
            ),
            ToolSpec(
                name="graph_search",
                description=(
                    "**Fallback.** Benutze dieses Werkzeug nur, wenn weder eine bestehende "
                    "Verbindung noch 'graph_overview' einen Startpunkt liefert. Es sucht "
                    "zweistufig: erst über Themengruppen, dann über einzelne Dokumente. Das "
                    "Ergebnis nennt im Feld 'mode', wie gesucht wurde."
                ),
                input_schema=_schema(
                    {
                        "query": _STR,
                        "scope": _STR,
                        "granularity": {
                            **_STR,
                            "enum": ["cluster", "document", "auto"],
                            "description": "Vorgabe 'auto': erst Cluster, dann Dokumente.",
                        },
                        "store": _STR,
                    },
                    required=["query"],
                ),
                call=self.graph_search,
            ),
            ToolSpec(
                name="concept_get",
                description=(
                    "Der vollständige Inhalt eines Konzepts einschließlich Fließtext, mit seinen "
                    "Kanten und seiner Herkunft. Lange Texte werden gekürzt; das Ergebnis sagt "
                    "es dann über 'truncated'."
                ),
                input_schema=_schema({"concept_id": _STR, "store": _STR}, required=["concept_id"]),
                call=self.concept_get,
            ),
            ToolSpec(
                name="concept_upsert",
                description=(
                    "Legt eine Notiz oder ein Projekt an — **ausschließlich im persönlichen "
                    "Store**. Der geteilte Store bekommt seine Inhalte aus den Quellsystemen und "
                    "ist für dich nicht beschreibbar. Ohne 'concept_id' entsteht ein neues "
                    "Konzept, mit ihr wird ein bestehendes fortgeschrieben."
                ),
                input_schema=_schema(
                    {
                        "type": self._typ_feld(),
                        "title": _STR,
                        "description": _STR,
                        "body": _STR,
                        "tags": {"type": "array", "items": _STR},
                        "scope": _STR,
                        "concept_id": {
                            **_STR,
                            "description": "Zum Fortschreiben eines bestehenden Konzepts.",
                        },
                    },
                    required=["type", "title"],
                ),
                call=self.concept_upsert,
            ),
            ToolSpec(
                name="link_add",
                description=(
                    "Verknüpft ein persönliches Konzept mit einem anderen — bevorzugt mit einer "
                    "Themengruppe im geteilten Store. Die Kante geht immer von deiner Notiz aus; "
                    "der geteilte Store weiß nichts von ihr."
                ),
                input_schema=_schema(
                    {
                        "from_id": _STR,
                        "to_id": _STR,
                        "to_store": _STR,
                        "kind": {**_STR, "description": "Vorgabe 'references'."},
                    },
                    required=["from_id", "to_id"],
                ),
                call=self.link_add,
            ),
            ToolSpec(
                name="cluster_project",
                description=(
                    "Bildet die Themengruppen im persönlichen Store neu — nützlich, nachdem du "
                    "mehrere Notizen zu einem Projekt angelegt hast. Verändert nichts im "
                    "geteilten Store."
                ),
                input_schema=_schema({"project_id": _STR}, required=["project_id"]),
                call=self.cluster_project,
            ),
        )

    # -- Umsetzungen --------------------------------------------------------------

    def graph_overview(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Die Cluster eines Stores (§18.1)."""
        store = self._store(args.get("store"))
        zusammenfassungen, weiter = self._catalog.clusters(
            store=store,
            scope=args.get("scope"),
            limit=int(args.get("limit", defaults.MCP_OVERVIEW_LIMIT)),
            cursor=args.get("cursor"),
        )
        naechster = (
            "Wähle ein Cluster und rufe graph_traverse damit auf. Suche nur, wenn hier "
            "nichts passt."
        )
        if weiter:
            # Ohne diesen Satz sähe eine abgeschnittene Übersicht aus wie eine vollständige, und
            # der Agent hielte für den ganzen Graphen, was nur sein Anfang ist.
            naechster = (
                "Dies ist nicht die vollständige Liste. Rufe graph_overview erneut mit "
                f"cursor: '{weiter}' auf, um weiterzublättern — oder schränke mit 'scope' ein. "
                + naechster
            )
        antwort: dict[str, Any] = {
            "store": store,
            "clusters": [
                {
                    "id": eintrag.concept.id,
                    "title": eintrag.concept.title,
                    "description": eintrag.concept.description,
                    "member_count": eintrag.member_count,
                }
                for eintrag in zusammenfassungen
            ],
            "next_step": naechster,
        }
        if weiter:
            antwort["next_cursor"] = weiter
        return self._deckeln(antwort)

    def graph_traverse(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Ein oder mehrere Hops von einem Konzept aus (§18.1)."""
        store = self._store(args.get("store"))
        try:
            ergebnis = self._graph.traverse(
                [str(args["concept_id"])],
                store=store,
                hops=int(args.get("hops", 1)),
                kinds=args.get("kinds"),
            )
        except UnknownStartError as exc:
            raise ToolError(str(exc)) from exc
        return self._deckeln(
            {
                "store": store,
                "hops": ergebnis.hops,
                "nodes": [knoten.as_dict() for knoten in ergebnis.nodes],
                "edges": [
                    {
                        "from_id": kante.from_id,
                        "to_id": kante.to_id,
                        "to_store": kante.to_store,
                        "kind": kante.kind,
                        "confidence": kante.confidence,
                        "verified": kante.verified_at is not None,
                    }
                    for kante in ergebnis.edges
                ],
                # Der 'score' der Knoten ist hier die Formel aus §12.3 und in einer Suche eine
                # Ähnlichkeit oder ein RRF-Wert. Der Zahl sieht man das nicht an, und ein Agent,
                # der die Werte zweier Antworten gegeneinander hält, irrt sich.
                "score_kind": defaults.SCORE_KIND_RANKING,
                "score_hint": defaults.SCORE_KIND_HINTS[defaults.SCORE_KIND_RANKING],
            }
        )

    def graph_search(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Die zweistufige Suche — der Fallback (§12.4, §18.2)."""
        scope = args.get("scope")
        store = (
            self._settings.store_of_scope(str(scope))
            if scope and str(scope) in {item.name for item in self._settings.scopes}
            else self._store(args.get("store"))
        )
        try:
            ergebnis = self._graph.search(
                str(args["query"]),
                store=store,
                granularity=str(args.get("granularity", defaults.SEARCH_GRANULARITY_AUTO)),
            )
        except (ProviderNotAllowedError, ModelError) as exc:  # pragma: no cover — defensiv
            raise ToolError(f"Die Suche ist nicht möglich: {exc}") from exc
        antwort: dict[str, Any] = {"store": store, **ergebnis.as_dict()}
        antwort["score_hint"] = defaults.SCORE_KIND_HINTS[ergebnis.score_kind]
        if not antwort.get("hits") and antwort.get("mode") == defaults.SEARCH_MODE_CLUSTER:
            # Eine leere Liste ohne Erklärung ist die schlechteste Antwort: Sie sieht aus wie
            # "dazu gibt es nichts", heißt aber nur "kein Cluster liegt nah genug". Auf der
            # Dokumentebene steht dieselbe Frage womöglich beantwortet da (§12.4).
            antwort["next_step"] = (
                "Kein Cluster liegt nah genug an dieser Anfrage — das heißt nicht, dass es dazu "
                "nichts gibt. Rufe dasselbe noch einmal mit granularity: 'document' auf."
            )
        return self._deckeln(antwort)

    def concept_get(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Der Volltext eines Konzepts (§18.1)."""
        store = self._store(args.get("store"))
        detail = self._catalog.concept(str(args["concept_id"]), store=store)
        if detail is None:
            raise ToolError(f"Konzept '{args['concept_id']}' gibt es im Store '{store}' nicht.")
        return self._deckeln(detail.as_dict())

    def concept_upsert(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Legt ein Konzept im persönlichen Store an oder schreibt es fort (§18.1).

        Der Store steht nicht in den Argumenten, und das ist die Aussage: Ein Agent kann ihn
        nicht wählen (§17.4). Der Scope wird gegen die Konfiguration geprüft und muss im
        persönlichen Store liegen.
        """
        scope = str(args.get("scope") or self._persoenlicher_scope())
        vorhanden = args.get("concept_id")
        try:
            if vorhanden:
                aenderungen = {
                    name: args[name]
                    for name in ("title", "description", "body", "tags")
                    if name in args
                }
                ergebnis = self._curation.patch_concept(
                    str(vorhanden),
                    store=defaults.STORE_PERSONAL,
                    changes=aenderungen,
                    actor=self.actor,
                )
            else:
                ergebnis = self._curation.create_concept(
                    scope=scope,
                    concept_type=str(args["type"]),
                    title=str(args["title"]),
                    description=args.get("description"),
                    body=args.get("body"),
                    tags=tuple(args.get("tags", ())),
                    actor=self.actor,
                )
        except NotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except CurationError as exc:
            raise ToolError(str(exc)) from exc
        assert ergebnis.concept is not None
        return self._deckeln({"concept": konzept_dict(ergebnis.concept), "actor": self.actor})

    def link_add(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Legt eine Kante vom persönlichen Store aus an (§18.1).

        ``from_store`` gibt es als Argument nicht: Eine Kante des Agenten beginnt immer im
        persönlichen Store. Die Gegenrichtung existiert nicht und soll es nicht (§12.1).
        """
        try:
            ergebnis = self._curation.add_edge(
                store=defaults.STORE_PERSONAL,
                from_id=str(args["from_id"]),
                to_id=str(args["to_id"]),
                to_store=str(args.get("to_store", defaults.STORE_SHARED)),
                kind=str(args.get("kind", defaults.EDGE_KIND_REFERENCES)),
                actor=self.actor,
            )
        except NotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except CurationError as exc:
            raise ToolError(str(exc)) from exc
        assert ergebnis.edge is not None
        return self._deckeln({"edge": kante_dict(ergebnis.edge), "actor": self.actor})

    def cluster_project(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Lokales Neu-Clustern um ein Brücken-Konzept (§18.1).

        Es läuft über den Scope des Projekts und nicht über eine eigene Teilmenge: §13.2 bildet
        Cluster "je Konzept innerhalb eines Scopes", und ein zweites Verfahren daneben wäre eine
        zweite Wahrheit darüber, was zusammengehört.
        """
        detail = self._catalog.concept(str(args["project_id"]), store=defaults.STORE_PERSONAL)
        if detail is None:
            raise ToolError(
                f"Projekt '{args['project_id']}' gibt es im Store "
                f"'{defaults.STORE_PERSONAL}' nicht."
            )
        bericht = self._clusters.run(scope=detail.concept.scope, actor=self.actor)
        return self._deckeln({"scope": detail.concept.scope, "report": bericht.as_dict()})

    # -- Hilfen -------------------------------------------------------------------

    def _store(self, angabe: Any) -> str:
        """Löst einen Store-Namen auf; ohne Angabe der geteilte.

        Raises:
            ToolError: Wenn der Store nicht konfiguriert ist.
        """
        if angabe is None:
            return defaults.STORE_SHARED
        name = str(angabe)
        if name not in self._settings.stores:
            raise ToolError(
                f"Unbekannter Store '{name}'. Es gibt: {', '.join(self._settings.stores)}."
            )
        return name

    def _persoenlicher_scope(self) -> str:
        """Der erste Scope im persönlichen Store — die Vorgabe für eine neue Notiz.

        Raises:
            ToolError: Wenn es keinen gibt. Dann ist der Agent in dieser Installation nicht
                vorgesehen, und das ist eine Auskunft und kein Absturz.
        """
        for scope in self._settings.scopes:
            if scope.store == defaults.STORE_PERSONAL:
                return scope.name
        raise ToolError(
            f"Es ist kein Scope im Store '{defaults.STORE_PERSONAL}' konfiguriert; ein Agent "
            f"kann hier nichts anlegen (§17.4)."
        )

    def _deckeln(self, antwort: dict[str, Any]) -> dict[str, Any]:
        """Kürzt eine Antwort auf ``mcp.max_response_tokens`` und markiert das (§18.3).

        Gekürzt werden Listen von hinten und Texte am Ende. Die Reihenfolge ist wichtig: Die
        vorderen Einträge einer Trefferliste sind die besseren (§12.3), und ein Fließtext ist von
        vorn nach hinten verständlich. Von vorn zu kürzen hieße, das Nützlichste wegzuwerfen.

        Unter den Texten wird immer der **längste** angefasst, und zwar einer nach dem anderen.
        Der Grund ist ein Fehler, den diese Fassung behebt: Vorher lief eine Schleife über alle
        Zeichenketten und zog von jeder den *gesamten* Überschuss ab. Bei einem Konzept mit langem
        ``body`` traf das zuerst ``id``, ``store``, ``scope``, ``type``, ``title`` und
        ``description`` — alle kurz, alle sofort auf ``""`` — während der lange Text übrig blieb.
        Der Agent bekam einen Fließtext ohne Kennung: nicht zuzuordnen und nicht weiterzuverfolgen.
        Nach der Länge vorzugehen dreht das um. Die Felder aus :data:`KENNFELDER` bleiben
        zusätzlich ganz unangetastet: Sie sind kurz, und mit ihnen kann der Agent weiterarbeiten —
        ohne ``id`` gibt es kein ``graph_traverse`` und kein zweites ``concept_get``. Ist die
        Grenze so eng, dass selbst danach nichts mehr passt, bleibt eine knappe Antwort mit
        ``truncated: true`` übrig. Das ist besser als eine ausführliche, die niemand zuordnen kann.
        """
        grenze = self._settings.mcp.max_response_tokens * defaults.MCP_CHARS_PER_TOKEN
        if _laenge(antwort) <= grenze:
            return antwort

        gekuerzt = dict(antwort)
        for schluessel, wert in list(gekuerzt.items()):
            if isinstance(wert, list) and len(wert) > 1:
                while len(wert) > 1 and _laenge(gekuerzt) > grenze:
                    wert = wert[:-1]
                    gekuerzt[schluessel] = wert

        while _laenge(gekuerzt) > grenze:
            texte = [
                s for s, w in gekuerzt.items() if isinstance(w, str) and w and s not in KENNFELDER
            ]
            if not texte:
                # Alles Kürzbare ist leer; übrig bleiben Zahlen, Strukturen und die Kennfelder.
                # Weiter geht es nicht, und das Ergebnis wird trotzdem als gekürzt markiert. Eine
                # zu enge Grenze führt so zu einer knappen, aber brauchbaren Antwort statt zu
                # einer vollständigen, die niemand zuordnen kann.
                break
            laengster = max(texte, key=lambda s: len(gekuerzt[s]))
            ueberschuss = _laenge(gekuerzt) - grenze
            gekuerzt[laengster] = gekuerzt[laengster][
                : max(0, len(gekuerzt[laengster]) - ueberschuss)
            ]

        gekuerzt[TRUNCATED_KEY] = True
        return gekuerzt


def _laenge(antwort: Mapping[str, Any]) -> int:
    """Die Länge der serialisierten Antwort in Zeichen."""
    return len(json.dumps(antwort, ensure_ascii=False, default=str))


def build_toolbox(runtime: Any, *, session: str = defaults.MCP_DEFAULT_SESSION) -> Toolbox:
    """Baut die Werkzeugkiste aus einer Laufzeit.

    ``runtime`` ist bewusst nicht typisiert: Die Werkzeuge liegen in der Interface-Schicht, und
    ein Import von :class:`~wissensgraph.runtime.Runtime` machte aus der Schichtenreihenfolge
    einen Zyklus. Gebraucht werden ohnehin nur vier Dienste.
    """
    return Toolbox(
        runtime.settings,
        graph=runtime.graph,
        catalog=runtime.catalog,
        curation=runtime.curation,
        clusters=runtime.clusters,
        session=session,
    )


__all__ = ["TRUNCATED_KEY", "ToolError", "ToolSpec", "Toolbox", "build_toolbox"]
