"""Ein kleiner Korpus und die Verdrahtung der semantischen Läufe (§13 bis §15).

Der Korpus ist so gebaut, dass die Abnahmekriterien der Stufen 8 bis 10 an ihm überhaupt prüfbar
sind: drei Themenfelder mit klar getrenntem Wortschatz, ein Grenzdokument, das zwei davon berührt,
und ein isolierter Knoten, der zu keinem passt.

Das trägt, weil :class:`~wissensgraph.testing.models.FakeEmbeddings` lexikalisch arbeitet: Zwei
Texte über dasselbe Thema teilen Wörter und landen deshalb wirklich nahe beieinander. Ein Fake mit
Zufallsvektoren hätte dieselbe Struktur vorgetäuscht und nichts geprüft.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from support.memory import MemoryUnitOfWorkFactory
from wissensgraph.config import defaults
from wissensgraph.config.models import ModelsConfig
from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import Concept
from wissensgraph.ports.models import PromptSpec
from wissensgraph.services.clustering import ClusterService
from wissensgraph.services.embeddings import EmbeddingService
from wissensgraph.services.graph import GraphService
from wissensgraph.services.orphans import OrphanService
from wissensgraph.services.relations import RelationService
from wissensgraph.services.router import ModelRouterService
from wissensgraph.testing.models import FakeClients

#: Groß genug, dass sich Wörter selten denselben Eimer teilen. Bei 64 Dimensionen überlappen die
#: Kollisionen so stark, dass zwei Texte über verschiedene Themen ähnlicher aussehen können als
#: zwei über dasselbe — ein Clustering-Test prüfte dann die Hash-Kollisionen und nicht §13.2.
DIM = 512

JETZT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def models_config(*, dim: int = DIM, local: bool = False) -> ModelsConfig:
    """Eine Router-Konfiguration mit einem Anbieter für alle Aufgaben."""
    provider = {"type": "google_genai", "api_key": "test", "local": local}

    def route(**extra: Any) -> dict[str, Any]:
        return {"primary": {"provider": "p", "model": "m", **extra}}

    return ModelsConfig.model_validate(
        {
            "providers": {"p": provider},
            "tasks": {
                defaults.TASK_EMBEDDING: route(dim=dim, batch_size=8),
                defaults.TASK_CLUSTER_LABELING: route(temperature=0.2, json_mode=True),
                defaults.TASK_RELATION_EXTRACTION: route(temperature=0.0, json_mode=True),
                defaults.TASK_CLUSTER_MATCHING: route(temperature=0.0, json_mode=True),
                defaults.TASK_SUMMARIZATION: route(temperature=0.3),
            },
            "policies": {
                "shared": {"allowed_providers": ["p"]},
                "personal": {"allowed_providers": ["p"]},
            },
        }
    )


def antwort_skript(prompt: PromptSpec) -> str:
    """Eine Standardantwort je Aufgabe, erkennbar am Systemteil des Prompts.

    Der Fake reagiert auf den Prompt statt auf die Reihenfolge der Aufrufe: §14 fragt je Paar
    etwas anderes, und ein Skript aus einer festen Reihe müsste die Paarreihenfolge vorwegnehmen.
    """
    system = prompt.system or ""
    if "Themengruppen" in system:
        return json.dumps({"title": "Testgruppe", "description": "Vom Fake benannt."})
    if "Beziehung" in system:
        # §14.2 Schritt 4: "Keine Beziehung" ist die erwartete Mehrheitsantwort.
        return json.dumps({"relationship": None, "confidence": 0.0, "reasoning": "kein Bezug"})
    if "Themengruppe zu" in system:
        return json.dumps({"suggested_cluster_ids": [], "confidence": 0.0})
    return "Eine erzeugte Beschreibung."


@dataclass
class Umgebung:
    """Alles, was ein semantischer Lauf braucht — an einer Stelle, wie in der echten Runtime."""

    settings: Settings
    uow: MemoryUnitOfWorkFactory
    clients: FakeClients
    router: ModelRouterService
    embeddings: EmbeddingService
    clusters: ClusterService
    relations: RelationService
    orphans: OrphanService
    graph: GraphService

    def state(self, store: str = "shared") -> Any:
        """Der Inhalt eines Stores — für Zusicherungen im Test."""
        return self.uow.state(store)

    def kanten(self, store: str = "shared", *, kind: str | None = None) -> list[Any]:
        """Die Kanten eines Stores, wahlweise einer Art."""
        return [edge for edge in self.state(store).edges if kind is None or edge.kind == kind]

    def cluster_ids(self, store: str = "shared") -> list[str]:
        """Die IDs aller Cluster-Konzepte."""
        return sorted(
            concept.id
            for concept in self.state(store).concepts.values()
            if concept.type == defaults.CONCEPT_TYPE_CLUSTER
        )

    def mitglieder(self, cluster_id: str, store: str = "shared") -> set[str]:
        """Die Mitglieder eines Clusters."""
        return {
            edge.to_id
            for edge in self.state(store).edges
            if edge.from_id == cluster_id and edge.kind == defaults.EDGE_KIND_MEMBER
        }


def baue(
    settings: Settings,
    *,
    models: ModelsConfig | None = None,
    chat: Any = antwort_skript,
    dim: int = DIM,
    cluster_ids: list[str] | None = None,
) -> Umgebung:
    """Steckt Router und alle vier Läufe gegen speicherresidente Stores zusammen."""
    konfiguration = models or models_config(dim=dim)
    uow = MemoryUnitOfWorkFactory(tuple(settings.stores))
    clients = FakeClients(dim=dim, chat=chat)
    router = ModelRouterService(
        settings, konfiguration, clients, unit_of_work=uow, sleep=lambda _: None
    )

    namen = list(cluster_ids or [])
    zaehler = {"n": 0}

    def naechste_id() -> str:
        if namen:
            return namen.pop(0)
        zaehler["n"] += 1
        return f"{defaults.ID_PREFIX_CLUSTER}:test-{zaehler['n']}"

    clock = lambda: JETZT  # noqa: E731 — eine feste Uhr macht Zeitstempel vergleichbar
    relations = RelationService(settings, uow, router, clock=clock)
    return Umgebung(
        settings=settings,
        uow=uow,
        clients=clients,
        router=router,
        embeddings=EmbeddingService(settings, uow, router, clock=clock),
        clusters=ClusterService(settings, uow, router, clock=clock, new_cluster_id=naechste_id),
        relations=relations,
        orphans=OrphanService(
            settings,
            uow,
            router,
            relations=relations,
            clock=clock,
            new_cluster_id=naechste_id,
        ),
        graph=GraphService(settings, uow, router=router, clock=clock),
    )


def konzept(
    concept_id: str,
    *,
    title: str,
    description: str | None = None,
    body: str | None = None,
    scope: str = "engineering",
    store: str = "shared",
    concept_type: str = "Confluence Page",
    curated: bool = False,
) -> Concept:
    """Ein gespeichertes Konzept mit den Feldern, auf die es in §13 bis §15 ankommt."""
    from wissensgraph.domain.hashing import content_hash

    return Concept(
        id=concept_id,
        store=store,
        scope=scope,
        type=concept_type,
        title=title,
        description=description,
        body=body,
        content_hash=content_hash(title=title, description=description, body=body),
        curated=curated,
        created_at=JETZT,
        updated_at=JETZT,
    )


#: Drei Themenfelder mit klar getrenntem Wortschatz (§24, Stufe 8: "Die drei Themenfelder des
#: Korpus ergeben mindestens drei Cluster").
THEMEN: dict[str, tuple[tuple[str, str, str], ...]] = {
    "warehouse": (
        (
            "confluence:100",
            "Partitionierung Faktentabellen Warehouse",
            "Faktentabellen Warehouse Partitionen Ladestrecke Archivierung Sternschema.",
        ),
        (
            "confluence:101",
            "Faktentabellen Warehouse laden",
            "Ladestrecke Faktentabellen Warehouse Partitionen Archivierung Sternschema.",
        ),
        (
            "confluence:102",
            "Warehouse Archivierung Partitionen",
            "Archivierung Partitionen Faktentabellen Warehouse Ladestrecke Sternschema.",
        ),
        (
            "confluence:103",
            "Partitionen pflegen Warehouse",
            "Partitionen Faktentabellen Warehouse Ladestrecke Archivierung Sternschema.",
        ),
    ),
    "incident": (
        (
            "confluence:200",
            "Incident Response Runbook",
            "Runbook Incident Response Eskalation Bereitschaft Postmortem Alarmierung.",
        ),
        (
            "confluence:201",
            "Eskalation Incident Bereitschaft",
            "Eskalation Incident Bereitschaft Runbook Postmortem Alarmierung Response.",
        ),
        (
            "confluence:202",
            "Postmortem Incident Eskalation",
            "Postmortem Incident Eskalation Bereitschaft Runbook Alarmierung Response.",
        ),
        (
            "confluence:203",
            "Bereitschaft Incident Runbook",
            "Bereitschaft Runbook Eskalation Postmortem Incident Alarmierung Response.",
        ),
    ),
    "urlaub": (
        (
            "confluence:300",
            "Urlaubsantrag Personalportal",
            "Urlaubsantrag Personalportal Vertretung Genehmigung Abwesenheit Resturlaub.",
        ),
        (
            "confluence:301",
            "Vertretung Urlaub eintragen",
            "Vertretung Genehmigung Urlaubsantrag Personalportal Abwesenheit Resturlaub.",
        ),
        (
            "confluence:302",
            "Genehmigung Urlaubsantrag",
            "Genehmigung Urlaubsantrag Personalportal Vertretung Abwesenheit Resturlaub.",
        ),
        (
            "confluence:303",
            "Personalportal Urlaub Vertretung",
            "Urlaubsantrag Vertretung Genehmigung Personalportal Abwesenheit Resturlaub.",
        ),
    ),
}

#: Ein Dokument, das zwei Themen berührt (§24, Stufe 8: "das Grenzdokument landet stabil"). Es
#: nennt beide Wortschätze, aber den des Warehouse häufiger — genau so entsteht der Fall, den das
#: Kriterium meint: nicht "gehört nirgends hin", sondern "gehört knapp hierhin und soll dort
#: bleiben".
GRENZDOKUMENT = (
    "confluence:400",
    "Incident Ladestrecke Faktentabellen",
    "Ladestrecke Faktentabellen Warehouse Partitionen Archivierung Sternschema "
    "Incident Eskalation.",
)

#: Ein Knoten ohne thematische Nachbarn (§24, Stufe 10: "Der isoliert angelegte Knoten wird
#: gefunden"). Sein Text nennt einen Jira-Key, den es anderswo wirklich gibt — das ist die
#: Grundlage des Textabgleichs aus §15.2a.
ISOLIERT = (
    "note:isoliert",
    "Kaffeemaschine im dritten Stock",
    "Entkalken, Bohnen nachfüllen, Filter tauschen. Siehe Vorgang PROJ-4711.",
)


def korpus(*, mit_grenzdokument: bool = True, mit_isoliertem: bool = True) -> list[Concept]:
    """Der vollständige Testkorpus als gespeicherte Konzepte."""
    konzepte = [
        konzept(concept_id, title=titel, description=beschreibung)
        for eintraege in THEMEN.values()
        for concept_id, titel, beschreibung in eintraege
    ]
    if mit_grenzdokument:
        concept_id, titel, beschreibung = GRENZDOKUMENT
        konzepte.append(konzept(concept_id, title=titel, description=beschreibung))
    if mit_isoliertem:
        concept_id, titel, beschreibung = ISOLIERT
        konzepte.append(konzept(concept_id, title=titel, body=beschreibung))
    return konzepte


def befuellen(umgebung: Umgebung, konzepte: list[Concept], *, store: str = "shared") -> None:
    """Legt Konzepte direkt in den Store — ohne Umweg über den Sync."""
    with umgebung.uow(store) as uow:
        for concept in konzepte:
            uow.concepts.save(concept)


__all__ = [
    "DIM",
    "GRENZDOKUMENT",
    "ISOLIERT",
    "JETZT",
    "THEMEN",
    "Umgebung",
    "antwort_skript",
    "baue",
    "befuellen",
    "konzept",
    "korpus",
    "models_config",
]
