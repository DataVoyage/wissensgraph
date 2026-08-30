"""Die Kernoperation als reine Funktion (§10.2, §10.4).

§10.2 beschreibt ``upsert_concept()`` mit fünf Regeln. Vier davon sind Entscheidungen über
Werte und brauchen keine Datenbank; nur die fünfte — "der Aufruf ist transaktional" — ist eine
Aussage über Infrastruktur. Dieses Modul enthält deshalb die ersten vier als reine Funktion:

1. Identität ist die ID.
2. ``content_hash`` entscheidet, ob überhaupt geschrieben wird.
3. Bei Gleichheit: kein UPDATE, kein ``change_log``, kein Re-Embedding.
4. Kuratierte Felder werden von der Quelle nicht überschrieben; der Konflikt landet als
   ``curation_conflict`` im ``change_log``.

Regel 5 setzt :mod:`wissensgraph.services.concepts` um.

**Wie die beiden Kurationsregeln zusammenpassen.** §10.4 sagt für ``title``, ``description``,
``body`` und ``resource``: "Quelle gewinnt immer". §10.2 Regel 4 sagt: "Kuratierte Felder werden
von der Quelle nicht überschrieben". Das ist kein Widerspruch, sondern eine Fallunterscheidung
nach Konzepttyp:

* Bei einem **quellgespiegelten** Typ (``source_mirrored: true``, §7.2) sind die Inhaltsfelder
  für UI, API und Agent ohnehin schreibgeschützt — es *kann* dort keine kuratierte Fassung des
  Bodys geben. Kuratierbar sind nur ``status``, ``tags`` und die Verifikationsfelder. Für sie
  gilt die Tabelle aus §10.4 wörtlich.
* Bei einem **nicht gespiegelten** Typ (einer Notiz, einem Brücken-Konzept) kann sehr wohl ein
  Mensch den Inhalt geschrieben haben. Versucht eine Quelle später, dasselbe Konzept zu
  überschreiben, greift Regel 4: Der kuratierte Inhalt bleibt stehen, und der Vorgang wird
  vermerkt statt stillschweigend verworfen.

Ein lokaler Schreibvorgang — UI oder Agent, erkennbar am fehlenden ``source_name`` — ist von
alldem nicht betroffen: Wer von Hand schreibt, überschreibt seine eigene Kuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from wissensgraph.domain.concepts import Concept, ConceptDraft, ConceptStatus

#: Felder, die den Inhalt eines Konzepts ausmachen. Nur sie unterliegen dem Schutz aus Regel 4;
#: ``resource`` gehört dazu, weil §10.4 es in dieselbe Zeile stellt.
CONTENT_FIELDS: tuple[str, ...] = ("title", "description", "body", "resource")


class UpsertOutcome(StrEnum):
    """Das Ergebnis einer Kernoperation — die Grundlage der Lauf-Statistik (§10.2)."""

    UNCHANGED = "unchanged"
    CREATED = "created"
    UPDATED = "updated"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class UpsertPlan:
    """Was aus einem Entwurf folgt, bevor irgendetwas geschrieben wurde.

    Attributes:
        outcome: Wie der Vorgang zu zählen ist.
        concept: Der zu schreibende Zustand — ``None`` genau dann, wenn nichts zu tun ist.
        held_back: Namen der Felder, die die Kuration gegen die Quelle behauptet hat.
        verification_reset: Ob eine bestehende Bestätigung durch die Inhaltsänderung hinfällig
            wurde (§10.4).
    """

    outcome: UpsertOutcome
    concept: Concept | None = None
    held_back: tuple[str, ...] = field(default_factory=tuple)
    verification_reset: bool = False

    @property
    def writes(self) -> bool:
        """Ob dieser Plan die Datenbank berührt."""
        return self.concept is not None


def plan_upsert(
    *,
    existing: Concept | None,
    draft: ConceptDraft,
    store: str,
    source_mirrored: bool,
    now: datetime,
) -> UpsertPlan:
    """Entscheidet, was aus einem Entwurf wird — ohne jede Datenbank.

    Args:
        existing: Der gespeicherte Stand oder ``None``, wenn die ID neu ist (Regel 1).
        draft: Der vorgeschlagene Stand.
        store: Der Store, in dem geschrieben wird. Er kommt von der Registry, nie aus dem
            Entwurf (§20.1).
        source_mirrored: Ob der Konzepttyp quellgespiegelt ist (§7.2).
        now: Zeitpunkt des Vorgangs. Als Parameter und nicht als ``datetime.now()`` im Rumpf,
            damit ein Test das Ergebnis vollständig bestimmen kann.

    Returns:
        Der Plan; bei unverändertem Inhalt mit ``outcome = UNCHANGED`` und ohne Konzept.
    """
    if existing is None:
        return UpsertPlan(
            outcome=UpsertOutcome.CREATED,
            concept=_neues_konzept(draft=draft, store=store, now=now),
        )

    # Regel 2 und 3: Der Hash allein entscheidet, ob überhaupt geschrieben wird. Eine reine
    # Tag- oder Statusänderung der Quelle bleibt damit folgenlos — das ist der Preis dafür,
    # dass ein unveränderter Lauf keine Kosten verursacht (§10.3).
    #
    # Die eine Ausnahme ist die Rückkehr aus dem Tombstone: Liefert eine Quelle ein Objekt
    # wieder aus, das sie zuvor als gelöscht gemeldet hatte, ist das eine Aussage über seine
    # *Existenz* und nicht über seinen Inhalt — und die geht am Hash vorbei. Ohne diese Zeile
    # bliebe ein wiederhergestelltes Objekt für immer ein Grabstein, weil sein Inhalt sich
    # nicht geändert hat (§7.6).
    if existing.content_hash == draft.content_hash and not _kehrt_zurueck(existing, draft):
        return UpsertPlan(outcome=UpsertOutcome.UNCHANGED)

    return _zusammenfuehren(
        existing=existing, draft=draft, source_mirrored=source_mirrored, now=now
    )


def _kehrt_zurueck(existing: Concept, draft: ConceptDraft) -> bool:
    """Ob eine Quelle ein zuvor als gelöscht gemeldetes Objekt wieder ausliefert (§7.6).

    Nur eine *Quelle* kann das: Ein lokaler Schreibvorgang ohne ``source_name`` sagt nichts
    darüber aus, ob das Quellobjekt wieder da ist. Und der Entwurf darf nicht selbst ein
    Tombstone sein — sonst wäre eine erneute Löschmeldung eine Rückkehr.
    """
    return (
        existing.status is ConceptStatus.TOMBSTONE
        and draft.is_from_source
        and draft.status is not ConceptStatus.TOMBSTONE
    )


def _neues_konzept(*, draft: ConceptDraft, store: str, now: datetime) -> Concept:
    """Baut das Konzept zu einem noch unbekannten Entwurf."""
    return Concept(
        **draft.model_dump(exclude={"references"}),
        store=store,
        content_hash=draft.content_hash,
        created_at=now,
        updated_at=now,
    )


def _zusammenfuehren(
    *, existing: Concept, draft: ConceptDraft, source_mirrored: bool, now: datetime
) -> UpsertPlan:
    """Verschmilzt einen geänderten Entwurf mit dem gespeicherten Stand (§10.4)."""
    werte = existing.model_dump()
    held_back: list[str] = []

    inhalt_geschuetzt = existing.curated and draft.is_from_source and not source_mirrored

    for name in CONTENT_FIELDS:
        neu = getattr(draft, name)
        if getattr(existing, name) == neu:
            continue
        if inhalt_geschuetzt:
            held_back.append(name)
        else:
            werte[name] = neu

    # Ohne den Inhaltsschutz übernimmt die Quelle auch die beschreibenden Nebenfelder. Mit ihm
    # bleibt der kuratierte Stand vollständig stehen — ein halb übernommenes Konzept wäre
    # schlechter als beide Ausgangszustände.
    if not inhalt_geschuetzt:
        werte["audience"] = draft.audience
        werte["stale_after"] = draft.stale_after
        werte["scope"] = draft.scope
        werte["type"] = draft.type
        werte["generated_by"] = draft.generated_by
        werte["generated_at"] = draft.generated_at

    werte["source_name"] = draft.source_name
    werte["external_id"] = draft.external_id
    werte["source_updated_at"] = draft.source_updated_at

    # §10.4: Tags sind die Vereinigung aus Quell-Tags und kuratierten Tags. Kuration entfernt
    # hier nichts — ein von Hand gesetztes Tag verschwindet nicht, weil die Quelle es nicht kennt.
    werte["tags"] = tuple(dict.fromkeys((*existing.tags, *draft.tags)))

    status, status_zurueckgehalten = _status_bestimmen(existing=existing, draft=draft)
    werte["status"] = status
    if status_zurueckgehalten:
        held_back.append("status")

    werte["curated"] = existing.curated or draft.curated
    werte["content_hash"] = existing.content_hash if inhalt_geschuetzt else draft.content_hash

    # §10.4: Eine Bestätigung gilt für einen bestimmten Inhaltsstand. Maßgeblich ist der Hash des
    # *gespeicherten* Inhalts: Hat die Kuration die Quelländerung abgewehrt, steht der bestätigte
    # Stand unverändert da, und die Bestätigung bleibt gedeckt.
    verification_reset = existing.is_verified and werte["content_hash"] != existing.content_hash
    if verification_reset:
        werte["verified_by"] = None
        werte["verified_at"] = None

    werte["updated_at"] = now
    zusammengefuehrt = Concept.model_validate(werte)

    outcome = UpsertOutcome.CONFLICT if held_back else UpsertOutcome.UPDATED
    if not _unterscheidet_sich(existing, zusammengefuehrt):
        # Der Entwurf weicht ab, aber alles, worin er abweicht, hat die Kuration behauptet. Ein
        # UPDATE, das nur ``updated_at`` fortschreibt, wäre eine Änderung ohne Änderung — es
        # würde bei jedem Lauf eine Zeile im Journal erzeugen und den Inhalt doch nie berühren.
        return UpsertPlan(outcome=outcome, held_back=tuple(held_back))

    return UpsertPlan(
        outcome=outcome,
        concept=zusammengefuehrt,
        held_back=tuple(held_back),
        verification_reset=verification_reset,
    )


def _unterscheidet_sich(existing: Concept, merged: Concept) -> bool:
    """Ob sich zwei Zustände in mehr als dem Änderungszeitpunkt unterscheiden."""
    return existing.model_dump(exclude={"updated_at"}) != merged.model_dump(exclude={"updated_at"})


def _status_bestimmen(*, existing: Concept, draft: ConceptDraft) -> tuple[ConceptStatus, bool]:
    """Der Status nach §10.4: "Kuration gewinnt, außer die Quelle meldet Löschung".

    Returns:
        Den geltenden Status und ob dafür ein Wunsch der Quelle zurückgewiesen wurde.
    """
    if draft.status == ConceptStatus.TOMBSTONE:
        return ConceptStatus.TOMBSTONE, False
    if existing.status == draft.status:
        return draft.status, False
    if existing.curated and draft.is_from_source:
        return existing.status, True
    return draft.status, False
