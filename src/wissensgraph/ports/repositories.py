"""Ports der Persistenz (§4.2, Leitprinzip 13).

Die Dienste sprechen ausschließlich mit diesen Protokollen; welche Datenbank dahinter liegt,
wissen sie nicht. Zwei Dinge folgen daraus, die für dieses System wichtiger sind als die reine
Testbarkeit:

* **Jedes Repository gehört zu genau einem Store.** Es gibt keine Methode, die einen Store als
  Parameter nimmt. Ein Dienst, der auf ``personal`` schreiben will, muss sich eine
  Arbeitseinheit für ``personal`` geben lassen — er kann nicht versehentlich in derselben
  Operation nach ``shared`` schreiben (§20.1, Anwendungsebene).
* **Der Schnitt liegt bei der Arbeitseinheit, nicht bei der einzelnen Methode.** §10.2 Regel 5
  verlangt, dass Konzept, Kanten und ``change_log`` gemeinsam geschrieben werden. Ein
  Repository, das für sich committet, könnte das nicht einhalten.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from wissensgraph.domain.changes import ChangeEntry
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge, EdgeDraft
from wissensgraph.ports.runs import RunRepository, SourceCursorRepository


@runtime_checkable
class ConceptRepository(Protocol):
    """Zugriff auf die Konzepte genau eines Stores."""

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def get(self, concept_id: str) -> Concept | None:
        """Das Konzept zu einer ID, oder ``None``, wenn es die ID hier nicht gibt."""

    def exists(self, concept_id: str) -> bool:
        """Ob ein Konzept mit dieser ID in diesem Store liegt.

        Grundlage des ``resolved``-Flags einer Kante (§8.5) — bewusst getrennt von :meth:`get`,
        weil dafür kein einziges Inhaltsfeld gelesen werden muss.
        """

    def existing_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        """Welche der angefragten IDs es in diesem Store gibt.

        Eine Abfrage für alle Referenzen eines Konzepts statt einer je Referenz: Ein Body mit
        dreißig Verweisen soll nicht dreißig Roundtrips auslösen.
        """

    def save(self, concept: Concept) -> None:
        """Legt ein Konzept an oder überschreibt es vollständig.

        Der übergebene Zustand ist das Ergebnis der Regeln aus §10.2 und §10.4; das Repository
        entscheidet nichts mehr, es schreibt nur.
        """


@runtime_checkable
class EdgeRepository(Protocol):
    """Zugriff auf die Kanten genau eines Stores.

    Ein Kanten-Repository schreibt nur Kanten, deren ``from_store`` sein eigener Store ist. Die
    Gegenrichtung einer Brücke gehört in den anderen Store und damit in dessen Arbeitseinheit.
    """

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def list_outgoing(self, concept_id: str) -> tuple[Edge, ...]:
        """Alle von einem Konzept ausgehenden Kanten."""

    def replace_generated(
        self, *, from_id: str, generated_by: Sequence[str], drafts: Sequence[EdgeDraft]
    ) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
        """Gleicht die von bestimmten Erzeugern angelegten Kanten eines Konzepts ab (§10.4).

        Angefasst werden ausschließlich Kanten, deren ``generated_by`` in ``generated_by`` steht
        und die ``curated = false`` sind: "Kanten mit ``curated = true`` bleiben unangetastet,
        Kanten mit ``generated_by`` dürfen von Läufen ersetzt werden."

        ``generated_by`` ist eine *Menge* und kein einzelner Wert, weil ein Konzept Verweise aus
        mehreren Quellen zugleich hat: aus seinem Fließtext (``code:body-reference``) und aus der
        Meldung der Quelle (``code:source-reference``, §8.5). Mit zwei getrennten Aufrufen wäre
        der Abgleich nicht mehr atomar — ein Verweis, der von der einen Herkunft in die andere
        wandert, verschwände zwischen ihnen für die Dauer eines Laufs.

        Jeder Entwurf trägt sein eigenes ``generated_by``; der Parameter sagt nur, welche
        bestehenden Kanten dieser Aufruf ersetzen darf.

        Returns:
            Die hinzugefügten und die entfernten Kanten — Grundlage der Journaleinträge
            ``edge_added`` und ``edge_removed``.
        """

    def refresh_resolution(self) -> int:
        """Prüft ungelöste Kanten erneut und setzt ``resolved``, wo das Ziel inzwischen da ist.

        §8.5: "Zeigt eine Referenz auf ein noch nicht synchronisiertes Objekt, wird die Kante mit
        ``resolved = false`` angelegt und bei jedem Lauf erneut geprüft." Der Schritt steht
        absichtlich neben der Kernoperation: Er hängt nicht am Inhalt eines einzelnen Konzepts
        und darf deshalb auch dann laufen, wenn kein Hash sich geändert hat (§10.2 Regel 3).

        Returns:
            Die Anzahl der Kanten, die dadurch auflösbar wurden.
        """


@runtime_checkable
class ChangeLogRepository(Protocol):
    """Schreibender und lesender Zugriff auf das Änderungsjournal eines Stores (§7.4)."""

    @property
    def store(self) -> str:
        """Der Store, für den dieses Repository zuständig ist."""

    def append(self, entry: ChangeEntry) -> None:
        """Hängt einen Eintrag an."""

    def entries_for(self, concept_id: str) -> tuple[ChangeEntry, ...]:
        """Alle Einträge zu einem Konzept, neueste zuerst."""

    def has_open_curation_conflict(self, *, concept_id: str, source_content_hash: str) -> bool:
        """Ob genau dieser Konflikt schon vermerkt ist.

        Ein Kurationskonflikt ist ein Zustand, kein Ereignis: Solange die Quelle denselben,
        abgewehrten Inhalt liefert, besteht er bei jedem Lauf fort. Ohne diese Prüfung entstünde
        je Lauf eine neue Zeile und die Kurationsliste (§17.2) wäre nach einer Woche unlesbar.
        """


class UnitOfWork(Protocol):
    """Eine Arbeitseinheit auf genau einem Store — der Träger von §10.2 Regel 5.

    Verwendung als Kontextmanager: Beim regulären Verlassen wird festgeschrieben, bei einer
    Ausnahme zurückgerollt. Konzept, Kanten und Journal sind damit entweder alle drei gespeichert
    oder keines von ihnen.
    """

    @property
    def store(self) -> str:
        """Der Store dieser Arbeitseinheit."""

    @property
    def concepts(self) -> ConceptRepository:
        """Das Konzept-Repository dieses Stores."""

    @property
    def edges(self) -> EdgeRepository:
        """Das Kanten-Repository dieses Stores."""

    @property
    def changes(self) -> ChangeLogRepository:
        """Das Journal-Repository dieses Stores."""

    @property
    def runs(self) -> RunRepository:
        """Das Lauf-Repository dieses Stores (§7.4)."""

    @property
    def cursors(self) -> SourceCursorRepository:
        """Das Cursor-Repository dieses Stores (§7.4)."""

    def commit(self) -> None:
        """Schreibt alles Angesammelte fest."""

    def rollback(self) -> None:
        """Verwirft alles Angesammelte."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Erzeugt Arbeitseinheiten zu einem Store-Namen.

    Die Dienste bekommen diese Fabrik statt einer Registry oder gar einer Engine. Der Unterschied
    ist klein, aber er hält den Zugriff eng: Ein Dienst kann eine Transaktion auf einem
    *benannten* Store beginnen — er kann keine Verbindung aufbauen und keinen DSN lesen (§20.1).
    """

    def __call__(self, store: str) -> UnitOfWork:
        """Eine noch nicht geöffnete Arbeitseinheit für einen Store.

        Raises:
            KeyError: Wenn der Store nicht konfiguriert ist.
        """
