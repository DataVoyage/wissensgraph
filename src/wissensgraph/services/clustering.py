"""Cluster-Bildung (§13.2), Stabilitätsschwelle (§13.3) und Kurationsschutz (§13.4).

Der Ablauf folgt §13.2 Schritt für Schritt: k-nächste Nachbarn, Kantenschwelle,
Zusammenhangskomponenten, Größenfilter, ein ``Cluster``-Konzept je Komponente, Zentroid,
``related``-Kanten zwischen Zentroiden.

Drei Dinge daran sind mehr als Umsetzung:

**Ein Cluster ist ein Konzept, keine Tabelle.** Es hat einen Titel, eine Beschreibung, Kanten und
einen Status wie jedes andere — und ist damit selbst traversierbar, kuratierbar und auffindbar.
Deshalb entsteht hier kein neues Schema; es entstehen Konzepte und Kanten.

**Mitgliedschaft wird nicht sofort geschrieben.** §13.3: Eine Zuordnung landet erst in
``cluster_assignment_candidates`` und wird geschrieben, wenn sie ``stability_runs`` Läufe
überlebt. Das verhindert das Flattern bei knappen Ähnlichkeiten — und macht die Bedingung
auswertbar, statt sie nur zu behaupten.

**Der Algorithmus darf nicht recht behalten.** §13.4 zählt fünf Fälle auf, in denen Handarbeit
gewinnt. Sie sind hier nicht als Sonderbehandlung eingestreut, sondern liegen an einer Stelle:
:meth:`ClusterService._mitglieder_abgleichen` schreibt ausschließlich Kanten mit der Kennung
``code:clustering``, und ``replace_generated`` rührt kuratierte grundsätzlich nicht an (§10.4).
Ein von Hand entferntes Mitglied kommt über den Ausschlussvermerk gar nicht erst wieder in die
Auswahl.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import EdgeDraft
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import BudgetExceededError, ModelError, ModelRouter, PromptSpec
from wissensgraph.ports.repositories import Neighbour, UnitOfWork, UnitOfWorkFactory

_log = get_logger(__name__)

#: Der Prompt der Cluster-Betitelung (§13.2 Schritt 4). Er bekommt Mitgliedstitel und sonst
#: nichts: Das Modell soll benennen, was diese Titel gemeinsam haben — nicht den Graphen deuten.
_TITEL_SYSTEM = (
    "Du benennst Themengruppen in einem Wissensgraphen. Du bekommst die Titel der Mitglieder "
    "einer Gruppe. Antworte als JSON-Objekt mit den Feldern 'title' (höchstens fünf Wörter, "
    "keine Aufzählung, in der Sprache der Titel) und 'description' (ein Satz). Benenne das "
    "gemeinsame Thema, nicht die Liste."
)

#: Wie viele Mitgliedstitel höchstens in den Prompt gehen. Mehr verbessert den Titel nicht und
#: kostet linear mehr Token.
_TITEL_MITGLIEDER = 15


class ClusterLabel(BaseModel):
    """Die Antwort der Betitelung (§13.2 Schritt 4)."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


