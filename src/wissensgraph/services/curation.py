"""Der Schreibpfad des Menschen — Kanten, Cluster, Bestätigungen, Undo (§16.2, §17.2 bis §17.4).

Dieser Dienst ist die einzige Stelle, an der ein Mensch den geteilten Store verändert, und auch
dort nur auf Organisationsebene: Kanten, Cluster, Mitgliedschaft, Status, Tags, Verifikation.
``title``, ``description`` und ``body`` eines quellgespiegelten Konzepts bleiben gesperrt — sie
gehören der Quelle (§17.4, Leitprinzip 4).

Zwei Regeln durchziehen alles, was hier passiert:

**Jede Kuration setzt ``curated = true``.** Das ist nicht kosmetisch: §10.4 erlaubt Läufen, ihre
eigenen generierten Kanten zu ersetzen, und schützt genau die kuratierten. Ohne das Flag hätte
eine Handbewegung die Lebensdauer eines Laufs.

**Jede Kuration hinterlässt einen Journaleintrag mit ``actor``.** Er ist zugleich der Anker des
Undo aus §17.3: Rückgängig gemacht wird ein *bestimmter* Eintrag, nicht "die letzte Änderung".
Was ein Undo kann und was nicht, steht bei :meth:`CurationService.undo`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.changes import ChangeEntry, ChangeType
from wissensgraph.domain.concepts import Concept, ConceptStatus
from wissensgraph.domain.edges import Edge, EdgeDraft
from wissensgraph.domain.hashing import content_hash
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.repositories import UnitOfWork, UnitOfWorkFactory

_log = get_logger(__name__)

#: Schlüssel im ``detail`` eines Journaleintrags. Sie stehen als Konstanten hier, weil das Undo
#: sie wieder auslesen muss: Ein Tippfehler auf einer der beiden Seiten machte eine Änderung
#: unumkehrbar, ohne dass etwas fehlschlüge.
DETAIL_TRIPLE = "triple"
DETAIL_KIND = "kind"
DETAIL_FIELDS = "fields"
DETAIL_PREVIOUS_STATUS = "previous_status"
DETAIL_CLUSTER = "cluster_id"
DETAIL_MEMBER = "concept_id"
DETAIL_REASON = "reason"
DETAIL_MERGED_FROM = "merged_from"
DETAIL_EDGE_FIELDS = "edge"

#: Die Felder eines Konzepts, die ein Mensch auch an quellgespiegelten Inhalten setzen darf
#: (§17.4: "``shared``: ``status``, ``tags``, Verifikation — ja").
CURATABLE_FIELDS: frozenset[str] = frozenset({"status", "tags", "audience", "stale_after"})

#: Die Inhaltsfelder. An einem quellgespiegelten Konzept sind sie gesperrt (§17.4, Leitprinzip 4).
CONTENT_FIELDS: frozenset[str] = frozenset({"title", "description", "body", "resource"})


class CurationError(RuntimeError):
    """Eine Kuration ist nach den Regeln aus §17.4 oder §16.2 nicht zulässig."""


class NotFoundError(LookupError):
    """Das Ziel einer Kuration gibt es nicht."""


@dataclass(frozen=True)
class CurationResult:
    """Das Ergebnis einer Kuration samt ihres Journaleintrags (§17.3).

    Der Eintrag kommt mit zurück, damit die Oberfläche sofort ein Undo anbieten kann, ohne die
    Historie erneut zu laden. Ohne ihn müsste sie raten, welcher der eben geschriebenen Einträge
    zu ihrer Aktion gehört.
    """

    entry: ChangeEntry
    concept: Concept | None = None
    edge: Edge | None = None
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für die API."""
        from wissensgraph.services.serialization import journal_dict, kante_dict, konzept_dict

        return {
            "entry": journal_dict(self.entry),
            "concept": None if self.concept is None else konzept_dict(self.concept),
            "edge": None if self.edge is None else kante_dict(self.edge),
            "detail": self.detail,
        }


