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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from wissensgraph.domain.changes import ChangeEntry
from wissensgraph.domain.concepts import Concept
from wissensgraph.domain.edges import Edge, EdgeDraft
from wissensgraph.ports.runs import RunRepository, SourceCursorRepository


@dataclass(frozen=True)
class LexicalHit:
    """Ein Treffer der lexikalischen Suche (§12.4).

    Der Wert ist ein *Rang* und kein Maß: Volltextrang und Trigrammähnlichkeit sind zwei Skalen,
    die sich nicht sinnvoll addieren lassen. Die Zusammenführung geschieht deshalb über die
    Plätze (Reciprocal Rank Fusion, §12.4), und was hier steht, ist deren Ergebnis.
    """

    concept: Concept
    score: float


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

    def resolvable_ids(self, concept_ids: Sequence[str]) -> frozenset[str]:
        """Welche der angefragten IDs hier liegen **und keine Grabsteine** sind (§7.6).

        Der Unterschied zu :meth:`existing_ids` ist der Grabstein. §7.6 sagt für ein in der
        Quelle gelöschtes Objekt: "Kanten bleiben bestehen und werden als ``resolved = false``
        markiert." Eine Kante auf einen Grabstein ist also weiterhin da und weiterhin sichtbar —
        sie führt nur nicht mehr zu etwas Auffindbarem. Genau das ist die Aussage von
        ``resolved``.

        Wird das Objekt in der Quelle wiederhergestellt, wird die Kante beim nächsten Abgleich
        von selbst wieder auflösbar. Es braucht dafür keine gespeicherte Erinnerung daran, dass
        sie es einmal war.
        """

    def get_many(self, concept_ids: Sequence[str]) -> tuple[Concept, ...]:
        """Mehrere Konzepte in einer Abfrage — der Batch-Load eines Traversierungs-Hops (§12.1).

        §12.1 verlangt "je Zielstore ein Batch-Load der Konzepte (ein Query pro Store und Hop)".
        Ohne diese Methode entstünde je Knoten eine Abfrage, und die Store-Trennung würde teuer
        aussehen, obwohl sie es nicht ist.
        """

    def search_lexical(self, query: str, *, limit: int) -> tuple[LexicalHit, ...]:
        """Lexikalische Suche über Volltext und Trigramm (§12.4).

        Der Fallback, der immer verfügbar ist: Er braucht kein Embedding-Modell und funktioniert
        deshalb auch im ``personal``-Store ohne lokalen Modellserver (§11.5). Ein stiller
        Qualitätsverlust wäre die schlechtere Variante — der Aufrufer erfährt, dass er lexikalisch
        gesucht hat.
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

    def list_incoming(self, concept_id: str) -> tuple[Edge, ...]:
        """Alle auf ein Konzept zeigenden Kanten, die in *diesem* Store liegen.

        Die Kanten aus einem anderen Store sind damit nicht erfasst: Eine Notiz in ``personal``,
        die auf eine Confluence-Seite in ``shared`` zeigt, ist im geteilten Store nicht sichtbar
        und soll es nach §12.1 auch nicht sein. Die Gegenrichtung liefert
        :meth:`bridges_into`, aufgerufen auf dem Repository des *persönlichen* Stores.
        """

    def neighbourhood(self, concept_ids: Sequence[str]) -> tuple[Edge, ...]:
        """Alle Kanten dieses Stores, die eine der IDs berühren — ein- und ausgehend.

        Der Kantenschritt eines Traversierungs-Hops (§12.1, Schritt 2) in *einer* Abfrage für die
        gesamte Front statt einer je Knoten.
        """

    def bridges_into(self, *, to_store: str, to_ids: Sequence[str]) -> tuple[Edge, ...]:
        """Kanten aus diesem Store, die auf Konzepte eines anderen Stores zeigen.

        Damit wird die Rückrichtung einer Brücke rekonstruiert (§12.1): "Der geteilte Store weiß
        nicht, dass es persönliche Konzepte gibt. Die Rückrichtung wird beim Traversieren aus dem
        personal-Store rekonstruiert." Wer wissen will, welche persönlichen Notizen auf eine
        geteilte Seite zeigen, fragt also nicht den geteilten Store — er fragt den persönlichen.
        """

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
        """Gleicht ``resolved`` für alle Kanten *innerhalb* dieses Stores mit der Wirklichkeit ab.

        §8.5: "Zeigt eine Referenz auf ein noch nicht synchronisiertes Objekt, wird die Kante mit
        ``resolved = false`` angelegt und bei jedem Lauf erneut geprüft." Der Schritt steht
        absichtlich neben der Kernoperation: Er hängt nicht am Inhalt eines einzelnen Konzepts
        und darf deshalb auch dann laufen, wenn kein Hash sich geändert hat (§10.2 Regel 3).

        Der Abgleich geht in **beide** Richtungen. Ein Ziel kann verschwinden — nicht durch ein
        ``DELETE``, das es nicht gibt, sondern durch einen Grabstein (§7.6: "Kanten bleiben
        bestehen und werden als ``resolved = false`` markiert"). Eine Prüfung, die nur auflöst und
        nie zurücknimmt, behauptete nach der ersten Löschung dauerhaft das Gegenteil.

        Returns:
            Die Anzahl der Kanten, deren ``resolved`` sich dadurch geändert hat.
        """

    def foreign_targets(self) -> Mapping[str, frozenset[str]]:
        """Die Ziele aller Brückenkanten dieses Stores, gruppiert nach Zielstore (§12.1).

        Der erste Schritt der store-übergreifenden Auflösung: Erst wird gefragt, *wohin* dieser
        Store überhaupt zeigt, dann wird je fremdem Store einmal nachgesehen. Über die Grenze
        hinweg gibt es keinen Join — die Reihenfolge ist der Ersatz dafür.
        """

    def unresolved_targets(self) -> frozenset[str]:
        """Die Ziel-IDs aller noch nicht aufgelösten Kanten dieses Stores.

        Sie sind die offene Frage des Graphen: Verweise auf etwas, das es beim letzten Versuch
        nicht gab. Wo dieses Etwas einmal liegen wird, weiß beim Anlegen niemand — deshalb steht
        in ``to_store`` zunächst der eigene Store (§8.5).
        """

    def attach_to_store(self, *, to_store: str, to_ids: frozenset[str]) -> int:
        """Hängt unaufgelöste Kanten an den fremden Store, in dem ihr Ziel aufgetaucht ist.

        Der Schritt, ohne den eine Brücke nie zustande käme, die *vor* ihrem Ziel entstand — der
        Normalfall, wenn jemand eine Notiz schreibt, bevor die Quelle das erste Mal lief. Eine
        unaufgelöste Kante hat über ihren Zielstore nie etwas behauptet; ihn jetzt zu setzen
        nimmt also nichts zurück, sondern beantwortet die offene Frage.

        Gibt es zu demselben Ausgangspunkt bereits eine Kante mit dem neuen Tripel, bleibt die
        unaufgelöste stehen: Zwei gleiche Tripel lässt ``ux_edges_triple`` (§7.4) ohnehin nicht zu,
        und die bestehende ist die aussagekräftigere.

        Returns:
            Die Anzahl der umgehängten Kanten.
        """

    def set_foreign_resolution(self, *, to_store: str, resolvable: frozenset[str]) -> int:
        """Setzt ``resolved`` für alle Brückenkanten in einen bestimmten fremden Store.

        Args:
            to_store: Der Zielstore, um dessen Kanten es geht.
            resolvable: Die dort tatsächlich auffindbaren IDs. Eine Kante, deren Ziel nicht
                darunter ist, wird auf ``resolved = false`` gesetzt — auch wenn sie es vorher
                war.

        Returns:
            Die Anzahl der Kanten, deren ``resolved`` sich dadurch geändert hat.
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