@dataclass
class ClusterReport:
    """Was ein Clustering-Lauf getan hat — die Zähler für ``runs.stats`` (§7.4)."""

    scope: str
    store: str
    model_key: str = ""
    embedded: int = 0
    components: int = 0
    too_small: int = 0
    split: int = 0
    clusters_created: int = 0
    clusters_matched: int = 0
    members_added: int = 0
    members_removed: int = 0
    candidates: int = 0
    expired: int = 0
    related_edges: int = 0
    labeled: int = 0
    relabel_proposed: int = 0
    excluded: int = 0
    budget_exceeded: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für Lauf-Statistik, CLI und API — nur Zahlen und Namen (§21.1)."""
        return {
            "scope": self.scope,
            "store": self.store,
            "model_key": self.model_key,
            "embedded": self.embedded,
            "components": self.components,
            "too_small": self.too_small,
            "split": self.split,
            "clusters_created": self.clusters_created,
            "clusters_matched": self.clusters_matched,
            "members_added": self.members_added,
            "members_removed": self.members_removed,
            "candidates": self.candidates,
            "expired": self.expired,
            "related_edges": self.related_edges,
            "labeled": self.labeled,
            "relabel_proposed": self.relabel_proposed,
            "excluded": self.excluded,
            "budget_exceeded": self.budget_exceeded,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class _Komponente:
    """Eine Zusammenhangskomponente des k-NN-Graphen, bevor sie ein Cluster wird."""

    members: tuple[str, ...]
    scores: Mapping[str, float]


class ClusterService:
    """Bildet Cluster, betitelt sie und hält ihre Mitgliedschaft stabil (§13.2 bis §13.4)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        router: ModelRouter,
        *,
        clock: Callable[[], datetime] | None = None,
        new_cluster_id: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration; liefert die Parameter aus §13.
            unit_of_work: Fabrik für Transaktionen je Store.
            router: Der Model-Router — für die Betitelung, nicht für die Gruppierung.
            clock: Zeitquelle.
            new_cluster_id: Erzeugt die ID eines neuen Clusters. Als Parameter, damit ein Test
                nachvollziehbare IDs bekommt; im Betrieb eine Zufalls-ID. Eine aus den Mitgliedern
                abgeleitete ID wäre verlockend und falsch: Sie änderte sich mit jeder
                Mitgliederänderung, und damit wäre kein Cluster über zwei Läufe hinweg dasselbe.
        """
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._router = router
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_cluster_id or (
            lambda: f"{defaults.ID_PREFIX_CLUSTER}:{uuid4().hex[:12]}"
        )

    # -- öffentliche Operationen ------------------------------------------------

    def run(
        self,
        *,
        scope: str,
        run_id: UUID | None = None,
        actor: str = defaults.ACTOR_CLUSTER,
    ) -> ClusterReport:
        """Führt einen Clustering-Lauf über einen Scope aus (§13.2).

        Returns:
            Den Bericht. Ein Lauf ohne Embeddings ist erfolgreich und tut nichts — die
            semantische Schicht ist eine Voraussetzung, kein Versprechen (§11.5).
        """
        store = self._settings.store_of_scope(scope)
        route = self._router.describe(defaults.TASK_EMBEDDING)
        bericht = ClusterReport(scope=scope, store=store, model_key=route.model_key)
        lauf = run_id or uuid4()

        komponenten = self._komponenten(
            scope=scope, store=store, model_key=route.model_key, bericht=bericht
        )
        if not komponenten:
            _log.info("cluster.nichts_zu_tun", scope=scope, embedded=bericht.embedded)
            return bericht

        bestehende = self._bestehende_cluster(scope=scope, store=store)
        vergeben: set[str] = set()

        try:
            for komponente in komponenten:
                cluster_id = self._zuordnen(komponente, bestehende, vergeben)
                vergeben.add(cluster_id)
                self._cluster_schreiben(
                    komponente=komponente,
                    cluster_id=cluster_id,
                    neu=cluster_id not in bestehende,
                    scope=scope,
                    store=store,
                    model_key=route.model_key,
                    run_id=lauf,
                    actor=actor,
                    bericht=bericht,
                )
        except BudgetExceededError as exc:
            bericht.budget_exceeded = True
            _log.warning("cluster.budget_erschoepft", scope=scope, grund=str(exc))

        self._verwandte_cluster(
            store=store, model_key=route.model_key, run_id=lauf, actor=actor, bericht=bericht
        )
        with self._unit_of_work(store) as uow:
            bericht.expired = uow.clusters.expire(run_id=lauf)

        _log.info("cluster.beendet", **bericht.as_dict())
        return bericht

    def exclude_member(
        self,
        *,
        concept_id: str,
        cluster_id: str,
        store: str,
        actor: str = defaults.ACTOR_CLI,
        run_id: UUID | None = None,
    ) -> bool:
        """Entfernt eine Mitgliedschaft von Hand und sperrt sie dauerhaft (§13.4).

        Zwei Schritte, die zusammengehören: Die Kante geht, und der Ausschluss bleibt. Ohne den
        zweiten fände der nächste Lauf dieselbe Nähe wieder und schriebe dieselbe Zuordnung — die
        Handarbeit wäre nach einem Lauf verschwunden (Leitprinzip 15).

        Returns:
            Ob es die Mitgliedschaft überhaupt gab. Der Ausschluss wird in beiden Fällen vermerkt:
            Man kann etwas verbieten, das gerade nicht der Fall ist.
        """
        with self._unit_of_work(store) as uow:
            vorhanden = [
                edge
                for edge in uow.edges.list_outgoing(cluster_id)
                if edge.kind == defaults.EDGE_KIND_MEMBER and edge.to_id == concept_id
            ]
            behalten = [
                EdgeDraft(**edge.model_dump(exclude={"id", "created_at"}))
                for edge in uow.edges.list_outgoing(cluster_id)
                if not (edge.kind == defaults.EDGE_KIND_MEMBER and edge.to_id == concept_id)
                and edge.generated_by == defaults.GENERATED_BY_CLUSTERING
            ]
            uow.edges.replace_generated(
                from_id=cluster_id,
                generated_by=(defaults.GENERATED_BY_CLUSTERING,),
                drafts=behalten,
            )
            uow.clusters.exclude(concept_id=concept_id, cluster_id=cluster_id)
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CLUSTER_REMOVED,
                    concept_id=concept_id,
                    actor=actor,
                    run_id=run_id,
                    detail={"cluster_id": cluster_id, "gesperrt": True},
                )
            )
        return bool(vorhanden)

    # -- Gruppierung ------------------------------------------------------------

    def _komponenten(
        self, *, scope: str, store: str, model_key: str, bericht: ClusterReport
    ) -> tuple[_Komponente, ...]:
        """Schritte 1 bis 3 aus §13.2: Nachbarn, Kantenschwelle, Zusammenhangskomponenten."""
        parameter = self._settings.clustering
        with self._unit_of_work(store) as uow:
            kandidaten = [
                concept.id
                for concept in uow.concepts.in_scope(scope)
                if concept.type != defaults.CONCEPT_TYPE_CLUSTER
            ]
            nachbarn: dict[str, tuple[Neighbour, ...]] = {}
            for concept_id in kandidaten:
                treffer = uow.embeddings.neighbours(
                    concept_id=concept_id,
                    model_key=model_key,
                    k=parameter.neighbors_k,
                    scope=scope,
                    min_similarity=parameter.min_similarity,
                )
                if treffer:
                    nachbarn[concept_id] = treffer
            bericht.embedded = uow.embeddings.count(model_key=model_key, scope=scope)

        # Cluster-Konzepte selbst sind keine Mitglieder: Ein Cluster im Cluster wäre die
        # Hierarchie, die §24 für diese Stufe ausdrücklich ausnimmt.
        erlaubt = frozenset(kandidaten)
        kanten = [
            (links, hit.concept_id, hit.similarity)
            for links, treffer in nachbarn.items()
            for hit in treffer
            if hit.concept_id in erlaubt
        ]

        roh = _zusammenhangskomponenten(erlaubt, kanten)
        ergebnis: list[_Komponente] = []
        for gruppe in roh:
            for teil in self._auf_groesse_bringen(gruppe, kanten, bericht):
                if len(teil) < parameter.min_cluster_size:
                    bericht.too_small += 1
                    continue
                ergebnis.append(
                    _Komponente(
                        members=tuple(sorted(teil)),
                        scores=_mittlere_aehnlichkeit(teil, kanten),
                    )
                )
        bericht.components = len(ergebnis)
        return tuple(ergebnis)

    def _auf_groesse_bringen(
        self,
        gruppe: set[str],
        kanten: Sequence[tuple[str, str, float]],
        bericht: ClusterReport,
    ) -> list[set[str]]:
        """Teilt eine zu große Komponente rekursiv (§13.2 Schritt 3).

        Geteilt wird über eine *höhere* Kantenschwelle und nicht über einen Schnitt nach Größe:
        Ein Cluster ist eine inhaltliche Aussage, und eine Zerlegung nach Anzahl wäre keine. Erst
        wenn auch die höchste Schwelle die Gruppe nicht zerlegt — alle Mitglieder sind einander
        gleich ähnlich —, wird nach ID aufgeteilt. Das ist willkürlich, aber es ist sichtbar
        willkürlich und tritt in echten Beständen kaum auf.
        """
        deckel = self._settings.clustering.max_cluster_size
        if len(gruppe) <= deckel:
            return [gruppe]

        schwelle = self._settings.clustering.min_similarity
        for _ in range(10):
            schwelle = schwelle + (1.0 - schwelle) / 4
            enger = [kante for kante in kanten if kante[2] >= schwelle]
            teile = _zusammenhangskomponenten(frozenset(gruppe), enger)
            if len(teile) > 1:
                bericht.split += 1
                ergebnis: list[set[str]] = []
                for teil in teile:
                    ergebnis.extend(self._auf_groesse_bringen(teil, enger, bericht))
                return ergebnis

        bericht.split += 1
        sortiert = sorted(gruppe)
        return [set(sortiert[i : i + deckel]) for i in range(0, len(sortiert), deckel)]

    # -- Zuordnung zu bestehenden Clustern --------------------------------------

    def _bestehende_cluster(
        self, *, scope: str, store: str
    ) -> dict[str, tuple[Concept, frozenset[str]]]:
        """Die vorhandenen Cluster des Scopes mit allem, was ihnen zugerechnet wird.

        Zugerechnet heißt: geschriebene ``member``-Kanten **und** vorgemerkte Kandidaten. Das ist
        keine Bequemlichkeit, sondern die Bedingung dafür, dass §13.3 überhaupt funktioniert: In
        den Läufen bis zum Erreichen der Stabilitätsschwelle hat ein Cluster noch keine einzige
        Kante. Zählte nur sie, fände der zweite Lauf keine Überschneidung, legte ein zweites
        Cluster an — und die Schwelle wäre nie zu erreichen, weil jeder Lauf von vorn begänne.
        """
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.in_scope(scope, concept_type=defaults.CONCEPT_TYPE_CLUSTER)
            vorgemerkt: dict[str, set[str]] = {}
            for kandidat in uow.clusters.candidates():
                if not kandidat.excluded:
                    vorgemerkt.setdefault(kandidat.cluster_id, set()).add(kandidat.concept_id)
            return {
                item.id: (
                    item,
                    frozenset(
                        {
                            edge.to_id
                            for edge in uow.edges.list_outgoing(item.id)
                            if edge.kind == defaults.EDGE_KIND_MEMBER
                        }
                        | vorgemerkt.get(item.id, set())
                    ),
                )
                for item in cluster
            }

    def _zuordnen(
        self,
        komponente: _Komponente,
        bestehende: Mapping[str, tuple[Concept, frozenset[str]]],
        vergeben: set[str],
    ) -> str:
        """Sucht das Cluster, das diese Komponente fortsetzt — oder vergibt eine neue ID.

        Zugeordnet wird über die größte Überschneidung der Mitglieder. Das ist die einzige
        Verbindung, die es zwischen zwei Läufen gibt: Ein Cluster hat keine inhaltliche Identität
        außer der Menge dessen, was es zusammenfasst. Ohne diese Zuordnung bekäme jede
        Mitgliederänderung ein neues Cluster, und weder Titel noch Kuration noch die
        Stabilitätsschwelle überlebten einen einzigen Lauf.
        """
        mitglieder = frozenset(komponente.members)
        bester: tuple[int, str] | None = None
        for cluster_id, (_, vorhandene) in bestehende.items():
            if cluster_id in vergeben:
                continue
            ueberschneidung = len(mitglieder & vorhandene)
            if ueberschneidung == 0:
                continue
            if bester is None or ueberschneidung > bester[0]:
                bester = (ueberschneidung, cluster_id)
        return self._new_id() if bester is None else bester[1]

    # -- Schreiben --------------------------------------------------------------

    def _cluster_schreiben(
        self,
        *,
        komponente: _Komponente,
        cluster_id: str,
        neu: bool,
        scope: str,
        store: str,
        model_key: str,
        run_id: UUID,
        actor: str,
        bericht: ClusterReport,
    ) -> None:
        """Legt das Cluster-Konzept an oder schreibt es fort, samt Mitgliedern und Zentroid."""
        with self._unit_of_work(store) as uow:
            mitglieder = uow.concepts.get_many(komponente.members)
            vorhanden = None if neu else uow.concepts.get(cluster_id)

        titel = self._betiteln(
            cluster_id=cluster_id,
            vorhanden=vorhanden,
            mitglieder=mitglieder,
            store=store,
            run_id=run_id,
            actor=actor,
            bericht=bericht,
        )

        jetzt = self._clock()
        route = self._router.describe(defaults.TASK_CLUSTER_LABELING)
        with self._unit_of_work(store) as uow:
            if vorhanden is None:
                concept = Concept(
                    id=cluster_id,
                    store=store,
                    scope=scope,
                    type=defaults.CONCEPT_TYPE_CLUSTER,
                    title=titel.title,
                    description=titel.description,
                    content_hash=_titel_hash(titel),
                    generated_by=route.generated_by,
                    generated_at=jetzt,
                    created_at=jetzt,
                    updated_at=jetzt,
                )
                uow.concepts.save(concept)
                uow.changes.append(
                    ChangeEntry(
                        change_type=ChangeType.CREATED,
                        concept_id=cluster_id,
                        actor=actor,
                        run_id=run_id,
                        detail={"members": len(komponente.members)},
                    )
                )
                bericht.clusters_created += 1
            else:
                bericht.clusters_matched += 1

            self._mitglieder_abgleichen(
                uow,
                cluster_id=cluster_id,
                komponente=komponente,
                store=store,
                run_id=run_id,
                actor=actor,
                bericht=bericht,
            )

        self._zentroid_schreiben(
            cluster_id=cluster_id,
            komponente=komponente,
            store=store,
            model_key=model_key,
        )

    def _mitglieder_abgleichen(
        self,
        uow: UnitOfWork,
        *,
        cluster_id: str,
        komponente: _Komponente,
        store: str,
        run_id: UUID,
        actor: str,
        bericht: ClusterReport,
    ) -> None:
        """Schreibt die Mitgliedschaft — aber erst ab der Stabilitätsschwelle (§13.3, §13.4)."""
        schwelle = self._settings.clustering.stability_runs
        gesperrt = uow.clusters.exclusions()
        bestehend = {
            edge.to_id
            for edge in uow.edges.list_outgoing(cluster_id)
            if edge.kind == defaults.EDGE_KIND_MEMBER
        }

        bestaetigt: list[str] = []
        for member in komponente.members:
            if (member, cluster_id) in gesperrt:
                bericht.excluded += 1
                continue
            gesehen = uow.clusters.bump(
                concept_id=member,
                cluster_id=cluster_id,
                score=komponente.scores.get(member, 0.0),
                run_id=run_id,
            )
            if member in bestehend or gesehen >= schwelle:
                bestaetigt.append(member)
            else:
                bericht.candidates += 1

        jetzt = self._clock()
        drafts = [
            EdgeDraft(
                from_store=store,
                from_id=cluster_id,
                to_store=store,
                to_id=member,
                kind=defaults.EDGE_KIND_MEMBER,
                weight=komponente.scores.get(member),
                resolved=True,
                generated_by=defaults.GENERATED_BY_CLUSTERING,
                generated_at=jetzt,
            )
            for member in bestaetigt
        ]
        hinzugefuegt, entfernt = uow.edges.replace_generated(
            from_id=cluster_id,
            generated_by=(defaults.GENERATED_BY_CLUSTERING,),
            drafts=drafts,
        )

        for edge in hinzugefuegt:
            bericht.members_added += 1
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CLUSTER_ASSIGNED,
                    concept_id=edge.to_id,
                    edge_id=edge.id,
                    actor=actor,
                    run_id=run_id,
                    detail={"cluster_id": cluster_id, "weight": edge.weight},
                )
            )
        for edge in entfernt:
            bericht.members_removed += 1
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CLUSTER_REMOVED,
                    concept_id=edge.to_id,
                    actor=actor,
                    run_id=run_id,
                    detail={"cluster_id": cluster_id, "gesperrt": False},
                )
            )

    def _zentroid_schreiben(
        self, *, cluster_id: str, komponente: _Komponente, store: str, model_key: str
    ) -> None:
        """Der Mittelwert der Mitgliedsvektoren (§13.2 Schritt 5)."""
        with self._unit_of_work(store) as uow:
            vektoren = [
                vektor
                for member in komponente.members
                if (vektor := uow.embeddings.get(concept_id=member, model_key=model_key))
                is not None
            ]
            if not vektoren:
                return
            uow.clusters.save_centroid(
                cluster_id=cluster_id,
                model_key=model_key,
                vector=_mittelwert(vektoren),
                member_count=len(komponente.members),
            )

    def _verwandte_cluster(
        self,
        *,
        store: str,
        model_key: str,
        run_id: UUID,
        actor: str,
        bericht: ClusterReport,
    ) -> None:
        """``related``-Kanten zu den ähnlichsten Zentroiden (§13.2 Schritt 6).

        Die Kanten gehen in beide Richtungen als zwei Zeilen, weil ``edges`` gerichtet ist und
        ``ux_edges_triple`` sonst die Gegenrichtung als eigenes Tripel führte. Sie tragen die
        gemessene Ähnlichkeit als ``weight`` — eine Zahl, die sich nachrechnen lässt.
        """
        top_n = self._settings.clustering.related_cluster_top_n
        if top_n < 1:
            return

        jetzt = self._clock()
        with self._unit_of_work(store) as uow:
            zentroide = uow.clusters.centroids(model_key=model_key)
            for zentroid in zentroide:
                aehnliche = uow.clusters.similar_centroids(
                    cluster_id=zentroid.cluster_id, model_key=model_key, limit=top_n
                )
                drafts = [
                    EdgeDraft(
                        from_store=store,
                        from_id=zentroid.cluster_id,
                        to_store=store,
                        to_id=hit.concept_id,
                        kind=defaults.EDGE_KIND_RELATED,
                        weight=hit.similarity,
                        resolved=True,
                        generated_by=defaults.GENERATED_BY_CLUSTER_SIMILARITY,
                        generated_at=jetzt,
                    )
                    for hit in aehnliche
                ]
                hinzugefuegt, _ = uow.edges.replace_generated(
                    from_id=zentroid.cluster_id,
                    generated_by=(defaults.GENERATED_BY_CLUSTER_SIMILARITY,),
                    drafts=drafts,
                )
                for edge in hinzugefuegt:
                    bericht.related_edges += 1
                    uow.changes.append(
                        ChangeEntry(
                            change_type=ChangeType.EDGE_ADDED,
                            concept_id=zentroid.cluster_id,
                            edge_id=edge.id,
                            actor=actor,
                            run_id=run_id,
                            detail={"kind": edge.kind, "to_id": edge.to_id},
                        )
                    )

    # -- Betitelung -------------------------------------------------------------

    def _betiteln(
        self,
        *,
        cluster_id: str,
        vorhanden: Concept | None,
        mitglieder: Sequence[Concept],
        store: str,
        run_id: UUID,
        actor: str,
        bericht: ClusterReport,
    ) -> ClusterLabel:
        """Bestimmt Titel und Beschreibung eines Clusters (§13.2 Schritt 4, §13.4).

        Drei Fälle, und §13.4 legt alle drei fest:

        * Ein neues Cluster wird betitelt.
        * Ein von Hand umbenanntes Cluster (``curated``) wird **nie** überschrieben.
        * Hat sich der Mitgliederbestand stark geändert, wird eine Neubetitelung *vorgeschlagen*
          und nicht angewandt — sie erscheint als Aufgabe in der UI (§17.2).
        """
        if vorhanden is not None:
            if vorhanden.curated:
                return ClusterLabel(
                    title=vorhanden.title or cluster_id, description=vorhanden.description
                )
            self._neubetitelung_pruefen(
                cluster_id=cluster_id,
                vorhanden=vorhanden,
                mitglieder=mitglieder,
                store=store,
                run_id=run_id,
                actor=actor,
                bericht=bericht,
            )
            return ClusterLabel(
                title=vorhanden.title or cluster_id, description=vorhanden.description
            )

        titel = self._modelltitel(mitglieder, store=store, run_id=run_id, bericht=bericht)
        if titel is not None:
            bericht.labeled += 1
            return titel
        return _ersatztitel(mitglieder)

    def _modelltitel(
        self,
        mitglieder: Sequence[Concept],
        *,
        store: str,
        run_id: UUID,
        bericht: ClusterReport,
    ) -> ClusterLabel | None:
        """Ruft ``cluster_labeling`` auf; ``None``, wenn das Modell nicht liefert.

        Ein misslungener Titel bricht den Lauf nicht ab. Ein Cluster ohne guten Namen ist immer
        noch ein Cluster — die Gruppierung selbst kommt aus dem Code und nicht aus dem Modell.
        """
        titel = [concept.title or concept.id for concept in mitglieder][:_TITEL_MITGLIEDER]
        try:
            antwort = self._router.complete(
                defaults.TASK_CLUSTER_LABELING,
                prompt=PromptSpec(
                    system=_TITEL_SYSTEM,
                    user=json.dumps({"member_titles": titel}, ensure_ascii=False),
                ),
                schema=ClusterLabel,
                store=store,
                run_id=run_id,
            )
        except (ProviderNotAllowedError, BudgetExceededError):
            raise
        except ModelError as exc:
            bericht.errors = (*bericht.errors, f"cluster_labeling: {type(exc).__name__}")
            return None
        return antwort.parsed if isinstance(antwort.parsed, ClusterLabel) else None

    def _neubetitelung_pruefen(
        self,
        *,
        cluster_id: str,
        vorhanden: Concept,
        mitglieder: Sequence[Concept],
        store: str,
        run_id: UUID,
        actor: str,
        bericht: ClusterReport,
    ) -> None:
        """Schlägt eine Neubetitelung vor, wenn sich der Bestand stark geändert hat (§13.4).

        Vorgeschlagen, nicht angewandt: Ein Titel, den jemand kennt und in einer Besprechung
        genannt hat, soll nicht über Nacht ein anderer sein.
        """
        grenze = self._settings.clustering.relabel_on_member_change_pct
        with self._unit_of_work(store) as uow:
            alt = {
                edge.to_id
                for edge in uow.edges.list_outgoing(cluster_id)
                if edge.kind == defaults.EDGE_KIND_MEMBER
            }
        neu = {concept.id for concept in mitglieder}
        if not alt:
            return
        veraendert = len(alt ^ neu) * 100 // max(len(alt), 1)
        if veraendert < grenze:
            return

        with self._unit_of_work(store) as uow:
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.STATUS_CHANGED,
                    concept_id=cluster_id,
                    actor=actor,
                    run_id=run_id,
                    detail={
                        "vorschlag": "relabel",
                        "member_change_pct": veraendert,
                        "aktueller_titel": vorhanden.title,
                    },
                )
            )
        bericht.relabel_proposed += 1


# ---------------------------------------------------------------------------
# Reine Rechenschritte — ohne Datenbank und ohne Modell
# ---------------------------------------------------------------------------


def _zusammenhangskomponenten(
    knoten: Iterable[str], kanten: Sequence[tuple[str, str, float]]
) -> list[set[str]]:
    """Die Zusammenhangskomponenten eines ungerichteten Graphen (§13.2 Schritt 2).

    Union-Find mit Pfadverkürzung. Die k-nächste-Nachbarn-Beziehung ist nicht symmetrisch — A kann
    B unter seinen acht Nächsten haben, ohne dass B es umgekehrt hat. Hier wird sie als
    ungerichtet behandelt: Wenn A B für nah hält, gehören beide in dieselbe Gruppe.
    """
    eltern: dict[str, str] = {name: name for name in knoten}

    def wurzel(name: str) -> str:
        while eltern[name] != name:
            eltern[name] = eltern[eltern[name]]
            name = eltern[name]
        return name

    for links, rechts, _ in kanten:
        if links not in eltern or rechts not in eltern:
            continue
        a, b = wurzel(links), wurzel(rechts)
        if a != b:
            eltern[a] = b

    gruppen: dict[str, set[str]] = {}
    for name in eltern:
        gruppen.setdefault(wurzel(name), set()).add(name)
    return sorted(gruppen.values(), key=lambda gruppe: (-len(gruppe), min(gruppe)))


def _mittlere_aehnlichkeit(
    gruppe: set[str], kanten: Sequence[tuple[str, str, float]]
) -> dict[str, float]:
    """Wie fest ein Mitglied in seiner Gruppe hängt — der ``score`` eines Kandidaten (§13.3).

    Der Mittelwert der Ähnlichkeiten zu seinen Nachbarn *innerhalb* der Gruppe. Er entscheidet
    nichts, aber er macht die Kandidatenliste lesbar: Wer knapp drinhängt, steht unten.
    """
    summe: dict[str, float] = {}
    anzahl: dict[str, int] = {}
    for links, rechts, wert in kanten:
        if links not in gruppe or rechts not in gruppe:
            continue
        for name in (links, rechts):
            summe[name] = summe.get(name, 0.0) + wert
            anzahl[name] = anzahl.get(name, 0) + 1
    return {name: summe[name] / anzahl[name] for name in summe}


def _mittelwert(vektoren: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Der komponentenweise Mittelwert — der Zentroid aus §13.2 Schritt 5."""
    anzahl = len(vektoren)
    return tuple(sum(werte) / anzahl for werte in zip(*vektoren, strict=True))