class CurationService:
    """Alle schreibenden Operationen der Oberfläche (§16.2, §17.2)."""

    def __init__(
        self,
        settings: Settings,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- Konzepte ----------------------------------------------------------------

    def create_concept(
        self,
        *,
        scope: str,
        concept_type: str,
        title: str,
        description: str | None = None,
        body: str | None = None,
        tags: Sequence[str] = (),
        actor: str,
    ) -> CurationResult:
        """Legt ein Konzept an — ausschließlich im ``personal``-Store (§16.2).

        Die Beschränkung ist keine Bequemlichkeit, sondern §17.4: Der geteilte Store bekommt seine
        Inhalte aus den Quellen. Was ein Mensch aufschreibt, ist eine persönliche Notiz, auch wenn
        sie später eine Brücke schlägt.

        Raises:
            CurationError: Wenn der Scope nicht im ``personal``-Store liegt oder der Typ dort
                nicht zugelassen ist.
        """
        store = self._store_of_scope(scope)
        if store != defaults.STORE_PERSONAL:
            raise CurationError(
                f"Konzepte lassen sich nur im Store '{defaults.STORE_PERSONAL}' anlegen; Scope "
                f"'{scope}' liegt in '{store}'. Der geteilte Store bekommt seine Inhalte aus den "
                f"Quellen (§17.4)."
            )
        try:
            typ = self._settings.concept_type(concept_type)
        except KeyError as exc:
            raise CurationError(
                f"Unbekannter Typ '{concept_type}'. Ein neuer Typ gehört in die Taxonomie in "
                f"config/wissensgraph.yaml, nicht in den Code (§7.2)."
            ) from exc
        if store not in typ.stores:
            raise CurationError(
                f"Der Typ '{concept_type}' ist im Store '{store}' nicht zugelassen "
                f"(erlaubt: {', '.join(typ.stores)})."
            )

        jetzt = self._clock()
        praefix = (
            defaults.ID_PREFIX_PROJECT
            if concept_type.lower().startswith("project")
            else defaults.ID_PREFIX_NOTE
        )
        concept = Concept(
            id=f"{praefix}{defaults.ID_SEPARATOR}{uuid4()}",
            store=store,
            scope=scope,
            type=concept_type,
            title=title,
            description=description,
            body=body,
            tags=tuple(tags),
            content_hash=content_hash(title=title, description=description, body=body),
            status=ConceptStatus(defaults.CONCEPT_STATUS_DEFAULT),
            curated=True,
            created_at=jetzt,
            updated_at=jetzt,
        )
        with self._unit_of_work(store) as uow:
            uow.concepts.save(concept)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CREATED,
                    concept_id=concept.id,
                    actor=actor,
                    detail={"type": concept_type, "scope": scope},
                )
            )
        _log.info("kuration.konzept_angelegt", concept_id=concept.id, actor=actor)
        return CurationResult(entry=eintrag, concept=concept)

    def patch_concept(
        self, concept_id: str, *, store: str, changes: dict[str, Any], actor: str
    ) -> CurationResult:
        """Ändert ein Konzept; an gespiegelten Inhalten nur die kuratierbaren Felder (§16.2).

        Raises:
            NotFoundError: Wenn es das Konzept nicht gibt.
            CurationError: Wenn ein gesperrtes Feld geändert werden soll (§17.4).
        """
        if not changes:
            raise CurationError("Eine Änderung ohne Felder ist keine Änderung.")

        with self._unit_of_work(store) as uow:
            vorhanden = uow.concepts.get(concept_id)
            if vorhanden is None:
                raise NotFoundError(f"Konzept '{concept_id}' gibt es im Store '{store}' nicht.")

            gesperrt = sorted(set(changes) & CONTENT_FIELDS) if vorhanden.is_from_source else []
            if gesperrt:
                raise CurationError(
                    f"Die Felder {', '.join(gesperrt)} von '{concept_id}' stammen aus der Quelle "
                    f"'{vorhanden.source_name}' und sind gesperrt. Änderbar sind hier: "
                    f"{', '.join(sorted(CURATABLE_FIELDS))} (§17.4)."
                )
            unbekannt = sorted(set(changes) - CURATABLE_FIELDS - CONTENT_FIELDS)
            if unbekannt:
                raise CurationError(
                    f"Die Felder {', '.join(unbekannt)} lassen sich nicht kuratieren. Änderbar "
                    f"sind: {', '.join(sorted(CURATABLE_FIELDS | CONTENT_FIELDS))}."
                )

            aktualisierung: dict[str, Any] = {
                **changes,
                "curated": True,
                "updated_at": self._clock(),
            }
            if set(changes) & CONTENT_FIELDS:
                # Der Hash zieht mit: Er beantwortet "hat sich der Inhalt geändert?" (§10.3), und
                # die Antwort ist hier ja. Bliebe er stehen, hielte der nächste Embedding-Lauf
                # den alten Vektor für gültig und der neue Text bliebe unauffindbar.
                zwischen = vorhanden.model_copy(update=changes)
                aktualisierung["content_hash"] = content_hash(
                    title=zwischen.title, description=zwischen.description, body=zwischen.body
                )
            neu = vorhanden.model_copy(update=aktualisierung)
            uow.concepts.save(neu)
            art = ChangeType.STATUS_CHANGED if set(changes) == {"status"} else ChangeType.UPDATED
            # Im Journal stehen Feldnamen, keine Werte — mit einer Ausnahme: der vorherige
            # Status. Er ist eine Aufzählung aus §7.6 und kein Inhalt, und ohne ihn ließe sich
            # eine Statusänderung nicht zurücknehmen (§17.3).
            detail: dict[str, Any] = {DETAIL_FIELDS: sorted(changes)}
            if "status" in changes:
                detail[DETAIL_PREVIOUS_STATUS] = str(vorhanden.status)
            eintrag = uow.changes.append(
                ChangeEntry(change_type=art, concept_id=concept_id, actor=actor, detail=detail)
            )
        _log.info(
            "kuration.konzept_geaendert",
            concept_id=concept_id,
            fields=sorted(changes),
            actor=actor,
        )
        return CurationResult(entry=eintrag, concept=neu)

    # -- Kanten ------------------------------------------------------------------

    def add_edge(
        self,
        *,
        store: str,
        from_id: str,
        to_id: str,
        to_store: str | None = None,
        kind: str = defaults.EDGE_KIND_REFERENCES,
        actor: str,
    ) -> CurationResult:
        """Legt eine Kante von Hand an (``curated = true``, §16.2).

        Raises:
            NotFoundError: Wenn der Ausgangspunkt in diesem Store nicht existiert.
            CurationError: Wenn die Kantenart nicht konfiguriert ist oder es die Kante schon gibt.
        """
        self._kantenart_pruefen(kind)
        ziel_store = to_store or store
        with self._unit_of_work(store) as uow:
            if not uow.concepts.exists(from_id):
                raise NotFoundError(f"Konzept '{from_id}' gibt es im Store '{store}' nicht.")
            aufloesbar = (
                uow.concepts.resolvable_ids([to_id]) if ziel_store == store else frozenset()
            )
            if ziel_store != store:
                with self._unit_of_work(ziel_store) as fremd:
                    aufloesbar = fremd.concepts.resolvable_ids([to_id])
            jetzt = self._clock()
            kante = uow.edges.add(
                EdgeDraft(
                    from_store=store,
                    from_id=from_id,
                    to_store=ziel_store,
                    to_id=to_id,
                    kind=kind,
                    resolved=to_id in aufloesbar,
                    curated=True,
                    verified_by=actor,
                    verified_at=jetzt,
                )
            )
            if kante is None:
                raise CurationError(
                    f"Zwischen '{from_id}' und '{to_id}' gibt es bereits eine Kante der Art "
                    f"'{kind}'."
                )
            # Ein früheres Verwerfen desselben Tripels wird zurückgenommen: Wer die Kante jetzt
            # von Hand setzt, hat seine Meinung geändert, und ein stehen gebliebener Negativvermerk
            # würde sie beim nächsten Lauf erneut blockieren.
            uow.edges.unreject(
                from_store=store, from_id=from_id, to_store=ziel_store, to_id=to_id, kind=kind
            )
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED,
                    concept_id=from_id,
                    edge_id=kante.id,
                    actor=actor,
                    detail={DETAIL_TRIPLE: list(kante.triple)},
                )
            )
        _log.info("kuration.kante_angelegt", edge_id=str(kante.id), kind=kind, actor=actor)
        return CurationResult(entry=eintrag, edge=kante)

    def delete_edge(self, edge_id: UUID, *, store: str, actor: str) -> CurationResult:
        """Entfernt eine Kante mit Journaleintrag (§16.2).

        Ohne Negativvermerk: Löschen heißt "hier gehört sie nicht hin", Verwerfen heißt "diese
        Beziehung gibt es nicht". Nur das zweite soll einen Folgelauf binden — deshalb sind es
        zwei Endpunkte und nicht ein Flag.
        """
        with self._unit_of_work(store) as uow:
            entfernt = uow.edges.remove(edge_id)
            if entfernt is None:
                raise NotFoundError(f"Kante '{edge_id}' gibt es im Store '{store}' nicht.")
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_REMOVED,
                    concept_id=entfernt.from_id,
                    edge_id=entfernt.id,
                    actor=actor,
                    detail={DETAIL_EDGE_FIELDS: _kante_wiederherstellbar(entfernt)},
                )
            )
        _log.info("kuration.kante_entfernt", edge_id=str(edge_id), actor=actor)
        return CurationResult(entry=eintrag, edge=entfernt)

    def verify_edge(self, edge_id: UUID, *, store: str, actor: str) -> CurationResult:
        """Bestätigt eine Kante (§16.2, Leitprinzip 6)."""
        with self._unit_of_work(store) as uow:
            kante = uow.edges.verify(edge_id=edge_id, actor=actor, now=self._clock())
            if kante is None:
                raise NotFoundError(f"Kante '{edge_id}' gibt es im Store '{store}' nicht.")
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.VERIFIED,
                    concept_id=kante.from_id,
                    edge_id=kante.id,
                    actor=actor,
                    detail={DETAIL_KIND: kante.kind},
                )
            )
        _log.info("kuration.kante_bestaetigt", edge_id=str(edge_id), actor=actor)
        return CurationResult(entry=eintrag, edge=kante)

    def reject_edge(
        self, edge_id: UUID, *, store: str, actor: str, reason: str | None = None
    ) -> CurationResult:
        """Verwirft eine Kante: entfernen **und** als Negativ vermerken (§16.2).

        Der Vermerk ist der Unterschied zum Löschen. §24 macht ihn zum Abnahmekriterium: "der
        verworfene entsteht im Folgelauf nicht neu." Ohne ihn fände die Kantenerkennung dasselbe
        Paar mit derselben Ähnlichkeit, fragte dasselbe Modell und bekäme dieselbe Antwort.
        """
        jetzt = self._clock()
        with self._unit_of_work(store) as uow:
            entfernt = uow.edges.remove(edge_id)
            if entfernt is None:
                raise NotFoundError(f"Kante '{edge_id}' gibt es im Store '{store}' nicht.")
            uow.edges.reject(edge=entfernt, actor=actor, reason=reason, now=jetzt)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.REJECTED,
                    concept_id=entfernt.from_id,
                    edge_id=entfernt.id,
                    actor=actor,
                    detail={
                        DETAIL_EDGE_FIELDS: _kante_wiederherstellbar(entfernt),
                        DETAIL_REASON: reason,
                    },
                )
            )
        _log.info("kuration.kante_verworfen", edge_id=str(edge_id), kind=entfernt.kind, actor=actor)
        return CurationResult(entry=eintrag, edge=entfernt)

    # -- Cluster ------------------------------------------------------------------

    def create_cluster(
        self,
        *,
        store: str,
        scope: str,
        title: str,
        description: str | None = None,
        member_ids: Sequence[str] = (),
        actor: str,
    ) -> CurationResult:
        """Legt ein Cluster von Hand an und hängt eine Auswahl hinein (§16.2).

        Das Cluster ist ``curated``, und das hat eine Folge: §13.2 Schritt 4 nimmt es damit von
        der automatischen Neubetitelung aus. Wer ein Cluster selbst benennt, will nicht, dass ein
        Lauf es umbenennt.
        """
        if self._store_of_scope(scope) != store:
            raise CurationError(
                f"Scope '{scope}' liegt nicht im Store '{store}'. Ein Cluster gehört in den Store "
                f"seiner Mitglieder (§13.2)."
            )
        jetzt = self._clock()
        cluster = Concept(
            id=f"{defaults.ID_PREFIX_CLUSTER}{defaults.ID_SEPARATOR}{uuid4()}",
            store=store,
            scope=scope,
            type=defaults.CONCEPT_TYPE_CLUSTER,
            title=title,
            description=description,
            content_hash=content_hash(title=title, description=description),
            status=ConceptStatus(defaults.CONCEPT_STATUS_DEFAULT),
            curated=True,
            created_at=jetzt,
            updated_at=jetzt,
        )
        with self._unit_of_work(store) as uow:
            uow.concepts.save(cluster)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CREATED,
                    concept_id=cluster.id,
                    actor=actor,
                    detail={"type": defaults.CONCEPT_TYPE_CLUSTER, "scope": scope},
                )
            )
            for concept_id in member_ids:
                self._mitglied_hinzufuegen(
                    uow, cluster_id=cluster.id, concept_id=concept_id, actor=actor
                )
        _log.info(
            "kuration.cluster_angelegt",
            cluster_id=cluster.id,
            members=len(member_ids),
            actor=actor,
        )
        return CurationResult(entry=eintrag, concept=cluster)

    def add_members(
        self, cluster_id: str, *, store: str, concept_ids: Sequence[str], actor: str
    ) -> tuple[CurationResult, ...]:
        """Hängt Konzepte in ein Cluster (``curated = true``, §16.2).

        Ein bestehender Ausschlussvermerk wird dabei aufgehoben: Wer ein Mitglied von Hand wieder
        hineinzieht, hat seine frühere Entscheidung geändert (§13.4).
        """
        ergebnisse: list[CurationResult] = []
        with self._unit_of_work(store) as uow:
            if not uow.concepts.exists(cluster_id):
                raise NotFoundError(f"Cluster '{cluster_id}' gibt es im Store '{store}' nicht.")
            for concept_id in concept_ids:
                ergebnisse.append(
                    self._mitglied_hinzufuegen(
                        uow, cluster_id=cluster_id, concept_id=concept_id, actor=actor
                    )
                )
        _log.info(
            "kuration.mitglieder_hinzugefuegt",
            cluster_id=cluster_id,
            anzahl=len(ergebnisse),
            actor=actor,
        )
        return tuple(ergebnisse)

    def remove_member(
        self, cluster_id: str, concept_id: str, *, store: str, actor: str
    ) -> CurationResult:
        """Entfernt ein Mitglied und vermerkt den Ausschluss (§13.4, §16.2).

        Der Vermerk ist der Grund, warum die Handbewegung einen Clustering-Lauf überlebt — das
        Abnahmekriterium aus §24: "ein Mitglied wird per Drag-and-Drop in ein anderes Cluster
        verschoben und überlebt einen erneuten Clustering-Lauf."
        """
        with self._unit_of_work(store) as uow:
            ergebnis = self._mitglied_entfernen(
                uow, cluster_id=cluster_id, concept_id=concept_id, actor=actor
            )
        _log.info(
            "kuration.mitglied_entfernt", cluster_id=cluster_id, concept_id=concept_id, actor=actor
        )
        return ergebnis

    def split(
        self,
        cluster_id: str,
        *,
        store: str,
        concept_ids: Sequence[str],
        title: str,
        description: str | None = None,
        actor: str,
    ) -> CurationResult:
        """Gliedert eine Auswahl in ein neues Cluster aus (§16.2, §17.2 Ansicht 3).

        Alles in **einer** Arbeitseinheit: Ein Abbruch nach dem Entfernen und vor dem Anlegen
        ließe die Auswahl in keinem Cluster zurück — ein Zustand, den niemand angefordert hat und
        den die Oberfläche nicht von einem gewollten unterscheiden könnte.
        """
        if not concept_ids:
            raise CurationError("Eine Ausgliederung ohne Mitglieder ergäbe ein leeres Cluster.")
        jetzt = self._clock()
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.get(cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' gibt es im Store '{store}' nicht.")

            neues = Concept(
                id=f"{defaults.ID_PREFIX_CLUSTER}{defaults.ID_SEPARATOR}{uuid4()}",
                store=store,
                scope=cluster.scope,
                type=defaults.CONCEPT_TYPE_CLUSTER,
                title=title,
                description=description,
                content_hash=content_hash(title=title, description=description),
                status=ConceptStatus(defaults.CONCEPT_STATUS_DEFAULT),
                curated=True,
                created_at=jetzt,
                updated_at=jetzt,
            )
            uow.concepts.save(neues)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.CREATED,
                    concept_id=neues.id,
                    actor=actor,
                    detail={"split_from": cluster_id, "members": len(concept_ids)},
                )
            )
            for concept_id in concept_ids:
                self._mitglied_entfernen(
                    uow, cluster_id=cluster_id, concept_id=concept_id, actor=actor
                )
                self._mitglied_hinzufuegen(
                    uow, cluster_id=neues.id, concept_id=concept_id, actor=actor
                )
        _log.info(
            "kuration.cluster_geteilt",
            von=cluster_id,
            nach=neues.id,
            anzahl=len(concept_ids),
            actor=actor,
        )
        return CurationResult(entry=eintrag, concept=neues)

    def merge(self, *, store: str, source_id: str, target_id: str, actor: str) -> CurationResult:
        """Verschmilzt zwei Cluster; die Kanten des Quellclusters werden umgehängt (§16.2).

        Das Quellcluster wird danach entfernt und nicht zum Grabstein: Ein Grabstein sagt "die
        Quelle hat es gelöscht" (§7.6), und ein von Hand verschmolzenes Cluster stand in keiner
        Quelle.
        """
        if source_id == target_id:
            raise CurationError("Ein Cluster lässt sich nicht mit sich selbst verschmelzen.")
        with self._unit_of_work(store) as uow:
            quelle = uow.concepts.get(source_id)
            ziel = uow.concepts.get(target_id)
            if quelle is None or ziel is None:
                fehlend = source_id if quelle is None else target_id
                raise NotFoundError(f"Cluster '{fehlend}' gibt es im Store '{store}' nicht.")
            umgehaengt = uow.edges.retarget(from_id=source_id, to_id=target_id)
            uow.concepts.delete(source_id)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.MERGED,
                    concept_id=target_id,
                    actor=actor,
                    detail={DETAIL_MERGED_FROM: source_id, "edges": umgehaengt},
                )
            )
        _log.info(
            "kuration.cluster_verschmolzen",
            source=source_id,
            target=target_id,
            edges=umgehaengt,
            actor=actor,
        )
        return CurationResult(entry=eintrag, concept=ziel, detail={"edges": umgehaengt})

    def patch_cluster(
        self,
        cluster_id: str,
        *,
        store: str,
        title: str | None = None,
        description: str | None = None,
        actor: str,
    ) -> CurationResult:
        """Setzt Titel und Beschreibung eines Clusters von Hand (§16.2).

        Sperrt damit die automatische Neubetitelung: §13.2 Schritt 4 lässt kuratierte Cluster in
        Ruhe. Das ist die eigentliche Wirkung dieses Endpunkts — der neue Titel ist nur die
        sichtbare Hälfte.
        """
        if title is None and description is None:
            raise CurationError("Weder Titel noch Beschreibung angegeben.")
        with self._unit_of_work(store) as uow:
            cluster = uow.concepts.get(cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' gibt es im Store '{store}' nicht.")
            aenderungen: dict[str, Any] = {"curated": True, "updated_at": self._clock()}
            if title is not None:
                aenderungen["title"] = title
            if description is not None:
                aenderungen["description"] = description
            neu = cluster.model_copy(update=aenderungen)
            uow.concepts.save(neu)
            eintrag = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.UPDATED,
                    concept_id=cluster_id,
                    actor=actor,
                    detail={
                        DETAIL_FIELDS: sorted(set(aenderungen) - {"curated", "updated_at"}),
                        "relabeling_locked": True,
                    },
                )
            )
        _log.info("kuration.cluster_umbenannt", cluster_id=cluster_id, actor=actor)
        return CurationResult(entry=eintrag, concept=neu)

    # -- Undo ---------------------------------------------------------------------

    def undo(self, entry_id: int, *, store: str, actor: str) -> CurationResult:
        """Nimmt eine Kuration zurück (§17.3).

        Rückgängig gemacht wird ein *bestimmter* Journaleintrag und nicht "die letzte Änderung":
        In einem System, das nebenher synchronisiert, clustert und Kanten erkennt, wäre "die
        letzte" selten die gemeinte.

        Was sich zurücknehmen lässt, sind Strukturentscheidungen — Kanten, Mitgliedschaften,
        Bestätigungen, Verwerfen, Anlagen, Statuswechsel. Was sich **nicht** zurücknehmen lässt,
        ist eine inhaltliche Änderung an Titel, Beschreibung oder Fließtext. Der Grund steht im
        Journal selbst: Es hält Feldnamen fest, keine Werte (§7.4, §21.1). Den alten Text dort
        abzulegen hieße, Inhalte an einer zweiten Stelle zu führen — und aus einem Journal, das
        heute gefahrlos exportierbar ist, würde eines, das es nicht mehr ist. Der Endpunkt sagt
        das deshalb offen, statt es zu versuchen und stillschweigend nur die Hälfte
        wiederherzustellen.

        Raises:
            NotFoundError: Wenn es den Eintrag nicht gibt.
            CurationError: Wenn diese Änderungsart sich nicht zurücknehmen lässt.
        """
        with self._unit_of_work(store) as uow:
            eintrag = uow.changes.get(entry_id)
            if eintrag is None:
                raise NotFoundError(f"Journaleintrag {entry_id} gibt es im Store '{store}' nicht.")
            rueckgabe = self._zuruecknehmen(uow, eintrag, actor=actor, store=store)
        _log.info(
            "kuration.rueckgaengig",
            entry_id=entry_id,
            change_type=str(eintrag.change_type),
            actor=actor,
        )
        return rueckgabe

    def _zuruecknehmen(
        self, uow: UnitOfWork, eintrag: ChangeEntry, *, actor: str, store: str
    ) -> CurationResult:
        """Die eigentliche Rücknahme, je nach Änderungsart."""
        detail = eintrag.detail or {}
        art = eintrag.change_type

        if art in {ChangeType.EDGE_ADDED, ChangeType.CLUSTER_ASSIGNED}:
            if eintrag.edge_id is None:
                raise CurationError("Der Eintrag nennt keine Kante; es gibt nichts zurückzunehmen.")
            entfernt = uow.edges.remove(eintrag.edge_id)
            if entfernt is None:
                raise CurationError("Die Kante gibt es nicht mehr; nichts zurückzunehmen.")
            neu = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_REMOVED,
                    concept_id=entfernt.from_id,
                    edge_id=entfernt.id,
                    actor=actor,
                    detail={"undo_of": eintrag.id},
                )
            )
            return CurationResult(entry=neu, edge=entfernt)

        if art in {ChangeType.EDGE_REMOVED, ChangeType.REJECTED, ChangeType.CLUSTER_REMOVED}:
            return self._kante_wiederherstellen(uow, eintrag, detail, actor=actor, store=store)

        if art is ChangeType.VERIFIED:
            if eintrag.edge_id is None:
                raise CurationError("Der Eintrag nennt keine Kante.")
            kante = uow.edges.get(eintrag.edge_id)
            if kante is None:
                raise CurationError("Die Kante gibt es nicht mehr.")
            # ``curated`` bleibt stehen: Dass ein Mensch die Kante angefasst hat, ist auch nach
            # dem Zurücknehmen der Bestätigung wahr — und ein Lauf soll sie weiterhin in Ruhe
            # lassen (§10.4).
            uow.edges.remove(kante.id)
            wieder = uow.edges.add(
                EdgeDraft(
                    **{
                        **kante.model_dump(exclude={"id", "created_at"}),
                        "verified_by": None,
                        "verified_at": None,
                    }
                )
            )
            neu = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.EDGE_ADDED,
                    concept_id=kante.from_id,
                    edge_id=None if wieder is None else wieder.id,
                    actor=actor,
                    detail={"undo_of": eintrag.id},
                )
            )
            return CurationResult(entry=neu, edge=wieder)

        if art is ChangeType.CREATED:
            if eintrag.concept_id is None:
                raise CurationError("Der Eintrag nennt kein Konzept.")
            if not uow.concepts.delete(eintrag.concept_id):
                raise CurationError("Das Konzept gibt es nicht mehr.")
            neu = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.STATUS_CHANGED,
                    concept_id=eintrag.concept_id,
                    actor=actor,
                    detail={"undo_of": eintrag.id, "deleted": True},
                )
            )
            return CurationResult(entry=neu)

        if art is ChangeType.STATUS_CHANGED and DETAIL_PREVIOUS_STATUS in detail:
            if eintrag.concept_id is None:
                raise CurationError("Der Eintrag nennt kein Konzept.")
            concept = uow.concepts.get(eintrag.concept_id)
            if concept is None:
                raise CurationError("Das Konzept gibt es nicht mehr.")
            vorher = ConceptStatus(str(detail[DETAIL_PREVIOUS_STATUS]))
            zurueck = concept.model_copy(update={"status": vorher, "updated_at": self._clock()})
            uow.concepts.save(zurueck)
            neu = uow.changes.append(
                ChangeEntry(
                    change_type=ChangeType.STATUS_CHANGED,
                    concept_id=concept.id,
                    actor=actor,
                    detail={"undo_of": eintrag.id, DETAIL_PREVIOUS_STATUS: str(concept.status)},
                )
            )
            return CurationResult(entry=neu, concept=zurueck)

        raise CurationError(
            f"Eine Änderung der Art '{art}' lässt sich nicht zurücknehmen. Inhaltliche Änderungen "
            f"sind davon ausgenommen: Das Journal hält Feldnamen fest, keine Werte (§7.4)."
        )

    def _kante_wiederherstellen(
        self,
        uow: UnitOfWork,
        eintrag: ChangeEntry,
        detail: dict[str, Any],
        *,
        actor: str,
        store: str,
    ) -> CurationResult:
        """Legt eine entfernte oder verworfene Kante wieder an."""
        if eintrag.change_type is ChangeType.CLUSTER_REMOVED:
            cluster_id = str(detail.get(DETAIL_CLUSTER, ""))
            concept_id = str(detail.get(DETAIL_MEMBER, ""))
            if not cluster_id or not concept_id:
                raise CurationError("Der Eintrag nennt weder Cluster noch Mitglied.")
            uow.clusters.include(concept_id=concept_id, cluster_id=cluster_id)
            return self._mitglied_hinzufuegen(
                uow, cluster_id=cluster_id, concept_id=concept_id, actor=actor
            )

        felder = detail.get(DETAIL_EDGE_FIELDS)
        if not isinstance(felder, dict):
            raise CurationError("Der Eintrag hält die Kante nicht fest.")
        entwurf = EdgeDraft.model_validate(felder)
        if eintrag.change_type is ChangeType.REJECTED:
            uow.edges.unreject(
                from_store=entwurf.from_store,
                from_id=entwurf.from_id,
                to_store=entwurf.to_store,
                to_id=entwurf.to_id,
                kind=entwurf.kind,
            )
        kante = uow.edges.add(entwurf)
        if kante is None:
            raise CurationError("Die Kante gibt es bereits wieder.")
        neu = uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.EDGE_ADDED,
                concept_id=kante.from_id,
                edge_id=kante.id,
                actor=actor,
                detail={"undo_of": eintrag.id, DETAIL_TRIPLE: list(kante.triple)},
            )
        )
        del store
        return CurationResult(entry=neu, edge=kante)

    # -- Hilfen -------------------------------------------------------------------

    def _mitglied_hinzufuegen(
        self, uow: UnitOfWork, *, cluster_id: str, concept_id: str, actor: str
    ) -> CurationResult:
        """Schreibt eine kuratierte ``member``-Kante und hebt einen Ausschluss auf (§13.4)."""
        if not uow.concepts.exists(concept_id):
            raise NotFoundError(f"Konzept '{concept_id}' gibt es im Store '{uow.store}' nicht.")
        uow.clusters.include(concept_id=concept_id, cluster_id=cluster_id)
        jetzt = self._clock()
        kante = uow.edges.add(
            EdgeDraft(
                from_store=uow.store,
                from_id=cluster_id,
                to_store=uow.store,
                to_id=concept_id,
                kind=defaults.EDGE_KIND_MEMBER,
                resolved=True,
                curated=True,
                verified_by=actor,
                verified_at=jetzt,
            )
        )
        if kante is None:
            raise CurationError(f"'{concept_id}' ist bereits Mitglied von '{cluster_id}'.")
        eintrag = uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.CLUSTER_ASSIGNED,
                concept_id=concept_id,
                edge_id=kante.id,
                actor=actor,
                detail={DETAIL_CLUSTER: cluster_id, DETAIL_MEMBER: concept_id},
            )
        )
        return CurationResult(entry=eintrag, edge=kante)

    def _mitglied_entfernen(
        self, uow: UnitOfWork, *, cluster_id: str, concept_id: str, actor: str
    ) -> CurationResult:
        """Entfernt eine ``member``-Kante und vermerkt den Ausschluss (§13.4)."""
        kante = next(
            (
                edge
                for edge in uow.edges.list_outgoing(cluster_id)
                if edge.kind == defaults.EDGE_KIND_MEMBER and edge.to_id == concept_id
            ),
            None,
        )
        if kante is None:
            raise NotFoundError(
                f"'{concept_id}' ist kein Mitglied von '{cluster_id}' im Store '{uow.store}'."
            )
        uow.edges.remove(kante.id)
        uow.clusters.exclude(concept_id=concept_id, cluster_id=cluster_id)
        eintrag = uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.CLUSTER_REMOVED,
                concept_id=concept_id,
                edge_id=kante.id,
                actor=actor,
                detail={DETAIL_CLUSTER: cluster_id, DETAIL_MEMBER: concept_id},
            )
        )
        return CurationResult(entry=eintrag, edge=kante)

    def _kantenart_pruefen(self, kind: str) -> None:
        """Stellt sicher, dass die Kantenart konfiguriert ist (§7.7, Leitprinzip 12)."""
        erlaubt = (
            *self._settings.edge_kinds.structural,
            *self._settings.edge_kinds.semantic,
        )
        if kind not in erlaubt:
            raise CurationError(
                f"Unbekannte Kantenart '{kind}'. Konfiguriert sind: {', '.join(erlaubt)}. Eine "
                f"neue Art gehört in config/wissensgraph.yaml, nicht in den Code (§7.7)."
            )

    def _store_of_scope(self, scope: str) -> str:
        """Der Store eines Scopes.

        Raises:
            CurationError: Wenn der Scope nicht konfiguriert ist.
        """
        try:
            return self._settings.store_of_scope(scope)
        except KeyError as exc:
            bekannt = ", ".join(item.name for item in self._settings.scopes)
            raise CurationError(
                f"Unbekannter Scope '{scope}'. Konfiguriert sind: {bekannt}."
            ) from exc


def _kante_wiederherstellbar(edge: Edge) -> dict[str, Any]:
    """Die Felder einer Kante, aus denen sie sich wieder anlegen lässt (§17.3).

    Keine Inhalte: Eine Kante *ist* Struktur. ``reasoning`` ist die einzige Textzeile darin und
    stammt vom Modell, nicht aus einer Quelle — sie geht mit, weil ein wiederhergestellter
    Vorschlag ohne seine Begründung nicht mehr zu beurteilen wäre.
    """
    return {
        "from_store": edge.from_store,
        "from_id": edge.from_id,
        "to_store": edge.to_store,
        "to_id": edge.to_id,
        "kind": edge.kind,
        "weight": edge.weight,
        "confidence": edge.confidence,
        "reasoning": edge.reasoning,
        "resolved": edge.resolved,
        "generated_by": edge.generated_by,
        "curated": edge.curated,
    }


__all__ = [
    "CONTENT_FIELDS",
    "CURATABLE_FIELDS",
    "CurationError",
    "CurationResult",
    "CurationService",
    "NotFoundError",
]
