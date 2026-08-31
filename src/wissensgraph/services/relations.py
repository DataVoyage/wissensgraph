"""Semantische Kantenerkennung (§14).

Clustering sagt, dass zwei Dinge zusammengehören. Es sagt nicht, **wie**. Dafür dieser Schritt —
und er ist so zugeschnitten, dass das Modell nie den Gesamtgraphen sieht: Es bekommt genau zwei
Konzepte und die Liste der erlaubten Beziehungsarten, sonst nichts.

**"Keine Beziehung" ist die erwartete Mehrheitsantwort.** §14.2 Schritt 4 sagt das ausdrücklich,
und der Prompt sagt es dem Modell ebenso ausdrücklich. Ohne diesen Satz erfindet ein
Sprachmodell zuverlässig einen Zusammenhang, weil die Frage einen nahelegt — und der Graph füllt
sich mit Kanten, die niemand nachvollziehen kann.

**Der Vorfilter ist der eigentliche Kostenhebel.** Ein Cluster mit 25 Mitgliedern hat 300 Paare.
``relations.min_pair_similarity`` wirft davon den größten Teil weg, *bevor* ein Aufruf entsteht
(§14.5). Der zweite Hebel ist der Zwischenspeicher des Routers: Ein Wiederholungslauf über
unveränderte Paare kostet fast nichts.

**``supersedes`` wirkt nicht.** §14.4: Eine erkannte Ablösung setzt **nicht** automatisch
``status = 'deprecated'``. Sie erzeugt eine Kuratierungsaufgabe. Ein Konzept auf Verdacht eines
Modells stillzulegen widerspräche Leitprinzip 6 — die Kante wird geschrieben, die Folge zieht ein
Mensch.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import BudgetExceededError, ModelError, ModelRouter, PromptSpec
from wissensgraph.ports.repositories import UnitOfWork, UnitOfWorkFactory

_log = get_logger(__name__)

#: Der Prompt aus §14.2/§14.3. Der zweite Satz ist der wichtigste der ganzen Datei.
_BEZIEHUNG_SYSTEM = (
    "Du bestimmst die Beziehung zwischen zwei Konzepten eines Wissensgraphen. "
    "'Keine Beziehung' ist eine gültige und die häufigste richtige Antwort — antworte dann mit "
    "relationship: null. Wähle sonst genau eine Art aus 'allowed_relationships'. "
    "Antworte als JSON-Objekt mit den Feldern relationship, direction (a_to_b | b_to_a | "
    "symmetric), confidence (0 bis 1) und reasoning (ein Satz)."
)

#: Wie viele Zeichen der Beschreibung je Konzept in den Prompt gehen. §14.3 gibt ``title`` und
#: ``description`` vor; der Deckel begrenzt einen Ausreißer, ohne die Aussage zu beschneiden.
_BESCHREIBUNG_ZEICHEN = 1200


class RelationAnswer(BaseModel):
    """Die Ausgabe aus §14.3 — Pydantic-validiert, wie §11.6 es verlangt."""

    relationship: str | None = None
    direction: Literal["a_to_b", "b_to_a", "symmetric"] = defaults.RELATION_DIRECTION_A_TO_B
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str | None = None


@dataclass
class RelationReport:
    """Was ein Lauf der Kantenerkennung getan hat — die Zähler für ``runs.stats`` (§7.4)."""

    scope: str
    store: str
    clusters: int = 0
    pairs_considered: int = 0
    pairs_filtered: int = 0
    pairs_known: int = 0
    pairs_rejected: int = 0
    calls: int = 0
    cached: int = 0
    no_relation: int = 0
    below_confidence: int = 0
    edges_written: int = 0
    supersedes_tasks: int = 0
    budget_exceeded: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form — nur Zahlen und Namen, nie Inhalte (§21.1)."""
        return {
            "scope": self.scope,
            "store": self.store,
            "clusters": self.clusters,
            "pairs_considered": self.pairs_considered,
            "pairs_filtered": self.pairs_filtered,
            "pairs_known": self.pairs_known,
            "pairs_rejected": self.pairs_rejected,
            "calls": self.calls,
            "cached": self.cached,
            "no_relation": self.no_relation,
            "below_confidence": self.below_confidence,
            "edges_written": self.edges_written,
            "supersedes_tasks": self.supersedes_tasks,
            "budget_exceeded": self.budget_exceeded,
            "errors": list(self.errors),
        }


