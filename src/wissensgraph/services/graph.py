"""Kernspace-Auflösung: der Graph aus eigener Perspektive (§12).

Die Frage, die dieser Dienst beantwortet, ist nicht "was steht im Graphen", sondern "was ist von
*hier aus* wichtig". Drei Bestandteile, in dieser Reihenfolge:

1. **Traversierung** (§12.1) — von einem Startknoten aus über Kanten, über Store-Grenzen hinweg,
   bis ``max_hops`` oder ``max_nodes``.
2. **Referenzdichte** (§12.2) — wie oft aus dem persönlichen Bestand auf ein Ziel verwiesen wird.
   Zwei Menschen bekommen für dasselbe globale Dokument verschiedene Werte; genau das ist der
   Zweck.
3. **Ranking** (§12.3) — Nähe, Dichte und Aktualität zu einer Zahl.

**Warum die Traversierung in der Anwendungsschicht liegt.** ``personal`` und ``shared`` sind zwei
Datenbanken; es gibt keinen SQL-Join über die Grenze (§7.3). Das klingt nach einem Nachteil und
ist im Ergebnis keiner: Der Ablauf lädt je Hop und Store *einen* Stapel Kanten und am Ende *einen*
Stapel Konzepte je Store. Eine N+1-Abfrage entsteht dabei an keiner Stelle.

**Warum die Konzepte erst am Schluss geladen werden.** Für das Ausbreiten reicht, was in den
Kanten steht — Herkunft, Ziel, Store, Art. Inhalte braucht erst das Ergebnis. Ein Batch-Load je
Hop, wie ihn §12.1 skizziert, wäre einfacher zu lesen und teurer: Er lüde Konzepte, die die
Deckelung durch ``max_nodes`` gleich darauf wieder verwirft.

**Die Rückrichtung einer Brücke.** Der geteilte Store weiß nicht, dass es persönliche Konzepte
gibt (§12.1). Wer von einer Confluence-Seite aus wissen will, welche eigenen Notizen auf sie
zeigen, fragt deshalb nicht den geteilten Store, sondern den persönlichen. Je Hop wird darum auch
der Store angefragt, der Brücken in die Front schlagen darf — selbst wenn dort kein einziger
Front-Knoten liegt. Das ist der Preis der Trennung, und er beträgt eine Abfrage je Hop.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from wissensgraph.config import defaults
from wissensgraph.config.schema import RankingConfig, Settings
from wissensgraph.domain.bridges import bridge_sources, bridge_targets
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import ModelError, ModelRouter
from wissensgraph.ports.repositories import LexicalHit, UnitOfWorkFactory

_log = get_logger(__name__)

#: Ein Knoten ist erst mit seinem Store eindeutig: Dieselbe ID kann es in beiden Datenbanken
#: geben, und sie meint dann nicht dasselbe (§12.1, Schritt 5).
NodeKey = tuple[str, str]


class UnknownStartError(LookupError):
    """Keiner der Startknoten ist in diesem Store auffindbar."""

    def __init__(self, start: Sequence[str], store: str) -> None:
        self.start = tuple(start)
        super().__init__(
            f"Keiner der Startknoten {list(start)} liegt im Store '{store}'. "
            f"Eine Traversierung braucht einen Ausgangspunkt, den es gibt (§12.1, Schritt 1)."
        )


@dataclass(frozen=True)
class GraphNode:
    """Ein erreichtes Konzept mit allem, was die Traversierung über es herausgefunden hat."""

    concept: Concept
    hops: int
    """Kürzeste gefundene Entfernung zum Startknoten; der Start selbst hat 0."""
    density: int = 0
    """Referenzdichte nach §12.2 — auf dem aufgelösten Teilgraphen, nicht global."""
    score: float = 0.0

    @property
    def store(self) -> str:
        """Der Store des Konzepts."""
        return self.concept.store

    @property
    def key(self) -> NodeKey:
        """Die eindeutige Kennung über Store-Grenzen hinweg."""
        return (self.concept.store, self.concept.id)

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für CLI und spätere API (§16.2)."""
        return {
            "id": self.concept.id,
            "store": self.concept.store,
            "scope": self.concept.scope,
            "type": self.concept.type,
            "title": self.concept.title,
            "status": str(self.concept.status),
            "hops": self.hops,
            "density": self.density,
            "score": round(self.score, 6),
        }


