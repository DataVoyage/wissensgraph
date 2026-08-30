"""Die Kernoperation ``upsert_concept()`` (§10.2).

Dieser Dienst ist die Klammer um vier Dinge, die einzeln schon geklärt sind:

* :mod:`wissensgraph.domain.upsert` entscheidet, *was* aus einem Entwurf wird (Regeln 1 bis 4),
* die Konfiguration entscheidet, *wohin* — über Scope und Taxonomie (§7.2, §6.1 Regel 1),
* die Arbeitseinheit sorgt dafür, dass alles *gemeinsam* geschrieben wird (Regel 5),
* das Journal hält fest, *dass* es geschah (§7.4).

Was hier bewusst nicht passiert: Quellen abfragen, Embeddings berechnen, Modelle aufrufen. Der
Kern ist die Operation, auf der jede Pipeline aufsetzt — nicht die Pipeline selbst.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.bridges import bridge_sources, bridge_targets
from wissensgraph.domain.changes import CONFLICT_SOURCE_HASH_KEY, ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptDraft, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft
from wissensgraph.domain.upsert import UpsertOutcome, UpsertPlan, plan_upsert
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.repositories import UnitOfWork, UnitOfWorkFactory

_log = get_logger(__name__)


class ConceptValidationError(ValueError):
    """Ein Entwurf passt nicht zur konfigurierten Taxonomie oder zu den Scopes (§6.5, §7.2).

    Die Prüfung gehört in den Dienst und nicht in das Domänenmodell: Welche Typen es gibt und in
    welchem Store sie liegen dürfen, steht in der Konfiguration, nicht im Code.
    """


@dataclass(frozen=True)
class UpsertResult:
    """Das Ergebnis einer Kernoperation — die Grundlage der Lauf-Statistik (§10.2)."""

    concept_id: str
    store: str
    outcome: UpsertOutcome
    held_back: tuple[str, ...] = ()
    edges_added: tuple[Edge, ...] = ()
    edges_removed: tuple[Edge, ...] = ()
    verification_reset: bool = False

    @property
    def written(self) -> bool:
        """Ob die Datenbank berührt wurde."""
        return self.outcome in {UpsertOutcome.CREATED, UpsertOutcome.UPDATED} or bool(
            self.edges_added or self.edges_removed
        )

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für Logeinträge und Lauf-Statistiken.

        Enthält keine Inhaltsfelder — nur IDs, Zähler und Feldnamen (§21.1).
        """
        return {
            "concept_id": self.concept_id,
            "store": self.store,
            "outcome": str(self.outcome),
            "held_back": list(self.held_back),
            "edges_added": len(self.edges_added),
            "edges_removed": len(self.edges_removed),
            "verification_reset": self.verification_reset,
        }


