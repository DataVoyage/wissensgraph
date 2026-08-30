"""Der Embedding-Lauf (§13.1).

Vier Regeln, und jede von ihnen ist eine Entscheidung gegen etwas Naheliegendes:

**Eingebettet wird ``title`` + ``description``, nicht der ``body``.** §13.1 begründet das: Der
Body "würde lange Dokumente überproportional gewichten". Eine dreißigseitige Betriebsanleitung
und eine Notiz von vier Zeilen sollen im selben Raum vergleichbar sein — sonst gruppiert sich am
Ende die Textlänge und nicht das Thema.

**Fehlt die ``description``, wird sie erzeugt — einmal.** Danach ist sie ein gewöhnliches Feld.
Der ``content_hash`` bleibt dabei unberührt, und das ist wichtig genug für einen eigenen Absatz:
Er ist der Hash des *Quellinhalts* (§10.3) und beantwortet die Frage "hat sich die Quelle
geändert?". Eine hier erzeugte Beschreibung ist keine Quelländerung. Würde sie den Hash
verschieben, meldete der nächste Sync eine Änderung, überschriebe die Beschreibung mit ``NULL``,
und der nächste Embedding-Lauf erzeugte sie neu — ein Kreislauf, der bei jedem Lauf Token kostet
und nichts verbessert.

**Neu eingebettet wird nur bei geändertem Hash.** Ein zweiter Lauf über einen unveränderten
Bestand kostet nichts. Das ist der Grund, warum ``concept_embeddings.source_hash`` überhaupt
existiert.

**Ein verweigerter Provider beendet den Lauf nicht.** §11.5: "Ohne lokalen Modellserver bleiben
persönliche Konzepte ohne Embedding. Das ist kein Fehler, sondern der Preis von Leitprinzip 2."
Der Lauf endet erfolgreich und sagt in seiner Statistik, was er nicht getan hat.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.policies import ProviderNotAllowedError
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.models import BudgetExceededError, ModelError, ModelRouter, PromptSpec
from wissensgraph.ports.repositories import UnitOfWork, UnitOfWorkFactory

_log = get_logger(__name__)

#: Was zwischen Titel und Beschreibung steht (§13.1: "``title`` + ``\\n\\n`` + ``description``").
_TRENNER = "\n\n"

#: Der Prompt der Beschreibungserzeugung (§13.1, Task ``summarization``). Er verlangt ausdrücklich
#: *einen* Absatz: Die Beschreibung geht in das Embedding ein, und ein langer Text verschöbe genau
#: die Gewichtung, wegen der der ``body`` gar nicht erst eingeht.
_ZUSAMMENFASSUNG_SYSTEM = (
    "Du fasst Dokumente für einen Wissensgraphen zusammen. Antworte mit einem einzigen Absatz "
    "von höchstens drei Sätzen, ohne Vorrede, ohne Aufzählung, in der Sprache des Dokuments. "
    "Beschreibe, worum es geht — nicht, dass es ein Dokument ist."
)

#: Wie viele Zeichen des ``body`` in die Zusammenfassung gehen. Der Deckel begrenzt die Kosten und
#: kostet wenig Aussage: Worum ein Dokument geht, steht fast immer am Anfang.
_ZUSAMMENFASSUNG_ZEICHEN = 4000


@dataclass
class EmbeddingReport:
    """Was ein Embedding-Lauf getan hat — die Zähler für ``runs.stats`` (§7.4)."""

    scope: str
    store: str
    model_key: str = ""
    considered: int = 0
    embedded: int = 0
    described: int = 0
    cached: int = 0
    skipped_empty: int = 0
    skipped_policy: int = 0
    budget_exceeded: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Serialisierbare Form für Lauf-Statistik, CLI und API — nur Zahlen und Namen (§21.1)."""
        return {
            "scope": self.scope,
            "store": self.store,
            "model_key": self.model_key,
            "considered": self.considered,
            "embedded": self.embedded,
            "described": self.described,
            "cached": self.cached,
            "skipped_empty": self.skipped_empty,
            "skipped_policy": self.skipped_policy,
            "budget_exceeded": self.budget_exceeded,
            "errors": list(self.errors),
        }