@dataclass(frozen=True)
class Traversal:
    """Das Ergebnis einer Kernspace-Auflösung (§12.1)."""

    start: tuple[NodeKey, ...]
    nodes: tuple[GraphNode, ...]
    """Alle erreichten Knoten, bestbewertete zuerst. Der Startknoten ist enthalten."""
    edges: tuple[Edge, ...]
    hops: int
    """Die tatsächlich gelaufene Tiefe."""
    truncated: bool
    """Ob ``max_nodes`` erreicht wurde — dann ist das Ergebnis ein Ausschnitt (§12.1)."""
    queries: int
    """Wie viele Datenbankabfragen es gekostet hat. Steht im Ergebnis und nicht nur im Log,
    weil §24 (Stufe 6) eine Obergrenze dafür abnimmt — eine Zusicherung, die prüfbar sein muss."""

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form."""
        return {
            "start": [f"{store}:{concept_id}" for store, concept_id in self.start],
            "hops": self.hops,
            "truncated": self.truncated,
            "queries": self.queries,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": len(self.edges),
        }


@dataclass(frozen=True)
class SearchResult:
    """Das Ergebnis einer Suche mit der Angabe, *wie* gesucht wurde (§12.4)."""

    query: str
    store: str
    hits: tuple[GraphNode, ...]
    mode: str = "lexical"
    """§12.4: "degradiert die Suche automatisch auf reine Volltext-/Trigrammsuche und markiert
    das im Ergebnis". Solange es keine Embeddings gibt, ist das der einzige Modus — und dass er
    im Ergebnis steht, ist der Unterschied zwischen einer Einschränkung und einer Täuschung."""

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form."""
        return {
            "query": self.query,
            "store": self.store,
            "mode": self.mode,
            "hits": [hit.as_dict() for hit in self.hits],
        }


@dataclass
class _Front:
    """Die Knoten eines Hops, nach Store gruppiert — die Arbeitsliste der Traversierung."""

    nach_store: dict[str, set[str]] = field(default_factory=dict)

    def add(self, key: NodeKey) -> None:
        store, concept_id = key
        self.nach_store.setdefault(store, set()).add(concept_id)

    def __bool__(self) -> bool:
        return any(self.nach_store.values())