@dataclass(frozen=True)
class ConceptView:
    """Ein Konzept mit seinen Kanten in beide Richtungen (§12.1).

    Die eingehenden Kanten sind der interessante Teil. Innerhalb eines Stores stehen sie einfach
    da; über die Grenze hinweg gibt es sie im Zielstore gar nicht — der geteilte Store weiß nicht,
    dass es persönliche Konzepte gibt. Sie werden deshalb aus den Stores rekonstruiert, die
    Brücken schlagen dürfen.
    """

    concept: Concept
    outgoing: tuple[Edge, ...]
    incoming: tuple[Edge, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für CLI und spätere API (§16.2)."""
        return {
            "id": self.concept.id,
            "store": self.concept.store,
            "scope": self.concept.scope,
            "type": self.concept.type,
            "title": self.concept.title,
            "status": str(self.concept.status),
            "curated": self.concept.curated,
            "outgoing": [
                {
                    "kind": edge.kind,
                    "to": f"{edge.to_store}:{edge.to_id}",
                    "resolved": edge.resolved,
                }
                for edge in self.outgoing
            ],
            "incoming": [
                {
                    "kind": edge.kind,
                    "from": f"{edge.from_store}:{edge.from_id}",
                    "resolved": edge.resolved,
                }
                for edge in self.incoming
            ],
        }


class ConceptService:
    """Schreibender Zugang zum Graphen auf Konzeptebene."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            settings: Die geprüfte Konfiguration; liefert Scopes und Taxonomie.
            unit_of_work: Fabrik für Transaktionen je Store.
            clock: Zeitquelle. Als Parameter, damit ein Test den Zeitpunkt bestimmen kann; im
                Betrieb ist es die Uhr in UTC.
        """
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- öffentliche Operationen ------------------------------------------------

    def store_of_scope(self, scope: str) -> str:
        """Der Store, in dem ein Scope liegt (§7.3).

        Raises:
            ConceptValidationError: Wenn der Scope nicht konfiguriert ist.
        """
        try:
            return self._settings.store_of_scope(scope)
        except KeyError as exc:
            bekannt = ", ".join(item.name for item in self._settings.scopes)
            raise ConceptValidationError(
                f"Unbekannter Scope '{scope}'. Konfiguriert sind: {bekannt}."
            ) from exc

    def store_for(self, draft: ConceptDraft) -> str:
        """Der Store, in den ein Entwurf gehört — abgeleitet aus seinem Scope (§7.3).

        Der Entwurf nennt seinen Store nicht selbst; er nennt seinen Scope, und die Zuordnung
        Scope → Store steht in der Konfiguration. So kann kein Adapter und kein Agent durch
        Setzen eines Feldes bestimmen, in welche Datenbank etwas geschrieben wird (§20.1).

        Raises:
            ConceptValidationError: Wenn Scope oder Typ unbekannt sind oder der Typ in diesem
                Store nicht zulässig ist.
        """
        try:
            store = self._settings.store_of_scope(draft.scope)
        except KeyError as exc:
            bekannt = ", ".join(scope.name for scope in self._settings.scopes)
            raise ConceptValidationError(
                f"Konzept '{draft.id}' nennt den unbekannten Scope '{draft.scope}'. "
                f"Konfiguriert sind: {bekannt}."
            ) from exc

        try:
            concept_type = self._settings.concept_type(draft.type)
        except KeyError as exc:
            bekannt = ", ".join(item.name for item in self._settings.concept_types)
            raise ConceptValidationError(
                f"Konzept '{draft.id}' hat den unbekannten Typ '{draft.type}'. "
                f"Konfiguriert sind: {bekannt}. Ein neuer Typ gehört in die Taxonomie in "
                f"config/wissensgraph.yaml, nicht in den Code (§7.2)."
            ) from exc

        if store not in concept_type.stores:
            raise ConceptValidationError(
                f"Der Typ '{draft.type}' ist im Store '{store}' nicht zugelassen "
                f"(erlaubt: {', '.join(concept_type.stores)}). Scope '{draft.scope}' liegt aber "
                f"in genau diesem Store (§7.2)."
            )
        return store

    def upsert(
        self, draft: ConceptDraft, *, actor: str = defaults.ACTOR_SYNC, run_id: UUID | None = None
    ) -> UpsertResult:
        """Legt ein Konzept an oder schreibt es fort — die Operation aus §10.2.

        Args:
            draft: Der vorgeschlagene Stand.
            actor: Wer die Änderung verantwortet (``'system:sync'``, ``'user:<id>'``, …).
            run_id: Der Lauf, zu dem der Vorgang gehört; ``None`` bei einer Einzeländerung.

        Returns:
            Das Ergebnis mit ``unchanged | created | updated | conflict``.

        Raises:
            ConceptValidationError: Wenn Scope oder Typ nicht zur Konfiguration passen.
        """
        store = self.store_for(draft)
        source_mirrored = self._settings.concept_type(draft.type).source_mirrored

        with self._unit_of_work(store) as uow:
            plan = plan_upsert(
                existing=uow.concepts.get(draft.id),
                draft=draft,
                store=store,
                source_mirrored=source_mirrored,
                now=self._clock(),
            )
            result = self._ausfuehren(
                uow=uow, plan=plan, draft=draft, store=store, actor=actor, run_id=run_id
            )

        _log.info("konzept.upsert", **result.as_dict())
        return result

    def mark_source_deleted(
        self,
        concept_id: str,
        *,
        store: str,
        actor: str = defaults.ACTOR_SYNC,
        run_id: UUID | None = None,
    ) -> bool:
        """Setzt ein Konzept auf ``tombstone``, weil die Quelle es als gelöscht meldet (§7.6).

        Der Grabstein ist der Ersatz für ein ``DELETE``, und der Grund dafür steht in §7.6:
        "Wird ein Objekt in der Quelle gelöscht, bleiben Inhalt und Kanten erhalten, damit
        persönliche Notizen, die darauf verlinkt haben, nachvollziehbar bleiben." Deshalb rührt
        diese Operation **keine einzige Kante an** — weder die ausgehenden noch die eingehenden.
        Eine Notiz, die auf eine gelöschte Seite zeigt, soll weiterhin zeigen, dass es sie gab.

        Der Status wird auch dann gesetzt, wenn das Konzept kuratiert ist. §10.4 nennt genau
        diese Ausnahme: "Kuration gewinnt, außer die Quelle meldet Löschung."

        Returns:
            Ob sich etwas geändert hat — ``False``, wenn die ID unbekannt ist oder bereits ein
            Grabstein war. Ein Lauf, der dieselbe Löschmeldung zweimal sieht, schreibt damit nur
            einmal ins Journal.
        """
        with self._unit_of_work(store) as uow:
            vorhanden = uow.concepts.get(concept_id)
            if vorhanden is None or vorhanden.status is ConceptStatus.TOMBSTONE:
                return False

            uow.concepts.save(
                vorhanden.model_copy(
                    update={"status": ConceptStatus.TOMBSTONE, "updated_at": self._clock()}
                )
            )
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.SOURCE_DELETED,
                    concept_id=concept_id,
                    actor=actor,
                    run_id=run_id,
                    detail={"vorheriger_status": str(vorhanden.status)},
                )
            )

        _log.info("konzept.quelle_geloescht", concept_id=concept_id, store=store)
        return True

    def describe(self, concept_id: str, *, store: str) -> ConceptView | None:
        """Ein Konzept mit seinen Kanten in beide Richtungen; ``None``, wenn es das nicht gibt.

        Die Gegenrichtung einer Brücke kostet eine Abfrage je Store, der Brücken hierher schlagen
        darf — für den geteilten Store also genau eine an den persönlichen. Sie geht bewusst in
        diese Richtung: Gefragt wird der Store, in dem die Kante *liegt*.
        """
        with self._unit_of_work(store) as uow:
            concept = uow.concepts.get(concept_id)
            if concept is None:
                return None
            ausgehend = uow.edges.list_outgoing(concept_id)
            eingehend = list(uow.edges.list_incoming(concept_id))

        for quell_store in bridge_sources(store, self._settings.stores):
            with self._unit_of_work(quell_store) as fremd:
                eingehend.extend(fremd.edges.bridges_into(to_store=store, to_ids=(concept_id,)))

        return ConceptView(concept=concept, outgoing=ausgehend, incoming=tuple(eingehend))

    def refresh_edge_resolution(self, store: str) -> int:
        """Gleicht alle Kanten ab, die in diesem Store *beginnen* (§8.5, §12.1).

        Der Schritt steht neben der Kernoperation, weil er nicht am Inhalt eines Konzepts hängt:
        Eine Kante wird auflösbar, weil ihr *Ziel* dazugekommen ist — am Ausgangskonzept hat sich
        dabei nichts geändert, sein Hash also auch nicht.

        Zwei Teile, weil es zwei Datenbanken sind: Kanten innerhalb des Stores erledigt eine
        einzige Anweisung; für jede Brücke wird einmal im Zielstore nachgesehen und das Ergebnis
        zurückgeschrieben. Zwei Abfragen je fremdem Store, unabhängig davon, wie viele Kanten
        dorthin zeigen.

        Returns:
            Die Anzahl der Kanten, deren ``resolved`` sich geändert hat — in beide Richtungen.
        """
        with self._unit_of_work(store) as uow:
            anzahl = uow.edges.refresh_resolution()
            fremde = dict(uow.edges.foreign_targets())

        for ziel_store, ziele in fremde.items():
            anzahl += self._bruecken_abgleichen(von=store, nach=ziel_store, ziele=ziele)
        for ziel_store in bridge_targets(store, self._settings.stores):
            anzahl += self._offene_anhaengen(von=store, nach=ziel_store)

        if anzahl:
            _log.info("kante.aufgeloest", store=store, count=anzahl)
        return anzahl

    def refresh_bridges_into(self, store: str) -> int:
        """Gleicht die Brücken ab, die *auf* diesen Store zeigen (§12.1).

        Die Gegenrichtung zu :meth:`refresh_edge_resolution` und der Grund, warum es sie braucht:
        Ändert sich etwas im geteilten Store — eine Seite kommt dazu, eine wird zum Grabstein —,
        dann betrifft das Kanten, die gar nicht dort liegen. Sie liegen im persönlichen Store, und
        niemand außer diesem Aufruf käme je auf die Idee, sie erneut zu prüfen.

        Der geteilte Store erfährt dabei nichts über den persönlichen: Gefragt wird immer aus der
        Richtung der Brücke heraus. Er beantwortet nur, welche seiner eigenen IDs auffindbar sind.
        """
        anzahl = 0
        for quell_store in bridge_sources(store, self._settings.stores):
            with self._unit_of_work(quell_store) as uow:
                ziele = uow.edges.foreign_targets().get(store, frozenset())
            anzahl += self._bruecken_abgleichen(von=quell_store, nach=store, ziele=ziele)
            anzahl += self._offene_anhaengen(von=quell_store, nach=store)
        if anzahl:
            _log.info("bruecke.aufgeloest", store=store, count=anzahl)
        return anzahl

    # -- innere Abläufe ---------------------------------------------------------

    def _bruecken_abgleichen(self, *, von: str, nach: str, ziele: frozenset[str]) -> int:
        """Fragt einen fremden Store nach seinen auffindbaren IDs und schreibt das Ergebnis fort.

        Die drei Transaktionen sind absichtlich getrennt und kurz. Eine offene Transaktion im
        einen Store, während im anderen gelesen wird, hielte über die Dauer einer fremden Abfrage
        Sperren — und das ausgerechnet in dem Store, aus dem gerade ein Sync schreibt.
        """
        if not ziele:
            return 0
        with self._unit_of_work(nach) as fremd:
            auffindbar = fremd.concepts.resolvable_ids(tuple(sorted(ziele)))
        with self._unit_of_work(von) as uow:
            return uow.edges.set_foreign_resolution(to_store=nach, resolvable=auffindbar)

    def _offene_anhaengen(self, *, von: str, nach: str) -> int:
        """Sucht offene Verweise im fremden Store und hängt die Kanten dorthin um (§8.5, §12.1).

        Der Fall, den das abdeckt, ist der häufigere von beiden: Jemand schreibt eine Notiz mit
        ``[[confluence:184320]]``, bevor Confluence das erste Mal synchronisiert wurde. Die Kante
        entsteht unaufgelöst und mit dem eigenen Store als Ziel, weil in diesem Augenblick niemand
        weiß, wo das Objekt einmal liegen wird. Sobald es da ist, wird aus der offenen Frage eine
        Brücke — ohne dass die Notiz dafür angefasst werden müsste.
        """
        with self._unit_of_work(von) as uow:
            offen = uow.edges.unresolved_targets()
        if not offen:
            return 0
        with self._unit_of_work(nach) as fremd:
            auffindbar = fremd.concepts.resolvable_ids(tuple(sorted(offen)))
        if not auffindbar:
            return 0
        with self._unit_of_work(von) as uow:
            return uow.edges.attach_to_store(to_store=nach, to_ids=auffindbar)

    def _ziele_aufloesen(self, uow: UnitOfWork, ids: Sequence[str]) -> dict[str, str]:
        """Sucht zu jeder referenzierten ID den Store, in dem sie auffindbar ist (§8.5, §12.1).

        Der eigene Store zuerst, dann die erlaubten Brückenziele — die Reihenfolge steht in
        :func:`resolution_order` und ist dort begründet. Was nirgends gefunden wird, taucht im
        Ergebnis nicht auf; die Kante entsteht trotzdem, nur unaufgelöst und mit dem eigenen Store
        als Ziel. §8.5: "Kaputte Referenzen sind kein Fehler."

        Für den geteilten Store ist die Schleife leer — er darf gar nicht hinauszeigen (§12.1),
        also entsteht für ihn auch keine einzige zusätzliche Abfrage.
        """
        if not ids:
            return {}
        treffer: dict[str, str] = dict.fromkeys(uow.concepts.resolvable_ids(ids), uow.store)
        offen = tuple(ziel for ziel in ids if ziel not in treffer)

        for fremd in bridge_targets(uow.store, self._settings.stores):
            if not offen:
                break
            with self._unit_of_work(fremd) as fremde:
                gefunden = fremde.concepts.resolvable_ids(offen)
            treffer.update(dict.fromkeys(gefunden, fremd))
            offen = tuple(ziel for ziel in offen if ziel not in treffer)

        return treffer

    def _ausfuehren(
        self,
        *,
        uow: UnitOfWork,
        plan: UpsertPlan,
        draft: ConceptDraft,
        store: str,
        actor: str,
        run_id: UUID | None,
    ) -> UpsertResult:
        """Schreibt, was der Plan vorsieht — Konzept, Kanten und Journal in einer Transaktion."""
        if plan.held_back:
            self._konflikt_vermerken(uow=uow, draft=draft, plan=plan, actor=actor, run_id=run_id)

        if plan.concept is None:
            return UpsertResult(
                concept_id=draft.id, store=store, outcome=plan.outcome, held_back=plan.held_back
            )

        uow.concepts.save(plan.concept)
        uow.changes.append(
            ChangeEntry(
                change_type=(
                    ChangeType.CREATED
                    if plan.outcome is UpsertOutcome.CREATED
                    else ChangeType.UPDATED
                ),
                concept_id=plan.concept.id,
                actor=actor,
                run_id=run_id,
                detail={"content_hash": plan.concept.content_hash},
            )
        )

        if plan.verification_reset:
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.VERIFICATION_RESET,
                    concept_id=plan.concept.id,
                    actor=actor,
                    run_id=run_id,
                    detail={
                        "grund": "Der Inhalt hat sich geändert; die Bestätigung galt für den "
                        "vorherigen Stand (§10.4).",
                    },
                )
            )

        hinzugefuegt, entfernt = self._kanten_abgleichen(
            uow=uow, concept=plan.concept, draft=draft, actor=actor, run_id=run_id
        )

        return UpsertResult(
            concept_id=plan.concept.id,
            store=store,
            outcome=plan.outcome,
            held_back=plan.held_back,
            edges_added=hinzugefuegt,
            edges_removed=entfernt,
            verification_reset=plan.verification_reset,
        )

    def _konflikt_vermerken(
        self,
        *,
        uow: UnitOfWork,
        draft: ConceptDraft,
        plan: UpsertPlan,
        actor: str,
        run_id: UUID | None,
    ) -> None:
        """Vermerkt einen Kurationskonflikt — einmal je unverändert fortbestehendem Konflikt.

        §10.2 Regel 4 verlangt den Eintrag. Er darf aber nicht bei jedem Lauf neu entstehen: Der
        Konflikt ist ein Zustand, der so lange besteht, bis ihn ein Mensch auflöst. Solange die
        Quelle denselben Inhalt liefert, ist es derselbe Konflikt.
        """
        quell_hash = draft.content_hash
        if uow.changes.has_open_curation_conflict(
            concept_id=draft.id, source_content_hash=quell_hash
        ):
            return
        uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.CURATION_CONFLICT,
                concept_id=draft.id,
                actor=actor,
                run_id=run_id,
                detail={
                    "fields": list(plan.held_back),
                    CONFLICT_SOURCE_HASH_KEY: quell_hash,
                    "source_name": draft.source_name,
                },
            )
        )
        _log.info(
            "konzept.kurationskonflikt",
            concept_id=draft.id,
            store=uow.store,
            held_back=list(plan.held_back),
        )

    def _kanten_abgleichen(
        self,
        *,
        uow: UnitOfWork,
        concept: Concept,
        draft: ConceptDraft,
        actor: str,
        run_id: UUID | None,
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        """Übersetzt die Referenzen eines Konzepts in Kanten (§8.5).

        Zwei Herkünfte, zwei Erzeugerkennungen: Was im Fließtext als ``[[id]]`` steht, bekommt
        ``code:body-reference``; was die Quelle gemeldet hat, ``code:source-reference`` — so
        verlangt es §8.5. Der Unterschied ist später wichtig: Verschwindet ein Verweis aus dem
        Text, soll die Kante gehen; hört die Quelle auf, ihn zu melden, ebenfalls — aber es sind
        zwei verschiedene Ereignisse, und eine gemeinsame Kennung könnte sie nicht auseinander
        halten.

        Beide gehen in *einem* Abgleich in die Datenbank. Zwei Aufrufe wären nicht atomar: Ein
        Verweis, der von der Quelle in den Text wandert, verschwände zwischen ihnen.

        Eine Referenz nennt nur eine ID, keinen Store — der Zielstore wird gesucht. Eine Notiz in
        ``personal``, die ``[[confluence:184320]]`` schreibt, bekommt damit eine Kante nach
        ``shared``: die Brücke aus §7.3. Findet sich die ID nirgends, entsteht die Kante trotzdem,
        mit dem eigenen Store als Ziel und ``resolved = false`` — genau der Zustand, den §8.5
        dafür vorsieht.

        Der eigene Store bleibt Ziel auch dann, wenn die ID unbekannt ist. Etwas anderes wäre
        eine Behauptung: Wo ein noch nicht synchronisiertes Objekt einmal liegen wird, weiß hier
        niemand, und ein geratener Zielstore stünde als Tatsache in der Datenbank.
        """
        herkunft = {
            **dict.fromkeys(draft.body_references, defaults.GENERATED_BY_BODY_REFERENCE),
            **dict.fromkeys(draft.source_references, defaults.GENERATED_BY_SOURCE_REFERENCE),
        }
        gefunden = self._ziele_aufloesen(uow, tuple(herkunft))
        jetzt = self._clock()

        drafts = [
            EdgeDraft(
                from_store=concept.store,
                from_id=concept.id,
                to_store=gefunden.get(ziel, concept.store),
                to_id=ziel,
                kind=defaults.EDGE_KIND_REFERENCES,
                resolved=ziel in gefunden,
                generated_by=erzeuger,
                generated_at=jetzt,
            )
            for ziel, erzeuger in herkunft.items()
        ]

        hinzugefuegt, entfernt = uow.edges.replace_generated(
            from_id=concept.id,
            generated_by=(
                defaults.GENERATED_BY_BODY_REFERENCE,
                defaults.GENERATED_BY_SOURCE_REFERENCE,
            ),
            drafts=drafts,
        )

        for edge in hinzugefuegt:
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED,
                    concept_id=concept.id,
                    edge_id=edge.id,
                    actor=actor,
                    run_id=run_id,
                    detail={"kind": edge.kind, "to_id": edge.to_id, "resolved": edge.resolved},
                )
            )
        for edge in entfernt:
            uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_REMOVED,
                    concept_id=concept.id,
                    edge_id=edge.id,
                    actor=actor,
                    run_id=run_id,
                    detail={"kind": edge.kind, "to_id": edge.to_id},
                )
            )
        return hinzugefuegt, entfernt