class EmbeddingService:
    """Berechnet und speichert die Vektoren eines Scopes (§13.1)."""

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

    def run(
        self,
        *,
        scope: str,
        rebuild: bool = False,
        run_id: UUID | None = None,
        actor: str = defaults.ACTOR_EMBED,
    ) -> EmbeddingReport:
        """Bettet alles ein, was im Scope fehlt oder veraltet ist.

        Args:
            scope: Der zu bearbeitende Scope; er bestimmt auch den Store (§7.3).
            rebuild: Alles neu einbetten, auch was aktuell aussieht — ``wg embed --rebuild``
                nach einem Modellwechsel bei gleicher Dimension (§11.7).
            run_id: Der Lauf, zu dem die Modellaufrufe gehören.
            actor: Wer die Änderung verantwortet.

        Returns:
            Den Bericht. Auch ein Lauf, der wegen der Store-Policy nichts tun durfte, ist ein
            erfolgreicher Lauf — er sagt es nur in seinen Zahlen.
        """
        store = self._settings.store_of_scope(scope)
        route = self._router.describe(defaults.TASK_EMBEDDING)
        bericht = EmbeddingReport(scope=scope, store=store, model_key=route.model_key)

        offen = self._offene_ids(scope=scope, store=store, model_key=route.model_key, alle=rebuild)
        bericht.considered = len(offen)
        if not offen:
            _log.info("embedding.nichts_zu_tun", scope=scope, model_key=route.model_key)
            return bericht

        try:
            self._verarbeiten(
                ids=offen,
                store=store,
                model_key=route.model_key,
                batch=max(1, route.batch_size),
                run_id=run_id,
                actor=actor,
                bericht=bericht,
            )
        except ProviderNotAllowedError as exc:
            # §11.5: kein Fehler, sondern der Preis von Leitprinzip 2 — und er wird beziffert.
            bericht.skipped_policy = bericht.considered - bericht.embedded
            _log.warning("embedding.policy_verweigert", scope=scope, grund=str(exc))
        except BudgetExceededError as exc:
            # §24, Stufe 7: "ein Budgetüberschritt beendet den Lauf sauber mit Teilergebnis".
            bericht.budget_exceeded = True
            _log.warning("embedding.budget_erschoepft", scope=scope, grund=str(exc))

        _log.info("embedding.beendet", **bericht.as_dict())
        return bericht

    # -- innere Abläufe ---------------------------------------------------------

    def _offene_ids(self, *, scope: str, store: str, model_key: str, alle: bool) -> tuple[str, ...]:
        """Welche Konzepte dieser Lauf anfassen muss."""
        with self._unit_of_work(store) as uow:
            if alle:
                return tuple(concept.id for concept in uow.concepts.in_scope(scope))
            return uow.embeddings.outdated(model_key=model_key, scope=scope)

    def _verarbeiten(
        self,
        *,
        ids: Sequence[str],
        store: str,
        model_key: str,
        batch: int,
        run_id: UUID | None,
        actor: str,
        bericht: EmbeddingReport,
    ) -> None:
        """Arbeitet die offenen Konzepte in Bündeln ab.

        Die Bündelgröße kommt aus der Route und nicht aus einer eigenen Zahl: Der Router bündelt
        seine Modellaufrufe ohnehin danach, und zwei verschiedene Bündelgrößen übereinander
        ergäben nur Datenbanktransaktionen, die zu keinem Aufruf passen.
        """
        for beginn in range(0, len(ids), batch):
            stapel = tuple(ids[beginn : beginn + batch])
            self._stapel_verarbeiten(
                ids=stapel,
                store=store,
                model_key=model_key,
                run_id=run_id,
                actor=actor,
                bericht=bericht,
            )

    def _stapel_verarbeiten(
        self,
        *,
        ids: Sequence[str],
        store: str,
        model_key: str,
        run_id: UUID | None,
        actor: str,
        bericht: EmbeddingReport,
    ) -> None:
        """Ein Bündel: Texte bauen (ggf. mit erzeugter Beschreibung), einbetten, ablegen."""
        with self._unit_of_work(store) as uow:
            konzepte = uow.concepts.get_many(tuple(ids))

        texte: list[str] = []
        gewaehlt: list[Concept] = []
        for concept in konzepte:
            angereichert = self._beschreibung_sichern(
                concept, store=store, run_id=run_id, actor=actor, bericht=bericht
            )
            text = _einbettungstext(angereichert)
            if not text:
                # Ein Konzept ohne Titel und ohne Beschreibung hat nichts, was sich einbetten
                # ließe. Ein Vektor daraus wäre eine Aussage über nichts.
                bericht.skipped_empty += 1
                continue
            texte.append(text)
            gewaehlt.append(angereichert)

        if not texte:
            return

        ergebnis = self._router.embed(defaults.TASK_EMBEDDING, texte, store=store, run_id=run_id)
        bericht.cached += ergebnis.cached

        with self._unit_of_work(store) as uow:
            for concept, vektor in zip(gewaehlt, ergebnis.vectors, strict=True):
                uow.embeddings.save(
                    concept_id=concept.id,
                    model_key=model_key,
                    vector=vektor,
                    source_hash=concept.content_hash,
                )
                bericht.embedded += 1

    def _beschreibung_sichern(
        self,
        concept: Concept,
        *,
        store: str,
        run_id: UUID | None,
        actor: str,
        bericht: EmbeddingReport,
    ) -> Concept:
        """Erzeugt eine fehlende ``description`` aus dem ``body`` (§13.1).

        Der Lauf hört nicht auf, wenn das Modell dabei versagt: Eine fehlende Beschreibung ist
        eine schlechtere Grundlage für das Embedding, aber der Titel allein trägt weiter. Ein
        abgebrochener Lauf wegen eines einzelnen misslungenen Satzes wäre der schlechtere Tausch.
        """
        if concept.description or not concept.body:
            return concept

        try:
            antwort = self._router.complete(
                defaults.TASK_SUMMARIZATION,
                prompt=PromptSpec(
                    system=_ZUSAMMENFASSUNG_SYSTEM,
                    user=(concept.title or "") + _TRENNER + concept.body[:_ZUSAMMENFASSUNG_ZEICHEN],
                ),
                store=store,
                run_id=run_id,
            )
        except (ProviderNotAllowedError, BudgetExceededError):
            raise
        except ModelError as exc:
            bericht.errors = (*bericht.errors, f"{concept.id}: {type(exc).__name__}")
            _log.warning("embedding.beschreibung_gescheitert", concept_id=concept.id)
            return concept

        beschreibung = antwort.raw.strip()
        if not beschreibung:
            return concept

        route = self._router.describe(defaults.TASK_SUMMARIZATION)
        jetzt = self._clock()
        # ``content_hash`` bleibt stehen — er beschreibt den Quellinhalt, nicht die Zeile.
        angereichert = concept.model_copy(
            update={
                "description": beschreibung,
                "generated_by": route.generated_by,
                "generated_at": jetzt,
                "updated_at": jetzt,
            }
        )
        with self._unit_of_work(store) as uow:
            self._speichern(uow, angereichert, actor=actor, run_id=run_id)
        bericht.described += 1
        return angereichert

    def _speichern(
        self, uow: UnitOfWork, concept: Concept, *, actor: str, run_id: UUID | None
    ) -> None:
        """Schreibt das angereicherte Konzept mit Journaleintrag (§7.4)."""
        from wissensgraph.domain.changes import ChangeEntry, ChangeType

        uow.concepts.save(concept)
        uow.changes.append(
            ChangeEntry(
                change_type=ChangeType.UPDATED,
                concept_id=concept.id,
                actor=actor,
                run_id=run_id,
                detail={"feld": "description", "generated_by": concept.generated_by},
            )
        )


def _einbettungstext(concept: Concept) -> str:
    """``title`` + ``\\n\\n`` + ``description`` (§13.1); leer, wenn beides fehlt."""
    teile = [teil.strip() for teil in (concept.title, concept.description) if teil]
    return _TRENNER.join(teil for teil in teile if teil)


__all__ = ["EmbeddingReport", "EmbeddingService"]
