"""Verwaiste-Knoten-Vernetzung (§15).

§14 findet nur Beziehungen zwischen Konzepten, die das Clustering bereits nebeneinandergestellt
hat. Der interessante Fall fällt dabei durch — §15.1 nennt ihn: "Ein Runbook erwähnt einen
Auth-Service, dessen Dokumentation embedding-mäßig weit entfernt liegt, weil das Runbook
thematisch um Incident Response kreist."

Dagegen ein eigener Lauf in zwei Stufen, und die Reihenfolge ist der ganze Trick: **erst Code,
dann Modell.**

* **Stufe 1a, Textabgleich.** Ein Bezeichner, der wörtlich im Text eines anderen Konzepts steht,
  ist ein Beleg und keine Vermutung. Die Kante entsteht mit ``confidence: 1.0`` und ohne einen
  einzigen Token. §15.2a: "ein Modell wäre hier reine Verschwendung."
* **Stufe 1b, Nähe.** Eine breite Vektorsuche über den *ganzen* Scope, nicht nur das eigene
  Cluster. Oberhalb von ``proximity_auto_commit`` wird direkt geschrieben, im Band darunter wird
  vorgemerkt, darunter verworfen.
* **Stufe 2, Modell.** Nur für Knoten, die nach Stufe 1 immer noch lose sind, und nur bei
  ``use_llm: true``. Aufruf A schlägt Cluster vor, Aufruf B prüft Paare — beide sehen nie mehr
  als einen Knoten plus eine kleine Liste.

**Mit jedem Lauf schrumpft die Menge.** Ein Knoten, der eine semantische Kante bekommen hat, ist
beim nächsten Mal nicht mehr lose und fällt aus der Sicht heraus. Das ist keine Nebenwirkung,
sondern das Abbruchkriterium des ganzen Verfahrens.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from wissensgraph.config import defaults
from wissensgraph.config.patterns import PatternConfig, load_patterns
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.hashing import content_hash
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import BudgetExceededError, ModelError, ModelRouter, PromptSpec
from wissensgraph.ports.repositories import LooseConcept, UnitOfWorkFactory
from wissensgraph.services.relations import RelationReport, RelationService

_log = get_logger(__name__)

#: Der Prompt aus §15.3, Aufruf A. Die Mitgliederliste je Cluster ist der Ersatz für das, was eine
#: ``index.md`` bei echten OKF-Dateien geleistet hätte: dem Modell mehr vom Cluster zeigen als nur
#: die generierte Zusammenfassung.
_ZUORDNUNG_SYSTEM = (
    "Du ordnest ein einzelnes Konzept einer bestehenden Themengruppe zu. Du bekommst das Konzept "
    "und eine Übersicht der Gruppen mit ihren Mitgliedstiteln. Passt keine Gruppe, darfst du eine "
    "neue vorschlagen — oder gar nichts. Ein leeres Ergebnis ohne jeden Vorschlag ist gültig. "
    "Antworte als JSON-Objekt mit suggested_cluster_ids (Liste), propose_new_cluster (Objekt mit "
    "title und description, oder null), confidence (0 bis 1) und reasoning (ein Satz). "
    "suggested_cluster_ids und propose_new_cluster schließen einander aus."
)


class NewClusterProposal(BaseModel):
    """Ein vorgeschlagenes neues Cluster (§15.3)."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ClusterSuggestion(BaseModel):
    """Die Ausgabe von Aufruf A (§15.3)."""

    suggested_cluster_ids: tuple[str, ...] = ()
    propose_new_cluster: NewClusterProposal | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str | None = None

    @model_validator(mode="after")
    def _check_exclusive(self) -> ClusterSuggestion:
        """§15.3: "schließen sich gegenseitig aus"."""
        if self.suggested_cluster_ids and self.propose_new_cluster is not None:
            raise ValueError(
                "suggested_cluster_ids und propose_new_cluster schließen einander aus: Entweder "
                "der Knoten gehört zu etwas Bestehendem, oder er begründet etwas Neues (§15.3)."
            )
        return self