class GraphService:
    """Lesender Zugang zum Graphen: Traversierung, Dichte, Ranking, Suche (§12)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        *,
        router: ModelRouter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration; liefert Grenzen und Ranking-Gewichte.
            unit_of_work: Fabrik für Transaktionen je Store. Auch der Lesepfad geht über sie —
                es gibt keinen zweiten Weg zu einer Verbindung (§20.1).
            router: Der Model-Router für die semantische Hälfte der Suche. **Optional**, und das
                ist die Aussage: Ohne ihn sucht dieser Dienst lexikalisch und sagt es im Ergebnis
                (§12.4). Der Lesepfad hängt nicht an der Verfügbarkeit eines Modells.
            clock: Zeitquelle für die Aktualitätskomponente des Rankings.
        """
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._router = router
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- öffentliche Operationen ------------------------------------------------

    def traverse(
        self,
        start: Sequence[str],
        *,
        store: str,
        hops: int | None = None,
        max_nodes: int | None = None,
        ranking: RankingConfig | None = None,
        include_tombstones: bool = False,
    ) -> Traversal:
        """Löst den Kernspace um einen oder mehrere Startknoten auf (§12.1).

        Args:
            start: Die Konzept-IDs, von denen aus gesucht wird.
            store: Der Store, in dem die Startknoten liegen.
            hops: Tiefe; ohne Angabe ``traversal.default_hops``, gedeckelt auf ``max_hops``.
            max_nodes: Obergrenze der Knoten; ohne Angabe ``traversal.max_nodes``.
            ranking: Abweichende Gewichte. §12.3: "Die Gewichte sind pro Anfrage überschreibbar,
                damit sich Varianten in der UI vergleichen lassen."
            include_tombstones: Ob Grabsteine im Ergebnis erscheinen (§12.3).

        Returns:
            Die erreichten Knoten, nach Bewertung sortiert, mit den Kanten dazwischen.

        Raises:
            UnknownStartError: Wenn keiner der Startknoten existiert.
        """
        grenzen = self._settings.traversal
        tiefe = min(grenzen.max_hops, grenzen.default_hops if hops is None else max(1, hops))
        deckel = grenzen.max_nodes if max_nodes is None else max(1, max_nodes)

        bruecken, abfragen = self._bruecken_index()
        entfernung, kanten, kosten, gedeckelt = self._ausbreiten(
            start=start, store=store, tiefe=tiefe, deckel=deckel, bruecken=bruecken
        )
        abfragen += kosten
        konzepte, ladekosten = self._konzepte_laden(entfernung)
        abfragen += ladekosten

        if not any(key in konzepte for key in ((store, item) for item in start)):
            raise UnknownStartError(start, store)

        knoten = self._bewerten(
            entfernung=entfernung,
            konzepte=konzepte,
            kanten=kanten,
            tiefe=tiefe,
            ranking=ranking or grenzen.ranking,
            include_tombstones=include_tombstones,
        )
        _log.info(
            "graph.traversiert",
            store=store,
            start=list(start),
            hops=tiefe,
            nodes=len(knoten),
            queries=abfragen,
        )
        return Traversal(
            start=tuple((store, item) for item in start),
            nodes=knoten,
            edges=tuple(kanten),
            hops=tiefe,
            truncated=gedeckelt,
            queries=abfragen,
        )

    def search(
        self,
        query: str,
        *,
        store: str,
        limit: int | None = None,
        granularity: str = defaults.SEARCH_GRANULARITY_AUTO,
    ) -> SearchResult:
        """Die zweistufige Suche aus §12.4 — Cluster zuerst, Dokumente danach.

        Die Reihenfolge ist die Aussage: Wer sucht, will meist wissen, *worum es geht*, und die
        Antwort darauf ist ein Thema und keine Liste von Dokumenten. Erst wenn kein Cluster nah
        genug liegt, wird auf die Dokumentebene zurückgefallen — hybrid aus Vektorähnlichkeit und
        Volltext, zusammengeführt über die Plätze.

        Args:
            query: Der Suchbegriff.
            store: Der zu durchsuchende Store.
            limit: Höchstzahl Treffer; ohne Angabe ``search.limit``.
            granularity: ``auto`` (erst Cluster, dann Dokumente), ``cluster`` oder ``document``.

        Returns:
            Das Ergebnis samt ``mode``. Ohne verfügbares Embedding-Modell ist der Modus
            ``lexical`` — §12.4: "Ein stiller Qualitätsverlust ohne Hinweis wäre die schlechtere
            Variante."
        """
        deckel = limit if limit is not None else self._settings.search.limit
        with self._unit_of_work(store) as uow:
            lexikalisch = uow.concepts.search_lexical(query, limit=deckel)

        vektor = self._anfragevektor(query, store=store)
        if vektor is None:
            return self._ergebnis(query, store, lexikalisch, defaults.SEARCH_MODE_LEXICAL)

        if granularity != defaults.SEARCH_GRANULARITY_DOCUMENT:
            anker = self._cluster_anker(vektor, store=store, limit=deckel)
            if anker or granularity == defaults.SEARCH_GRANULARITY_CLUSTER:
                return SearchResult(
                    query=query, store=store, hits=anker, mode=defaults.SEARCH_MODE_CLUSTER
                )

        return self._hybrid(query, vektor, lexikalisch, store=store, limit=deckel)

    # -- innere Abläufe: Suche ---------------------------------------------------

    def _anfragevektor(self, query: str, *, store: str) -> tuple[float, ...] | None:
        """Der Vektor der Anfrage — oder ``None``, wenn semantisch nicht gesucht werden kann.

        Drei Gründe für ``None``, und alle drei sind zulässige Betriebszustände: kein Router, kein
        Embedding im Store (dann gäbe es nichts zu vergleichen), oder die Store-Policy verbietet
        den Aufruf (§11.5). In allen dreien ist die lexikalische Suche die richtige Antwort — und
        kein Fehler.
        """
        if self._router is None or not query.strip():
            return None

        route = self._router.describe(defaults.TASK_EMBEDDING)
        with self._unit_of_work(store) as uow:
            vorhanden = uow.embeddings.count(model_key=route.model_key)
        if vorhanden == 0:
            return None

        try:
            ergebnis = self._router.embed(defaults.TASK_EMBEDDING, [query], store=store)
        except (ProviderNotAllowedError, ModelError) as exc:
            _log.info("suche.lexikalisch", store=store, grund=type(exc).__name__)
            return None
        return ergebnis.vectors[0] if ergebnis.vectors else None

    def _cluster_anker(
        self, vektor: Sequence[float], *, store: str, limit: int
    ) -> tuple[GraphNode, ...]:
        """Stufe 1 aus §12.4: Zentroide über der Schwelle, als Anker statt ihrer Mitglieder."""
        schwelle = self._settings.search.cluster_hit_threshold
        route = self._router.describe(defaults.TASK_EMBEDDING)  # type: ignore[union-attr]
        with self._unit_of_work(store) as uow:
            treffer = [
                hit
                for hit in uow.clusters.search_centroids(
                    vector=vektor, model_key=route.model_key, limit=limit
                )
                if hit.similarity >= schwelle
            ]
            konzepte = {
                concept.id: concept
                for concept in uow.concepts.get_many([hit.concept_id for hit in treffer])
            }
        return tuple(
            GraphNode(concept=konzepte[hit.concept_id], hops=0, score=hit.similarity)
            for hit in treffer
            if hit.concept_id in konzepte
        )

    def _hybrid(
        self,
        query: str,
        vektor: Sequence[float],
        lexikalisch: Sequence[LexicalHit],
        *,
        store: str,
        limit: int,
    ) -> SearchResult:
        """Stufe 2 aus §12.4: Vektorähnlichkeit und Volltext über Reciprocal Rank Fusion.

        Zusammengeführt werden die *Plätze* und nicht die Werte. Eine Kosinusähnlichkeit von 0,71
        und ein ``ts_rank`` von 0,08 sagen nichts übereinander; ihre Ränge dagegen sind
        vergleichbar. Derselbe Grund wie in §12.4 für die lexikalische Hälfte allein — nur dass
        hier zwei Verfahren zusammenkommen, die wirklich verschieden sehen.
        """
        route = self._router.describe(defaults.TASK_EMBEDDING)  # type: ignore[union-attr]
        with self._unit_of_work(store) as uow:
            semantisch = uow.embeddings.search(
                vector=vektor, model_key=route.model_key, limit=limit
            )
            k = self._settings.search.rrf_k
            punkte: dict[str, float] = {}
            for rang, hit in enumerate(lexikalisch, start=1):
                punkte[hit.concept.id] = punkte.get(hit.concept.id, 0.0) + 1.0 / (k + rang)
            for rang, nachbar in enumerate(semantisch, start=1):
                punkte[nachbar.concept_id] = punkte.get(nachbar.concept_id, 0.0) + 1.0 / (k + rang)

            geordnet = sorted(punkte.items(), key=lambda item: (-item[1], item[0]))[:limit]
            konzepte = {
                concept.id: concept
                for concept in uow.concepts.get_many([concept_id for concept_id, _ in geordnet])
            }

        return SearchResult(
            query=query,
            store=store,
            hits=tuple(
                GraphNode(concept=konzepte[concept_id], hops=0, score=punktzahl)
                for concept_id, punktzahl in geordnet
                if concept_id in konzepte
            ),
            mode=defaults.SEARCH_MODE_HYBRID,
        )

    def _ergebnis(
        self, query: str, store: str, treffer: Sequence[LexicalHit], mode: str
    ) -> SearchResult:
        """Ein rein lexikalisches Ergebnis."""
        return SearchResult(
            query=query,
            store=store,
            hits=tuple(GraphNode(concept=hit.concept, hops=0, score=hit.score) for hit in treffer),
            mode=mode,
        )

    # -- innere Abläufe: Ausbreiten ---------------------------------------------

    def _bruecken_index(self) -> tuple[dict[str, dict[str, frozenset[str]]], int]:
        """Wohin dieser Bestand überhaupt Brücken schlägt — einmal je Traversierung (§12.1).

        Ohne diese Frage müsste jeder Hop den persönlichen Store nach der Rückrichtung fragen,
        auch wenn es dort keine einzige Brücke gibt. Eine Traversierung durch drei Hops des
        geteilten Graphen bezahlte damit drei Abfragen an eine Datenbank, die zur Antwort nichts
        beiträgt. Eine Abfrage vorab beantwortet das für den ganzen Lauf.

        Die Antwort ist eine Menge von IDs und keine Kantenliste: Sie sagt nur, *ob* für einen
        Front-Knoten überhaupt eine Brücke in Frage kommt. Die Kanten selbst holt jeder Hop.
        """
        index: dict[str, dict[str, frozenset[str]]] = {}
        abfragen = 0
        for store in self._settings.stores:
            if not bridge_targets(store, self._settings.stores):
                continue
            with self._unit_of_work(store) as uow:
                index[store] = dict(uow.edges.foreign_targets())
            abfragen += 1
        return index, abfragen

    def _ausbreiten(
        self,
        *,
        start: Sequence[str],
        store: str,
        tiefe: int,
        deckel: int,
        bruecken: Mapping[str, Mapping[str, frozenset[str]]],
    ) -> tuple[dict[NodeKey, int], list[Edge], int, bool]:
        """Die Breitensuche aus §12.1, Schritt 1 bis 6.

        Gearbeitet wird ausschließlich auf Kanten. Ein Knoten ist hier nichts weiter als ein Paar
        aus Store und ID — das genügt zum Ausbreiten und kostet keine einzige Inhaltsabfrage.
        """
        entfernung: dict[NodeKey, int] = {(store, item): 0 for item in start}
        kanten: list[Edge] = []
        gesehen: set[tuple[str, str, str, str, str]] = set()
        abfragen = 0
        gedeckelt = False

        front = _Front()
        for item in start:
            front.add((store, item))

        for hop in range(1, tiefe + 1):
            neue = _Front()
            for aktueller_store in self._zu_befragende_stores(front, bruecken):
                gefunden, kosten = self._kanten_laden(aktueller_store, front, bruecken)
                abfragen += kosten
                for edge in gefunden:
                    if edge.triple in gesehen:
                        continue
                    gesehen.add(edge.triple)
                    kanten.append(edge)
                    for key in self._enden(edge):
                        if key in entfernung:
                            continue
                        if len(entfernung) >= deckel:
                            gedeckelt = True
                            continue
                        entfernung[key] = hop
                        neue.add(key)
            if not neue or gedeckelt:
                break
            front = neue

        return entfernung, kanten, abfragen, gedeckelt

    def _zu_befragende_stores(
        self, front: _Front, bruecken: Mapping[str, Mapping[str, frozenset[str]]]
    ) -> tuple[str, ...]:
        """Welche Stores für diesen Hop überhaupt anzufragen sind.

        Nicht nur die, in denen die Front liegt. Zeigt eine persönliche Notiz auf eine geteilte
        Seite, liegt diese Kante im *persönlichen* Store — der geteilte weiß nichts von ihr
        (§12.1). Eine Traversierung, die nur die Front-Stores fragte, fände die Rückrichtung
        einer Brücke deshalb nie.

        Umgekehrt wird ein fremder Store nur dann gefragt, wenn er laut Brücken-Index überhaupt
        auf einen Knoten der Front zeigen *kann*. Ein rein geteilter Graph kostet damit keine
        einzige Abfrage an den persönlichen Store.
        """
        gefragt = {store for store, ids in front.nach_store.items() if ids}
        for store in tuple(gefragt):
            for quelle in bridge_sources(store, self._settings.stores):
                if self._brueckenziele(quelle, store, front, bruecken):
                    gefragt.add(quelle)
        return tuple(sorted(gefragt))

    @staticmethod
    def _brueckenziele(
        von: str,
        nach: str,
        front: _Front,
        bruecken: Mapping[str, Mapping[str, frozenset[str]]],
    ) -> tuple[str, ...]:
        """Die Front-Knoten in ``nach``, auf die aus ``von`` überhaupt eine Brücke zeigen kann."""
        moeglich = bruecken.get(von, {}).get(nach, frozenset())
        return tuple(sorted(front.nach_store.get(nach, set()) & moeglich))

    def _kanten_laden(
        self, store: str, front: _Front, bruecken: Mapping[str, Mapping[str, frozenset[str]]]
    ) -> tuple[tuple[Edge, ...], int]:
        """Alle Kanten *dieses* Stores, die die Front berühren (§12.1, Schritt 2).

        Zwei Dinge aus derselben Tabelle: die Kanten an den eigenen Front-Knoten und die Brücken,
        die aus diesem Store heraus auf Front-Knoten in anderen Stores zeigen. Beide Abfragen
        gehen an dieselbe Datenbank; gezählt werden sie einzeln, weil es einzelne Roundtrips sind.
        """
        eigene = tuple(sorted(front.nach_store.get(store, ())))
        fremde = {
            anderer: ziele
            for anderer in bridge_targets(store, self._settings.stores)
            if (ziele := self._brueckenziele(store, anderer, front, bruecken))
        }
        if not eigene and not fremde:
            return (), 0

        with self._unit_of_work(store) as uow:
            gefunden = list(uow.edges.neighbourhood(eigene)) if eigene else []
            for ziel_store, ziel_ids in fremde.items():
                gefunden.extend(uow.edges.bridges_into(to_store=ziel_store, to_ids=ziel_ids))
        return tuple(gefunden), (1 if eigene else 0) + len(fremde)

    @staticmethod
    def _enden(edge: Edge) -> tuple[NodeKey, NodeKey]:
        """Beide Enden einer Kante als Knotenschlüssel.

        Die Traversierung folgt Kanten in beide Richtungen. §12.1 unterscheidet die Richtung
        nicht; §7.7 tut es für die *Gewichtung* ("abwärts vs. seitwärts"), und die schlägt sich
        im Ranking nieder, nicht in der Erreichbarkeit. Wer von einem Cluster aus sucht, will
        seine Mitglieder sehen — und wer von einem Mitglied aus sucht, sein Cluster.
        """
        return (edge.from_store, edge.from_id), (edge.to_store, edge.to_id)

    def _konzepte_laden(
        self, entfernung: Mapping[NodeKey, int]
    ) -> tuple[dict[NodeKey, Concept], int]:
        """Ein Batch-Load je Store, ganz am Ende (§12.1, Schritt 4)."""
        nach_store: dict[str, list[str]] = {}
        for store, concept_id in entfernung:
            nach_store.setdefault(store, []).append(concept_id)

        geladen: dict[NodeKey, Concept] = {}
        for store, ids in sorted(nach_store.items()):
            with self._unit_of_work(store) as uow:
                for concept in uow.concepts.get_many(sorted(ids)):
                    geladen[(store, concept.id)] = concept
        return geladen, len(nach_store)

    # -- innere Abläufe: Bewerten ------------------------------------------------

    def _bewerten(
        self,
        *,
        entfernung: Mapping[NodeKey, int],
        konzepte: Mapping[NodeKey, Concept],
        kanten: Sequence[Edge],
        tiefe: int,
        ranking: RankingConfig,
        include_tombstones: bool,
    ) -> tuple[GraphNode, ...]:
        """Dichte und Bewertung, dann Sortierung (§12.2, §12.3)."""
        sichtbar = {
            key: concept
            for key, concept in konzepte.items()
            if include_tombstones or concept.status is not ConceptStatus.TOMBSTONE
        }
        eingehend = self._eingehende_kanten(kanten)
        dichten = {key: self._dichte(key, eingehend, tiefe) for key in sichtbar}
        groesste = max(dichten.values(), default=0)
        jetzt = self._clock()

        knoten = [
            GraphNode(
                concept=concept,
                hops=entfernung[key],
                density=dichten[key],
                score=self._score(
                    hops=entfernung[key],
                    density=dichten[key],
                    groesste_dichte=groesste,
                    concept=concept,
                    ranking=ranking,
                    jetzt=jetzt,
                ),
            )
            for key, concept in sichtbar.items()
        ]
        # Bei gleichem Wert entscheidet die ID: Zwei Aufrufe über denselben Bestand sollen
        # dieselbe Reihenfolge liefern, sonst ist ein Vergleich zweier Gewichtungen wertlos.
        knoten.sort(key=lambda node: (-node.score, node.concept.id))
        return tuple(knoten)

    @staticmethod
    def _eingehende_kanten(kanten: Sequence[Edge]) -> dict[NodeKey, list[tuple[NodeKey, str]]]:
        """Der Teilgraph, rückwärts gelesen: zu jedem Knoten, wer auf ihn zeigt."""
        rueckwaerts: dict[NodeKey, list[tuple[NodeKey, str]]] = {}
        for edge in kanten:
            ziel = (edge.to_store, edge.to_id)
            quelle = (edge.from_store, edge.from_id)
            rueckwaerts.setdefault(ziel, []).append((quelle, edge.kind))
        return rueckwaerts

    @staticmethod
    def _dichte(
        ziel: NodeKey, eingehend: Mapping[NodeKey, list[tuple[NodeKey, str]]], tiefe: int
    ) -> int:
        """Referenzdichte nach §12.2 — auf dem aufgelösten Teilgraphen, nicht global.

            "density(z) = Anzahl der Konzepte im personal-Store, die innerhalb von d Hops auf z
            oder auf ein Cluster von z verweisen"

        Umgesetzt als Rückwärtssuche vom Ziel aus. Ein Schritt entlang einer ``member``-Kante
        zählt dabei **nicht** als Hop: Ein Cluster ist keine Zwischenstation, sondern eine andere
        Adresse für dieselbe Sache. Genau das meint "oder auf ein Cluster von z" — ohne diese
        Ausnahme wäre der Zusatz bei ``d = 1`` wirkungslos.

        Gezählt werden Knoten des persönlichen Stores, das Ziel selbst nie. Die Zahl ist damit
        eine Aussage über den eigenen Bestand und nicht über die Beliebtheit eines Dokuments.
        """
        gezaehlt: set[NodeKey] = set()
        besucht: dict[NodeKey, int] = {ziel: 0}
        offen: list[tuple[NodeKey, int]] = [(ziel, 0)]

        while offen:
            knoten, entfernung = offen.pop()
            for vorgaenger, kind in eingehend.get(knoten, ()):
                naechste = entfernung + (0 if kind == defaults.EDGE_KIND_MEMBER else 1)
                if naechste > tiefe:
                    continue
                if vorgaenger in besucht and besucht[vorgaenger] <= naechste:
                    continue
                besucht[vorgaenger] = naechste
                offen.append((vorgaenger, naechste))
                if vorgaenger[0] == defaults.STORE_PERSONAL and vorgaenger != ziel:
                    gezaehlt.add(vorgaenger)

        return len(gezaehlt)

    def _score(
        self,
        *,
        hops: int,
        density: int,
        groesste_dichte: int,
        concept: Concept,
        ranking: RankingConfig,
        jetzt: datetime,
    ) -> float:
        """Die Formel aus §12.3, unverändert.

        ``normalize`` ist die Division durch den größten Wert *dieser Antwort*. Eine globale
        Normierung gäbe es nicht umsonst — sie bräuchte einen Bezugswert über den ganzen Bestand,
        und der änderte sich mit jedem Lauf.
        """
        naehe = 1.0 / (1.0 + hops)
        dichte = 0.0 if groesste_dichte == 0 else density / groesste_dichte
        alter_tage = max(0.0, (jetzt - concept.updated_at).total_seconds() / 86400.0)
        aktualitaet = math.exp(-math.log(2) * alter_tage / ranking.recency_half_life_days)
        return (
            ranking.hop_weight * naehe
            + ranking.density_weight * dichte
            + ranking.recency_weight * aktualitaet
        )


__all__ = ["GraphNode", "GraphService", "SearchResult", "Traversal", "UnknownStartError"]