class RelationService:
    """Erkennt typisierte Beziehungen zwischen Konzepten (§14)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        router: ModelRouter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._router = router
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- öffentliche Operationen ------------------------------------------------

    def run(
        self,
        *,
        scope: str,
        run_id: UUID | None = None,
        actor: str = defaults.ACTOR_RELATIONS,
        dry_run: bool = False,
    ) -> RelationReport:
        """Prüft die Paare der stabilen Cluster eines Scopes (§14.2).

        Args:
            scope: Der zu bearbeitende Scope.
            run_id: Der Lauf, zu dem die Modellaufrufe gehören.
            actor: Wer die entstehenden Kanten verantwortet.
            dry_run: Alles fragen, nichts schreiben. Die Modellaufrufe finden trotzdem statt —
                anders ließe sich über das Ergebnis nichts sagen.

        Returns:
            Den Bericht. Ein Lauf, in dem jedes Paar "keine Beziehung" ergibt, ist ein
            erfolgreicher Lauf; §14.2 nennt das den Regelfall.
        """
        store = self._settings.store_of_scope(scope)
        bericht = RelationReport(scope=scope, store=store)
        route = self._router.describe(defaults.TASK_EMBEDDING)

        paare = self._paare(scope=scope, store=store, model_key=route.model_key, bericht=bericht)
        try:
            for links, rechts, aehnlichkeit in paare:
                self._paar_pruefen(
                    links=links,
                    rechts=rechts,
                    similarity=aehnlichkeit,
                    store=store,
                    run_id=run_id,
                    actor=actor,
                    dry_run=dry_run,
                    bericht=bericht,
                )
        except (BudgetExceededError, ProviderNotAllowedError) as exc:
            bericht.budget_exceeded = isinstance(exc, BudgetExceededError)
            _log.warning("beziehung.abgebrochen", scope=scope, grund=str(exc))

        _log.info("beziehung.beendet", **bericht.as_dict())
        return bericht

    def check_pairs(
        self,
        paare: Sequence[tuple[str, str, float]],
        *,
        store: str,
        run_id: UUID | None = None,
        actor: str = defaults.ACTOR_RELATIONS,
        dry_run: bool = False,
        bericht: RelationReport | None = None,
    ) -> RelationReport:
        """Prüft eine vorgegebene Paarliste — der Einstieg für Aufruf B aus §15.3.

        §15.3 verlangt für die Paarprüfung "identisches Format zu §14.3". Statt es dort noch
        einmal zu bauen, benutzt die Verwaiste-Knoten-Vernetzung dieselbe Methode: Ein zweites
        Prompt-Format für dieselbe Frage wäre die Stelle, an der beide Läufe auseinanderdriften.
        """
        ergebnis = bericht or RelationReport(scope="", store=store)
        try:
            for links, rechts, aehnlichkeit in paare:
                self._paar_pruefen(
                    links=links,
                    rechts=rechts,
                    similarity=aehnlichkeit,
                    store=store,
                    run_id=run_id,
                    actor=actor,
                    dry_run=dry_run,
                    bericht=ergebnis,
                )
        except (BudgetExceededError, ProviderNotAllowedError) as exc:
            ergebnis.budget_exceeded = isinstance(exc, BudgetExceededError)
            _log.warning("beziehung.abgebrochen", store=store, grund=str(exc))
        return ergebnis

    # -- Paarbildung ------------------------------------------------------------

    def _paare(
        self, *, scope: str, store: str, model_key: str, bericht: RelationReport
    ) -> tuple[tuple[str, str, float], ...]:
        """Alle Kandidatenpaare eines Scopes: innerhalb der Cluster und zwischen verwandten.

        Innerhalb: alle Mitgliedspaare eines Clusters, das die Stabilitätsschwelle erreicht hat —
        erkennbar daran, dass es überhaupt geschriebene ``member``-Kanten hat (§13.3).
        Zwischen: die zentralsten Mitglieder zweier über ``related`` verbundener Cluster
        (§14.2 Schritt 6). Ohne den Deckel wäre dieser Schritt quadratisch in der Clustergröße.
        """
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.in_scope(scope, concept_type=defaults.CONCEPT_TYPE_CLUSTER)
            mitglieder: dict[str, tuple[tuple[str, float], ...]] = {}
            verwandt: set[tuple[str, str]] = set()
            for item in cluster:
                gefunden: list[tuple[str, float]] = []
                for edge in uow.edges.list_outgoing(item.id):
                    if edge.kind == defaults.EDGE_KIND_MEMBER:
                        gefunden.append((edge.to_id, edge.weight or 0.0))
                    elif edge.kind == defaults.EDGE_KIND_RELATED:
                        verwandt.add((min(item.id, edge.to_id), max(item.id, edge.to_id)))
                if gefunden:
                    # Absteigend nach Gewicht: Das ist die Reihenfolge der Zentralität, die
                    # §14.2 Schritt 6 braucht.
                    mitglieder[item.id] = tuple(
                        sorted(gefunden, key=lambda eintrag: (-eintrag[1], eintrag[0]))
                    )
        bericht.clusters = len(mitglieder)

        roh: set[tuple[str, str]] = set()
        for geordnet in mitglieder.values():
            roh.update((min(a, b), max(a, b)) for (a, _), (b, _) in combinations(geordnet, 2))
        deckel = self._settings.relations.cross_cluster_members
        for links, rechts in sorted(verwandt):
            for a, _ in mitglieder.get(links, ())[:deckel]:
                for b, _ in mitglieder.get(rechts, ())[:deckel]:
                    if a != b:
                        roh.add((min(a, b), max(a, b)))

        bericht.pairs_considered = len(roh)
        return self._vorfiltern(sorted(roh), store=store, model_key=model_key, bericht=bericht)

    def _vorfiltern(
        self,
        paare: Sequence[tuple[str, str]],
        *,
        store: str,
        model_key: str,
        bericht: RelationReport,
    ) -> tuple[tuple[str, str, float], ...]:
        """Wirft Paare unter ``min_pair_similarity`` und bereits verbundene weg (§14.2, §14.5)."""
        schwelle = self._settings.relations.min_pair_similarity
        semantisch = frozenset(self._settings.edge_kinds.semantic)
        ergebnis: list[tuple[str, str, float]] = []

        with self._unit_of_work(store) as uow:
            vektoren: dict[str, tuple[float, ...] | None] = {}
            for links, rechts in paare:
                for name in (links, rechts):
                    if name not in vektoren:
                        vektoren[name] = uow.embeddings.get(concept_id=name, model_key=model_key)
                a, b = vektoren[links], vektoren[rechts]
                if a is None or b is None:
                    bericht.pairs_filtered += 1
                    continue
                aehnlichkeit = _kosinus(a, b)
                if aehnlichkeit < schwelle:
                    bericht.pairs_filtered += 1
                    continue
                # §14.5: "Verarbeitung nur neuer/geänderter Paare". Ein Paar, dessen Beziehung
                # schon im Graphen steht, ist keine offene Frage mehr.
                if uow.edges.kinds_between(from_id=links, to_id=rechts) & semantisch:
                    bericht.pairs_known += 1
                    continue
                # Ein Paar, dessen Beziehung ein Mensch verworfen hat, ist erst recht keine offene
                # Frage (§16.2, §24). Der Vermerk wirkt hier und nicht erst beim Schreiben: Sonst
                # kostete jedes verworfene Paar bei jedem Lauf erneut einen Modellaufruf, und
                # genau das schließt §14.5 aus.
                if uow.edges.rejected_kinds(from_id=links, to_id=rechts) & semantisch:
                    bericht.pairs_rejected += 1
                    continue
                ergebnis.append((links, rechts, aehnlichkeit))
        return tuple(ergebnis)

    # -- Einzelprüfung ----------------------------------------------------------

    def _paar_pruefen(
        self,
        *,
        links: str,
        rechts: str,
        similarity: float,
        store: str,
        run_id: UUID | None,
        actor: str,
        dry_run: bool,
        bericht: RelationReport,
    ) -> None:
        """Ein Paar, ein Modellaufruf, höchstens eine Kante (§14.2 Schritte 3 bis 5)."""
        with self._unit_of_work(store) as uow:
            konzepte = {c.id: c for c in uow.concepts.get_many((links, rechts))}
        a, b = konzepte.get(links), konzepte.get(rechts)
        if a is None or b is None:
            return

        try:
            antwort = self._router.complete(
                defaults.TASK_RELATION_EXTRACTION,
                prompt=PromptSpec(system=_BEZIEHUNG_SYSTEM, user=_eingabe(a, b, self._settings)),
                schema=RelationAnswer,
                store=store,
                run_id=run_id,
            )
        except (ProviderNotAllowedError, BudgetExceededError):
            raise
        except ModelError as exc:
            bericht.errors = (*bericht.errors, f"{links}|{rechts}: {type(exc).__name__}")
            return

        bericht.calls += 1
        if antwort.cached:
            bericht.cached += 1

        geparst = antwort.parsed
        if not isinstance(geparst, RelationAnswer) or geparst.relationship is None:
            bericht.no_relation += 1
            return
        if geparst.relationship not in self._settings.relation_kinds:
            # Eine Art, die der Graph nicht führt, ist keine Antwort — sie wäre eine neue
            # Taxonomie zur Laufzeit, und die nimmt §24 für diese Stufe ausdrücklich aus.
            bericht.no_relation += 1
            return
        if geparst.confidence < self._settings.relations.min_confidence:
            bericht.below_confidence += 1
            return

        if dry_run:
            bericht.edges_written += 1
            return
        self._kante_schreiben(
            antwort=geparst,
            links=links,
            rechts=rechts,
            similarity=similarity,
            store=store,
            run_id=run_id,
            actor=actor,
            bericht=bericht,
        )

    def _kante_schreiben(
        self,
        *,
        antwort: RelationAnswer,
        links: str,
        rechts: str,
        similarity: float,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: RelationReport,
    ) -> None:
        """Schreibt die erkannte Beziehung mit Provenienz und Confidence (§14.2 Schritt 5)."""
        route = self._router.describe(defaults.TASK_RELATION_EXTRACTION)
        von, nach = (
            (rechts, links)
            if antwort.direction == defaults.RELATION_DIRECTION_B_TO_A
            else (links, rechts)
        )
        jetzt = self._clock()
        draft = EdgeDraft(
            from_store=store,
            from_id=von,
            to_store=store,
            to_id=nach,
            kind=antwort.relationship or "",
            weight=similarity,
            confidence=antwort.confidence,
            reasoning=antwort.reasoning,
            resolved=True,
            generated_by=route.generated_by,
            generated_at=jetzt,
        )

        with self._unit_of_work(store) as uow:
            edge = uow.edges.add(draft)
            if edge is None:
                return
            bericht.edges_written += 1
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED,
                    concept_id=von,
                    edge_id=edge.id,
                    actor=actor,
                    run_id=run_id,
                    detail={
                        "kind": edge.kind,
                        "to_id": nach,
                        "confidence": antwort.confidence,
                        "direction": antwort.direction,
                    },
                )
            )
            if edge.kind == defaults.EDGE_KIND_SUPERSEDES:
                self._kuratierungsaufgabe(
                    uow, von=von, nach=nach, actor=actor, run_id=run_id, bericht=bericht
                )

    def _kuratierungsaufgabe(
        self,
        uow: UnitOfWork,
        *,
        von: str,
        nach: str,
        actor: str,
        run_id: UUID | None,
        bericht: RelationReport,
    ) -> None:
        """§14.4: ``supersedes`` erzeugt eine Aufgabe, keine Statusänderung.

        Das abgelöste Konzept behält seinen Status. Automatisches Deprecaten aufgrund einer
        Modellvermutung widerspricht Leitprinzip 6 — und wäre schwer zurückzunehmen: Ein
        ``deprecated`` steht in jeder Ansicht und in jedem Export.
        """
        uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.STATUS_CHANGED,
                concept_id=nach,
                actor=actor,
                run_id=run_id,
                detail={
                    "vorschlag": "deprecate",
                    "abgeloest_durch": von,
                    "hinweis": (
                        "Vorschlag aus der Kantenerkennung. Der Status bleibt unverändert, bis "
                        "ein Mensch entscheidet (§14.4)."
                    ),
                },
            )
        )
        bericht.supersedes_tasks += 1


def _eingabe(a: Concept, b: Concept, settings: Settings) -> str:
    """Die Eingabe aus §14.3 — zwei Konzepte und die erlaubten Beziehungsarten, sonst nichts."""
    return json.dumps(
        {
            "concept_a": _konzept(a),
            "concept_b": _konzept(b),
            "allowed_relationships": list(settings.relation_kinds),
        },
        ensure_ascii=False,
    )


def _konzept(concept: Concept) -> dict[str, str | None]:
    """Ein Konzept in der knappen Form, die §14.3 vorgibt."""
    return {
        "id": concept.id,
        "title": concept.title,
        "description": (concept.description or "")[:_BESCHREIBUNG_ZEICHEN] or None,
    }


def _kosinus(links: Sequence[float], rechts: Sequence[float]) -> float:
    """Die Kosinusähnlichkeit zweier Vektoren; 0.0, wenn einer die Länge null hat."""
    import math

    produkt = sum(a * b for a, b in zip(links, rechts, strict=False))
    laenge = math.sqrt(sum(a * a for a in links)) * math.sqrt(sum(b * b for b in rechts))
    return 0.0 if laenge == 0.0 else produkt / laenge


__all__ = ["RelationAnswer", "RelationReport", "RelationService"]
