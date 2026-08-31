"""Der Kontrakt einer Quelle (§8.2).

Dies ist die Naht, an der eine neue Quelle andockt. §8.1 setzt den Maßstab: "Eine neue Quelle
wird eingebunden, ohne Kernlogik zu ändern. Was ein Entwickler beisteuert, ist eine Klasse, die
einen Kontrakt erfüllt, plus ein Eintrag in ``sources.yaml``." Damit das trägt, muss der Kontrakt
zwei Dinge zugleich leisten — er muss eng genug sein, dass der Kern nichts über die Quelle wissen
muss, und weit genug, dass eine Quelle mit anderen Fähigkeiten nicht daran vorbeigebaut wird.

Der zweite Punkt ist der Grund für :class:`AdapterCapabilities`. §8.2 Regel 3 legt fest: "Fehlt
eine Fähigkeit, ist das Flag ``false`` und die Methode wirft ``NotSupported``. Der ``SyncService``
fragt Flags ab, nicht Ausnahmen." Eine Ausnahme als Steuerfluss würde bedeuten, dass jeder Lauf
erst einmal etwas Verbotenes versucht; das Flag macht die Fähigkeit zu einer Eigenschaft, die man
*lesen* kann. Die Ausnahme bleibt trotzdem im Kontrakt — als Absicherung gegen einen Adapter, der
sein Flag falsch setzt.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import Field, field_validator

from wissensgraph.domain.base import DomainModel, unique_strings
from wissensgraph.domain.references import SourceReference, normalize_references

if TYPE_CHECKING:  # pragma: no cover — nur für die Typprüfung, zur Laufzeit nicht nötig
    from wissensgraph.config.sources import SourceConfig


class SourceError(RuntimeError):
    """Oberklasse aller Fehler, die aus einer Quelle stammen."""


class NotSupported(SourceError):
    """Eine Fähigkeit, die dieser Adapter nicht deklariert hat, wurde angefordert (§8.2 Regel 3).

    Der Aufrufer soll das nicht als Ausnahmefall behandeln müssen: Er kann die Fähigkeit vorher
    an :class:`AdapterCapabilities` ablesen. Wer trotzdem hier landet, hat ein Flag ignoriert —
    und genau das soll auffallen, statt in einem stillen ``pass`` zu verschwinden.
    """


class SourceObjectNotFound(SourceError):
    """Die Quelle kennt dieses Objekt nicht (mehr).

    Getrennt von einem sonstigen Fehler, weil es für den Aufrufer kein Fehler ist: ``fetch()``
    gibt in diesem Fall ``None`` zurück, und ein Lauf, der ein inzwischen gelöschtes Objekt
    nachladen wollte, macht einfach weiter.
    """


class SourceUnavailable(SourceError):
    """Die Quelle war vorübergehend nicht erreichbar, auch nach allen erlaubten Versuchen.

    Bewusst getrennt von einem Programmfehler: Ein Lauf darf daran scheitern, ohne dass der
    Cursor fortgeschrieben wird (§22.3). Beim nächsten Lauf wird es erneut versucht.
    """


class SourceDocument(DomainModel):
    """Quellneutrale Darstellung eines einzelnen Objekts (§8.2).

    Das DTO ist die Grenze: Diesseits steht die Quelle mit ihren Eigenheiten — Paginierung,
    Feldnamen, Auth —, jenseits steht der Kern, der nur noch Titel, Text und Verweise sieht. Ein
    Adapter kennt "weder ``concepts`` noch SQL noch Scopes" (§8.2 Regel 2); entsprechend fehlen
    hier Store, Scope, Typ und ID. Die entstehen erst beim Mapping aus der Konfiguration.
    """

    external_id: str = Field(
        min_length=1,
        description=(
            "Die ID des Objekts im Quellsystem. Muss über Läufe stabil sein (§22.3) — sie wird "
            "über das Präfix der Quelle zur Konzept-ID (§7.5)."
        ),
    )
    title: str | None = None
    description: str | None = None
    body: str | None = None
    resource: str | None = None
    tags: tuple[str, ...] = ()
    updated_at: datetime | None = None
    type_hint: str | None = Field(
        default=None,
        description="Überschreibt den 'default_type' der Quelle für dieses eine Objekt (§8.4).",
    )
    references: tuple[SourceReference, ...] = Field(
        default=(),
        description=(
            "Verweise dieses Objekts — noch nicht in interne IDs übersetzt (§8.5). Eine blanke "
            "ID gilt als gewöhnliche 'references'-Kante; ein Adapter, der die Art kennt, gibt "
            "sie mit an."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Quellspezifisch und vom Kern nicht interpretiert. Der Platz für alles, was ein "
            "Adapter mitgeben will, ohne dass der Kern es kennen muss."
        ),
    )

    _normalize_tags = field_validator("tags", mode="before")(unique_strings)
    _lift_references = field_validator("references", mode="before")(normalize_references)


class Cursor(DomainModel):
    """Opake, adapterdefinierte Fortschrittsmarke (§8.2).

    Der Kern liest den Inhalt nie. Er speichert ihn als JSONB und gibt ihn beim nächsten Lauf
    unverändert zurück — was darin steht, ist allein Sache des Adapters: ein Zeitstempel, ein
    Änderungsprotokoll-Offset, eine Seitennummer.
    """

    value: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """Ob der Cursor noch nichts markiert — gleichbedeutend mit "noch kein Lauf"."""
        return not self.value


class AdapterCapabilities(DomainModel):
    """Was ein Adapter kann (§8.2). Der Lauf fragt diese Flags ab, nicht die Ausnahmen."""

    incremental: bool = Field(
        default=False, description="Unterstützt Cursor-basierten Teilabgleich."
    )
    deletions: bool = Field(default=False, description="Kann gelöschte Objekte melden.")
    single_fetch: bool = Field(default=False, description="Kann ein Einzelobjekt gezielt holen.")
    references: bool = Field(default=False, description="Liefert ausgehende Referenzen mit.")


class HealthState(StrEnum):
    """Zustand eines Adapters (§8.3)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthStatus(DomainModel):
    """Das Ergebnis von :meth:`SourceAdapter.health`.

    §8.3: "Ein fehlerhafter Adapter deaktiviert sich selbst und erscheint in der UI als
    ``unhealthy``, ohne den Start zu verhindern." Deshalb ist das hier ein Rückgabewert und keine
    Ausnahme — ein Zustand, den man anzeigen kann.
    """

    state: HealthState = HealthState.HEALTHY
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Ob ein Lauf gegen diesen Adapter überhaupt sinnvoll ist."""
        return self.state is not HealthState.UNHEALTHY


@runtime_checkable
class SourceAdapter(Protocol):
    """Alles, was der Kern von einer Quelle verlangt (§8.2).

    Die vier verpflichtenden Eigenschaften aus §8.2, die kein Typsystem prüfen kann und die
    deshalb die Contract-Suite prüft (§22.3):

    1. :meth:`iter_documents` ist ein Generator und lädt nie den gesamten Bestand in den Speicher.
    2. Der Adapter kennt weder ``concepts`` noch SQL noch Scopes. Er liefert DTOs.
    3. Fehlt eine Fähigkeit, ist das Flag ``false`` und die Methode wirft :class:`NotSupported`.
    4. Der Adapter ist idempotent: derselbe Cursor liefert dasselbe Ergebnis.
    5. Rate-Limits und Retries behandelt der Adapter selbst, mit Werten aus seiner Config.
    """

    @property
    def name(self) -> str:
        """Der Adapterschlüssel — ``'confluence'``, ``'jira'``, … (§8.3)."""

    @property
    def capabilities(self) -> AdapterCapabilities:
        """Was dieser Adapter kann."""

    def configure(self, cfg: SourceConfig) -> None:
        """Übernimmt die aufgelöste Konfiguration dieser Quellinstanz (§8.4).

        Wird von der Registry genau einmal vor dem ersten Zugriff aufgerufen. Verbindungsdaten,
        Zeitlimits und Rate-Limits kommen ausschließlich von hier — im Adapter steht kein
        Literal für eine URL oder eine Schwelle (§6.1 Regel 1).
        """

    def health(self) -> HealthStatus:
        """Prüft, ob die Quelle erreichbar und die Konfiguration brauchbar ist."""

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Liefert die Objekte der Quelle, ohne Cursor alle, mit Cursor nur Geändertes.

        Muss ein Generator sein: Ein Bestand von hunderttausend Seiten darf nicht als Liste im
        Speicher entstehen.
        """

    def next_cursor(self) -> Cursor:
        """Die Fortschrittsmarke *nach* einer vollständig durchlaufenen Iteration.

        Erst hier — nicht schon währenddessen. §22.3: "Netzwerkfehler mitten in der Iteration
        lassen den Cursor unverändert." Wäre der Cursor schon nach der ersten Seite
        fortgeschrieben, ginge bei einem Abbruch der Rest des Bestands stillschweigend verloren.
        """

    def list_deleted(self, cursor: Cursor | None) -> Iterator[str]:
        """Die externen IDs der seit dem Cursor gelöschten Objekte.

        Raises:
            NotSupported: Wenn ``capabilities.deletions`` ``false`` ist.
        """

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Ein einzelnes Objekt, oder ``None``, wenn es die ID nicht (mehr) gibt.

        Raises:
            NotSupported: Wenn ``capabilities.single_fetch`` ``false`` ist.
        """