@dataclass
class OrphanReport:
    """Was ein Vernetzungslauf getan hat — die Zähler für ``runs.stats`` (§7.4)."""

    scope: str
    store: str
    loose_before: int = 0
    loose_after: int = 0
    text_matches: int = 0
    proximity_committed: int = 0
    proximity_candidates: int = 0
    cluster_suggestions: int = 0
    clusters_created: int = 0
    model_edges: int = 0
    calls: int = 0
    dry_run: bool = False
    budget_exceeded: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form — nur Zahlen und Namen, nie Inhalte (§21.1)."""
        return {
            "scope": self.scope,
            "store": self.store,
            "loose_before": self.loose_before,
            "loose_after": self.loose_after,
            "text_matches": self.text_matches,
            "proximity_committed": self.proximity_committed,
            "proximity_candidates": self.proximity_candidates,
            "cluster_suggestions": self.cluster_suggestions,
            "clusters_created": self.clusters_created,
            "model_edges": self.model_edges,
            "calls": self.calls,
            "dry_run": self.dry_run,
            "budget_exceeded": self.budget_exceeded,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class OrphanRequest:
    """Die Parameter aus §15.4 — jeder einzeln überschreibbar (§6.2).

    Als eigenes Objekt und nicht als zwölf Argumente, weil dieselbe Anfrage über drei Wege kommt:
    CLI-Flags, API-Parameter und ein Job der Queue. Sie soll auf allen dreien dasselbe bedeuten.
    """

    scope: str
    loose_threshold: int | None = None
    proximity_top_n: int | None = None
    proximity_auto_commit: float | None = None
    proximity_candidate_band: float | None = None
    use_llm: bool | None = None
    cluster_suggestion_limit: int | None = None
    cluster_preview_members: int | None = None
    min_confidence: float | None = None
    pattern_files: tuple[str, ...] = ()
    dry_run: bool = False

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> OrphanRequest:
        """Baut die Anfrage aus den ``params`` eines Jobs (§16.3).

        Unbekannte Schlüssel werden übergangen und nicht zum Fehler: In ``runs.params`` steht,
        was der Lauf angestoßen hat, und dort kann mit einer späteren Stufe etwas dazukommen, das
        dieser Lauf nicht kennt. Ein Abbruch machte den Lauf unwiederholbar.
        """
        bekannt = {feld.name for feld in fields(cls)}
        werte = {
            name: wert for name, wert in params.items() if name in bekannt and wert is not None
        }
        werte.setdefault("scope", "")
        muster = werte.get("pattern_files")
        if muster is not None:
            werte["pattern_files"] = tuple(muster)
        return cls(**werte)

    def gegen(self, settings: Settings) -> OrphanRequest:
        """Füllt die offenen Werte aus der Konfiguration auf (§15.4, §6.2)."""
        vorgabe = settings.orphans
        return OrphanRequest(
            scope=self.scope,
            loose_threshold=_oder(self.loose_threshold, vorgabe.loose_threshold),
            proximity_top_n=_oder(self.proximity_top_n, vorgabe.proximity_top_n),
            proximity_auto_commit=_oder(self.proximity_auto_commit, vorgabe.proximity_auto_commit),
            proximity_candidate_band=_oder(
                self.proximity_candidate_band, vorgabe.proximity_candidate_band
            ),
            use_llm=_oder(self.use_llm, vorgabe.use_llm),
            cluster_suggestion_limit=_oder(
                self.cluster_suggestion_limit, vorgabe.cluster_suggestion_limit
            ),
            cluster_preview_members=_oder(
                self.cluster_preview_members, vorgabe.cluster_preview_members
            ),
            min_confidence=_oder(self.min_confidence, vorgabe.min_confidence),
            pattern_files=self.pattern_files or vorgabe.pattern_files,
            dry_run=self.dry_run,
        )


class OrphanService:
    """Vernetzt lose Knoten in zwei Stufen — erst Code, dann Modell (§15)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        router: ModelRouter,
        *,
        relations: RelationService | None = None,
        clock: Callable[[], datetime] | None = None,
        new_cluster_id: Callable[[], str] | None = None,
    ) -> None:
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._router = router
        self._relations = relations or RelationService(settings, unit_of_work, router, clock=clock)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_cluster_id or (
            lambda: f"{defaults.ID_PREFIX_CLUSTER}:{uuid4().hex[:12]}"
        )

    # -- öffentliche Operationen ------------------------------------------------

    def run(
        self,
        request: OrphanRequest,
        *,
        run_id: UUID | None = None,
        actor: str = defaults.ACTOR_ORPHANS,
    ) -> OrphanReport:
        """Führt einen Vernetzungslauf über einen Scope aus (§15.2, §15.3)."""
        parameter = request.gegen(self._settings)
        store = self._settings.store_of_scope(parameter.scope)
        bericht = OrphanReport(scope=parameter.scope, store=store, dry_run=parameter.dry_run)
        schwelle = parameter.loose_threshold or 0

        lose = self._lose(scope=parameter.scope, store=store, threshold=schwelle)
        bericht.loose_before = len(lose)
        if not lose:
            _log.info("verwaist.nichts_zu_tun", scope=parameter.scope)
            return bericht

        muster = load_patterns(
            self._settings,
            paths=tuple(Path(eintrag) for eintrag in parameter.pattern_files) or None,
        )
        self._textabgleich(
            lose=lose,
            muster=muster,
            scope=parameter.scope,
            store=store,
            run_id=run_id,
            actor=actor,
            bericht=bericht,
        )
        kandidaten = self._naehe(
            lose=lose, parameter=parameter, store=store, run_id=run_id, actor=actor, bericht=bericht
        )

        if parameter.use_llm:
            try:
                self._stufe_zwei(
                    kandidaten=kandidaten,
                    parameter=parameter,
                    store=store,
                    run_id=run_id,
                    actor=actor,
                    bericht=bericht,
                )
            except (BudgetExceededError, ProviderNotAllowedError) as exc:
                bericht.budget_exceeded = isinstance(exc, BudgetExceededError)
                _log.warning("verwaist.abgebrochen", scope=parameter.scope, grund=str(exc))

        bericht.loose_after = len(
            self._lose(scope=parameter.scope, store=store, threshold=schwelle)
        )
        _log.info("verwaist.beendet", **bericht.as_dict())
        return bericht

    # -- Stufe 1 ----------------------------------------------------------------

    def _lose(self, *, scope: str, store: str, threshold: int) -> tuple[LooseConcept, ...]:
        """Die losen Knoten aus ``v_loose_concepts`` (§15.1)."""
        with self._unit_of_work(store) as uow:
            return uow.concepts.loose(threshold=threshold, scope=scope)

    def _textabgleich(
        self,
        *,
        lose: Sequence[LooseConcept],
        muster: Sequence[PatternConfig],
        scope: str,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: OrphanReport,
    ) -> None:
        """§15.2a: Bezeichner, die wörtlich anderswo vorkommen, werden zu Kanten.

        Der Index wird **einmal** über den ganzen Scope gebaut und nicht je losem Knoten neu.
        Ohne ihn wäre der Schritt quadratisch in der Zahl der Konzepte — und das ausgerechnet bei
        dem Teil des Verfahrens, dessen Vorzug seine Billigkeit ist.
        """
        if not muster:
            return

        with self._unit_of_work(store) as uow:
            konzepte = uow.concepts.in_scope(scope)

        index: dict[str, set[str]] = {}
        eigene: dict[str, set[str]] = {}
        uebersetzt = [item.compiled() for item in muster]
        for concept in konzepte:
            treffer = _treffer(concept, uebersetzt)
            eigene[concept.id] = treffer
            for token in treffer:
                index.setdefault(token, set()).add(concept.id)

        jetzt = self._clock()
        for knoten in lose:
            ziele = {
                anderer
                for token in eigene.get(knoten.id, set())
                for anderer in index.get(token, set())
                if anderer != knoten.id
            }
            for ziel in sorted(ziele):
                if bericht.dry_run:
                    bericht.text_matches += 1
                    continue
                with self._unit_of_work(store) as uow:
                    edge = uow.edges.add(
                        EdgeDraft(
                            from_store=store,
                            from_id=knoten.id,
                            to_store=store,
                            to_id=ziel,
                            kind=defaults.EDGE_KIND_REFERENCES,
                            confidence=1.0,
                            resolved=True,
                            generated_by=defaults.GENERATED_BY_TEXT_MATCH,
                            generated_at=jetzt,
                        )
                    )
                    if edge is None:
                        continue
                    bericht.text_matches += 1
                    uow.changes.append(
                        ChangeEntry(
                            change_type=ChangeType.EDGE_ADDED,
                            concept_id=knoten.id,
                            edge_id=edge.id,
                            actor=actor,
                            run_id=run_id,
                            detail={"kind": edge.kind, "to_id": ziel, "grund": "text-match"},
                        )
                    )

    def _naehe(
        self,
        *,
        lose: Sequence[LooseConcept],
        parameter: OrphanRequest,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: OrphanReport,
    ) -> dict[str, tuple[tuple[str, float], ...]]:
        """§15.2b: breite Vektorsuche mit Auto-Commit und Kandidatenband.

        Returns:
            Je losem Knoten die Kandidaten des mittleren Bandes — die Eingabe von Stufe 2.
        """
        route = self._router.describe(defaults.TASK_EMBEDDING)
        auto = parameter.proximity_auto_commit or 1.0
        band = parameter.proximity_candidate_band or 0.0
        top_n = parameter.proximity_top_n or 1
        kandidaten: dict[str, tuple[tuple[str, float], ...]] = {}
        jetzt = self._clock()

        for knoten in lose:
            with self._unit_of_work(store) as uow:
                eigen = uow.embeddings.get(concept_id=knoten.id, model_key=route.model_key)
                if eigen is None:
                    continue
                treffer = uow.embeddings.search(
                    vector=eigen,
                    model_key=route.model_key,
                    limit=top_n,
                    scope=parameter.scope,
                    exclude=(knoten.id,),
                )

            offen: list[tuple[str, float]] = []
            for hit in treffer:
                if hit.similarity >= auto:
                    self._nahe_kante(
                        knoten=knoten.id,
                        ziel=hit.concept_id,
                        similarity=hit.similarity,
                        store=store,
                        run_id=run_id,
                        actor=actor,
                        jetzt=jetzt,
                        bericht=bericht,
                    )
                elif hit.similarity >= band:
                    offen.append((hit.concept_id, hit.similarity))
            if offen:
                kandidaten[knoten.id] = tuple(offen)
                bericht.proximity_candidates += len(offen)
        return kandidaten

    def _nahe_kante(
        self,
        *,
        knoten: str,
        ziel: str,
        similarity: float,
        store: str,
        run_id: UUID | None,
        actor: str,
        jetzt: datetime,
        bericht: OrphanReport,
    ) -> None:
        """Schreibt eine ``related``-Kante aus gemessener Nähe (§15.2b, Auto-Commit).

        ``generated_by: 'code:embedding-proximity'`` und keine Modellkennung: Die Aussage ist eine
        Messung, kein Urteil. Wer die Kante später beurteilen will, kann sie nachrechnen.
        """
        if bericht.dry_run:
            bericht.proximity_committed += 1
            return
        with self._unit_of_work(store) as uow:
            # Ein verworfenes Tripel entsteht nicht neu (§16.2, §24) — auch dann nicht, wenn die
            # gemessene Nähe unverändert hoch ist. Die Messung war nie strittig; das Urteil
            # darüber, ob sie eine Beziehung bedeutet, hat ein Mensch gefällt.
            if defaults.EDGE_KIND_RELATED in uow.edges.rejected_kinds(from_id=knoten, to_id=ziel):
                return
            edge = uow.edges.add(
                EdgeDraft(
                    from_store=store,
                    from_id=knoten,
                    to_store=store,
                    to_id=ziel,
                    kind=defaults.EDGE_KIND_RELATED,
                    weight=similarity,
                    resolved=True,
                    generated_by=defaults.GENERATED_BY_PROXIMITY,
                    generated_at=jetzt,
                )
            )
            if edge is None:
                return
            bericht.proximity_committed += 1
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED,
                    concept_id=knoten,
                    edge_id=edge.id,
                    actor=actor,
                    run_id=run_id,
                    detail={"kind": edge.kind, "to_id": ziel, "similarity": similarity},
                )
            )

    # -- Stufe 2 ----------------------------------------------------------------

    def _stufe_zwei(
        self,
        *,
        kandidaten: Mapping[str, tuple[tuple[str, float], ...]],
        parameter: OrphanRequest,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: OrphanReport,
    ) -> None:
        """§15.3: Cluster-Vorschlag und Paarprüfung — nur für weiterhin lose Knoten."""
        schwelle = parameter.loose_threshold or 0
        weiterhin = {
            knoten.id: knoten
            for knoten in self._lose(scope=parameter.scope, store=store, threshold=schwelle)
        }
        if not weiterhin:
            return

        uebersicht = self._clusteruebersicht(
            scope=parameter.scope, store=store, mitglieder=parameter.cluster_preview_members or 1
        )
        paare: list[tuple[str, str, float]] = []

        for knoten_id in sorted(weiterhin):
            vorschlag = self._cluster_vorschlagen(
                knoten_id=knoten_id,
                uebersicht=uebersicht,
                parameter=parameter,
                store=store,
                run_id=run_id,
                bericht=bericht,
            )
            if vorschlag is not None:
                paare.extend(
                    self._vorschlag_verarbeiten(
                        knoten_id=knoten_id,
                        vorschlag=vorschlag,
                        uebersicht=uebersicht,
                        parameter=parameter,
                        store=store,
                        run_id=run_id,
                        actor=actor,
                        bericht=bericht,
                    )
                )
            paare.extend(
                (knoten_id, ziel, aehnlichkeit)
                for ziel, aehnlichkeit in kandidaten.get(knoten_id, ())
            )

        if not paare:
            return
        # Aufruf B, im identischen Format zu §14.3 — dieselbe Methode, nicht ein zweiter Prompt.
        teil = RelationReport(scope=parameter.scope, store=store)
        self._relations.check_pairs(
            paare, store=store, run_id=run_id, actor=actor, dry_run=bericht.dry_run, bericht=teil
        )
        bericht.model_edges += teil.edges_written
        bericht.calls += teil.calls
        bericht.errors = (*bericht.errors, *teil.errors)
        bericht.budget_exceeded = bericht.budget_exceeded or teil.budget_exceeded

    def _clusteruebersicht(
        self, *, scope: str, store: str, mitglieder: int
    ) -> tuple[dict[str, Any], ...]:
        """Die Cluster-Übersicht aus §15.3, inklusive Mitgliedstiteln je Cluster."""
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.in_scope(scope, concept_type=defaults.CONCEPT_TYPE_CLUSTER)
            uebersicht: list[dict[str, Any]] = []
            for item in cluster:
                kanten = [
                    edge
                    for edge in uow.edges.list_outgoing(item.id)
                    if edge.kind == defaults.EDGE_KIND_MEMBER
                ]
                kanten.sort(key=lambda edge: (-(edge.weight or 0.0), edge.to_id))
                namen = uow.concepts.get_many([edge.to_id for edge in kanten[:mitglieder]])
                uebersicht.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "description": item.description,
                        "members": [concept.title or concept.id for concept in namen],
                        "_zentral": [edge.to_id for edge in kanten],
                    }
                )
        return tuple(uebersicht)

    def _cluster_vorschlagen(
        self,
        *,
        knoten_id: str,
        uebersicht: Sequence[Mapping[str, Any]],
        parameter: OrphanRequest,
        store: str,
        run_id: UUID | None,
        bericht: OrphanReport,
    ) -> ClusterSuggestion | None:
        """Aufruf A aus §15.3 — der lose Knoten plus die Cluster-Übersicht, sonst nichts."""
        with self._unit_of_work(store) as uow:
            concept = uow.concepts.get(knoten_id)
        if concept is None:
            return None

        eingabe = {
            "concept": {"title": concept.title, "description": concept.description},
            "clusters": [
                {key: wert for key, wert in item.items() if not key.startswith("_")}
                for item in uebersicht
            ],
        }
        try:
            antwort = self._router.complete(
                defaults.TASK_CLUSTER_MATCHING,
                prompt=PromptSpec(
                    system=_ZUORDNUNG_SYSTEM, user=json.dumps(eingabe, ensure_ascii=False)
                ),
                schema=ClusterSuggestion,
                store=store,
                run_id=run_id,
            )
        except (ProviderNotAllowedError, BudgetExceededError):
            raise
        except ModelError as exc:
            bericht.errors = (*bericht.errors, f"{knoten_id}: {type(exc).__name__}")
            return None

        bericht.calls += 1
        geparst = antwort.parsed
        if not isinstance(geparst, ClusterSuggestion):
            return None
        if geparst.confidence < (parameter.min_confidence or 0.0):
            return None
        return geparst

    def _vorschlag_verarbeiten(
        self,
        *,
        knoten_id: str,
        vorschlag: ClusterSuggestion,
        uebersicht: Sequence[Mapping[str, Any]],
        parameter: OrphanRequest,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: OrphanReport,
    ) -> list[tuple[str, str, float]]:
        """Legt gegebenenfalls ein neues Cluster an und liefert die Paare für Aufruf B."""
        if vorschlag.propose_new_cluster is not None:
            self._neues_cluster(
                knoten_id=knoten_id,
                vorschlag=vorschlag.propose_new_cluster,
                scope=parameter.scope,
                store=store,
                run_id=run_id,
                actor=actor,
                bericht=bericht,
            )
            return []

        deckel = parameter.cluster_suggestion_limit or 0
        gewaehlt = list(vorschlag.suggested_cluster_ids)[:deckel]
        if not gewaehlt:
            return []
        bericht.cluster_suggestions += len(gewaehlt)

        zentral = {item["id"]: item["_zentral"] for item in uebersicht}
        anzahl = self._settings.relations.cross_cluster_members
        return [
            (knoten_id, ziel, 0.0)
            for cluster_id in gewaehlt
            for ziel in zentral.get(cluster_id, ())[:anzahl]
            if ziel != knoten_id
        ]

    def _neues_cluster(
        self,
        *,
        knoten_id: str,
        vorschlag: NewClusterProposal,
        scope: str,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: OrphanReport,
    ) -> None:
        """§15.3: ein neues Cluster mit Provenienz, ``verified = false``, plus ``member``-Kante.

        Ab dann ist es ein gewöhnliches Cluster: Es bekommt beim nächsten Clustering-Lauf einen
        Zentroid und ``related``-Kanten und kann bei einer vollständigen Neu-Clusterung mit einem
        inzwischen passenderen Cluster verschmelzen. So organisiert sich die Struktur über die
        Zeit selbst, statt starr zu bleiben.
        """
        bericht.clusters_created += 1
        if bericht.dry_run:
            return

        route = self._router.describe(defaults.TASK_CLUSTER_MATCHING)
        jetzt = self._clock()
        cluster_id = self._new_id()
        with self._unit_of_work(store) as uow:
            uow.concepts.save(
                Concept(
                    id=cluster_id,
                    store=store,
                    scope=scope,
                    type=defaults.CONCEPT_TYPE_CLUSTER,
                    title=vorschlag.title,
                    description=vorschlag.description,
                    content_hash=content_hash(
                        title=vorschlag.title, description=vorschlag.description, body=None
                    ),
                    generated_by=route.generated_by,
                    generated_at=jetzt,
                    created_at=jetzt,
                    updated_at=jetzt,
                )
            )
            edge = uow.edges.add(
                EdgeDraft(
                    from_store=store,
                    from_id=cluster_id,
                    to_store=store,
                    to_id=knoten_id,
                    kind=defaults.EDGE_KIND_MEMBER,
                    resolved=True,
                    generated_by=defaults.GENERATED_BY_CLUSTERING,
                    generated_at=jetzt,
                )
            )
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CREATED,
                    concept_id=cluster_id,
                    actor=actor,
                    run_id=run_id,
                    detail={"grund": "orphan-vorschlag", "member": knoten_id},
                )
            )
            if edge is not None:
                uow.changes.append(
                    ChangeEntry(
                        change_type=ChangeType.CLUSTER_ASSIGNED,
                        concept_id=knoten_id,
                        edge_id=edge.id,
                        actor=actor,
                        run_id=run_id,
                        detail={"cluster_id": cluster_id, "grund": "orphan-vorschlag"},
                    )
                )


def _treffer(concept: Concept, muster: Sequence[re.Pattern[str]]) -> set[str]:
    """Alle Musterfunde in Beschreibung und Fließtext eines Konzepts (§15.2a)."""
    text = " ".join(teil for teil in (concept.description, concept.body) if teil)
    if not text:
        return set()
    gefunden: set[str] = set()
    for pattern in muster:
        gefunden.update(match.group(0) for match in pattern.finditer(text))
    return gefunden


def _oder(wert: Any, vorgabe: Any) -> Any:
    """Der übergebene Wert, sonst die Vorgabe aus der Konfiguration (§6.2)."""
    return vorgabe if wert is None else wert


__all__ = [
    "ClusterSuggestion",
    "NewClusterProposal",
    "OrphanReport",
    "OrphanRequest",
    "OrphanService",
]