def _ersatztitel(mitglieder: Sequence[Concept]) -> ClusterLabel:
    """Ein Titel ohne Modell — die drei häufigsten Wörter der Mitgliedstitel.

    Nicht schön, aber ehrlich: Er sagt, worum es geht, und er sagt durch seine Form, dass ihn
    niemand formuliert hat. Ein Cluster ohne Titel wäre in der UI eine leere Zeile.
    """
    woerter: dict[str, int] = {}
    for concept in mitglieder:
        for wort in (concept.title or "").split():
            gestutzt = wort.strip(".,;:()[]").lower()
            if len(gestutzt) > 3:
                woerter[gestutzt] = woerter.get(gestutzt, 0) + 1
    haeufig = sorted(woerter.items(), key=lambda item: (-item[1], item[0]))[:3]
    if not haeufig:
        return ClusterLabel(title=f"Gruppe aus {len(mitglieder)} Konzepten")
    return ClusterLabel(
        title=" / ".join(wort for wort, _ in haeufig),
        description=(
            f"Automatisch gebildete Gruppe aus {len(mitglieder)} Konzepten, ohne Modell benannt."
        ),
    )


def _titel_hash(titel: ClusterLabel) -> str:
    """Der Content-Hash eines Cluster-Konzepts (§10.3).

    Er geht über Titel und Beschreibung, weil ein Cluster keinen Quellinhalt hat: Was sich bei ihm
    ändern kann, ist genau sein Name.
    """
    from wissensgraph.domain.hashing import content_hash

    return content_hash(title=titel.title, description=titel.description, body=None)


__all__ = ["ClusterLabel", "ClusterReport", "ClusterService"]
