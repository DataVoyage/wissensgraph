"""Die Adapter-Registry (§8.3).

Sie ist der Ort, an dem sich entscheidet, ob §8.1 stimmt: "Eine neue Quelle wird eingebunden,
ohne Kernlogik zu ändern." Genau dafür gibt es drei Wege, einen Adapter zu finden, und eine
klare Rangfolge zwischen ihnen:

1. **``class:`` in ``sources.yaml``** — ein Modulpfad ``paket.modul:Klasse``. Der spezifischste
   Weg, deshalb der stärkste. Er braucht keine Installation als Paket und keinen Entry Point;
   damit wird ein Adapter allein über einen Config-Eintrag aktiv.
2. **Entry Point** unter der Gruppe ``wissensgraph.adapters`` — der Weg für ein installiertes
   Paket, das sich selbst anmeldet.
3. **Mitgeliefert** — Confluence, Jira, Fixture. Sie sind eingebaut, damit ein frisch
   ausgechecktes Repository ohne Installationsschritt läuft.

Zwei Fehlerarten, die leicht verwechselt werden, sind hier bewusst getrennt:

* **Nicht auffindbar** ist ein Startfehler (§6.5, letzter Punkt). Eine Konfiguration, die auf
  einen Adapter zeigt, den es nicht gibt, ist falsch — und zwar jetzt und nicht erst beim Lauf.
* **Auffindbar, aber kaputt** ist keiner. §8.3: "Ein fehlerhafter Adapter deaktiviert sich selbst
  und erscheint in der UI als ``unhealthy``, ohne den Start zu verhindern." Eine unerreichbare
  Confluence-Instanz darf nicht verhindern, dass die Jira-Quelle synchronisiert wird.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points

from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.sources import SourceConfig, SourcesConfig
from wissensgraph.infrastructure.adapters.confluence import ConfluenceAdapter
from wissensgraph.infrastructure.adapters.fixture import FixtureAdapter
from wissensgraph.infrastructure.adapters.jira import JiraAdapter
from wissensgraph.infrastructure.adapters.sap_docs import SapDocsAdapter
from wissensgraph.nebenlaeufig import parallel
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import HealthState, HealthStatus, SourceAdapter

_log = get_logger(__name__)

#: Fabrik eines Adapters: ohne Argumente aufrufbar, liefert eine noch nicht konfigurierte Instanz.
AdapterFactory = Callable[[], SourceAdapter]

#: Die mitgelieferten Adapter. Ein gleichnamiger Entry Point verdrängt sie — so lässt sich eine
#: eingebaute Umsetzung ersetzen, ohne sie zu entfernen.
BUILTIN_ADAPTERS: dict[str, AdapterFactory] = {
    defaults.ADAPTER_CONFLUENCE: ConfluenceAdapter,
    defaults.ADAPTER_JIRA: JiraAdapter,
    defaults.ADAPTER_FIXTURE: FixtureAdapter,
    defaults.ADAPTER_SAP_DOCS: SapDocsAdapter,
}


class AdapterNotFound(ConfigValidationError):
    """Ein in ``sources.yaml`` genannter Adapter ist nirgends auffindbar (§6.5)."""


@dataclass(frozen=True)
class RegisteredSource:
    """Eine Quelle nach dem Registrierungsversuch — mit Adapter oder mit Begründung.

    Beides zusammen in einem Objekt, weil die UI beides braucht: Eine ausgefallene Quelle soll
    mit ihrem Namen und ihrem Grund erscheinen und nicht schlicht fehlen (§8.3).
    """

    config: SourceConfig
    adapter: SourceAdapter | None
    health: HealthStatus

    @property
    def name(self) -> str:
        """Der Name der Quellinstanz."""
        return self.config.name

    @property
    def usable(self) -> bool:
        """Ob ein Lauf gegen diese Quelle sinnvoll ist."""
        return self.adapter is not None and self.health.usable

    def require(self) -> SourceAdapter:
        """Der Adapter, oder ein Fehler mit dem Grund seines Ausfalls.

        Raises:
            RuntimeError: Wenn die Quelle nicht benutzbar ist.
        """
        if self.adapter is None or not self.health.usable:
            raise RuntimeError(
                f"Quelle '{self.name}' ist nicht benutzbar: {self.health.detail} "
                f"(Zustand '{self.health.state}')."
            )
        return self.adapter

    def as_dict(self) -> dict[str, object]:
        """Serialisierbare Form für ``wg sources list`` und ``GET /api/v1/sources`` (§16.2)."""
        return {
            "name": self.config.name,
            "adapter": self.config.adapter,
            "enabled": self.config.enabled,
            "id_prefix": self.config.id_prefix,
            "scope": self.config.target.scope,
            "default_type": self.config.target.default_type,
            "capabilities": (
                {} if self.adapter is None else self.adapter.capabilities.model_dump()
            ),
            "health": self.health.model_dump(mode="json"),
        }


class AdapterRegistry:
    """Findet, baut und konfiguriert die Adapter der eingeschalteten Quellen."""

    def __init__(
        self,
        *,
        builtins: dict[str, AdapterFactory] | None = None,
        entry_point_group: str = defaults.ADAPTER_ENTRY_POINT_GROUP,
    ) -> None:
        """
        Args:
            builtins: Abweichende eingebaute Adapter. Als Parameter, damit ein Test die Registry
                ohne die mitgelieferten Umsetzungen prüfen kann.
            entry_point_group: Abweichende Entry-Point-Gruppe, ebenfalls für Tests.
        """
        self._builtins = BUILTIN_ADAPTERS if builtins is None else builtins
        self._group = entry_point_group

    # -- Auffinden --------------------------------------------------------------

    def known_keys(self) -> tuple[str, ...]:
        """Alle ohne ``class:`` auffindbaren Adapterschlüssel, sortiert."""
        return tuple(sorted(set(self._builtins) | {item.name for item in self._entry_points()}))

    def factory_for(self, cfg: SourceConfig) -> AdapterFactory:
        """Die Fabrik zu einer Quellkonfiguration, entlang der Rangfolge aus §8.3.

        Raises:
            AdapterNotFound: Wenn keiner der drei Wege etwas liefert.
        """
        if cfg.adapter_class:
            return _load_class(cfg.adapter_class, cfg.name)

        for eintrag in self._entry_points():
            if eintrag.name == cfg.adapter:
                return _load_entry_point(eintrag, cfg.name)

        eingebaut = self._builtins.get(cfg.adapter)
        if eingebaut is not None:
            return eingebaut

        raise AdapterNotFound(
            f"Quelle '{cfg.name}' nennt den Adapter '{cfg.adapter}', der nirgends auffindbar ist. "
            f"Bekannt sind: {', '.join(self.known_keys()) or '—'}. Entweder das Paket "
            f"installieren, das ihn unter '{self._group}' anmeldet, oder in sources.yaml ein "
            f"'class: \"paket.modul:Klasse\"' angeben (§8.3)."
        )

    def _entry_points(self) -> Iterator[EntryPoint]:
        """Die angemeldeten Adapter-Fabriken installierter Pakete."""
        return iter(entry_points(group=self._group))

    # -- Bauen ------------------------------------------------------------------

    def create(self, cfg: SourceConfig) -> SourceAdapter:
        """Baut einen Adapter und konfiguriert ihn (§8.3).

        Raises:
            AdapterNotFound: Wenn der Adapter nicht auffindbar ist.
        """
        adapter = self.factory_for(cfg)()
        adapter.configure(cfg)
        return adapter

    def register(self, cfg: SourceConfig) -> RegisteredSource:
        """Baut eine Quelle und prüft sie — ohne dass ein Fehler den Aufrufer erreicht.

        Die einzige Ausnahme, die durchgereicht wird, ist :class:`AdapterNotFound`: Sie ist ein
        Konfigurationsfehler und gehört nach §6.5 an den Start, nicht in eine Statusanzeige.
        """
        adapter = self.create(cfg)
        try:
            health = adapter.health()
        except Exception as exc:
            _log.warning("quelle.ungesund", source=cfg.name, error=str(exc))
            return RegisteredSource(
                config=cfg,
                adapter=adapter,
                health=HealthStatus(
                    state=HealthState.UNHEALTHY,
                    detail=f"health() ist gescheitert: {type(exc).__name__}: {exc}",
                ),
            )
        return RegisteredSource(config=cfg, adapter=adapter, health=health)

    def build_all(self, sources: SourcesConfig) -> tuple[RegisteredSource, ...]:
        """Registriert alle eingeschalteten Quellen.

        Ausgeschaltete Quellen erscheinen bewusst nicht: ``enabled: false`` heißt "gibt es
        gerade nicht", nicht "ist kaputt". Ihr Status wäre eine Behauptung über eine Verbindung,
        die niemand aufgebaut hat.

        Raises:
            AdapterNotFound: Wenn eine Quelle auf einen unbekannten Adapter zeigt (§6.5).
        """
        # `register` ruft `health()` auf, und das ist bei einer HTTP-Quelle eine Anfrage nach
        # draußen. Bei zehn Quellen summierten sich zehn Zeitlimits nacheinander, bevor der
        # Dienst überhaupt startet — und die schlechteste Quelle bestimmte die Startzeit aller.
        # `sources.max_concurrency` löst das; die Reihenfolge bleibt die der Konfiguration,
        # weil `parallel` sie hält (§8.3: der Status ist eine Anzeige, kein Wettlauf).
        gebaut = tuple(
            parallel(
                sources.enabled,
                self.register,
                gleichzeitig=sources.max_concurrency,
            )
        )
        _log.info(
            "quellen.registriert",
            total=len(gebaut),
            usable=sum(1 for item in gebaut if item.usable),
            gleichzeitig=sources.max_concurrency,
        )
        return gebaut


def _load_class(pfad: str, quelle: str) -> AdapterFactory:
    """Lädt ``paket.modul:Klasse`` (§8.3, Weg 2).

    Raises:
        AdapterNotFound: Bei falscher Schreibweise, fehlendem Modul oder fehlendem Namen.
    """
    modulname, trenner, klassenname = pfad.partition(defaults.ADAPTER_CLASS_SEPARATOR)
    if not trenner or not modulname or not klassenname:
        raise AdapterNotFound(
            f"Quelle '{quelle}': '{pfad}' ist kein Modulpfad. Erwartet wird "
            f"'paket.modul{defaults.ADAPTER_CLASS_SEPARATOR}Klasse' (§8.3)."
        )
    try:
        modul = import_module(modulname)
    except ImportError as exc:
        raise AdapterNotFound(
            f"Quelle '{quelle}': Das Modul '{modulname}' lässt sich nicht laden: {exc}"
        ) from exc
    try:
        return getattr(modul, klassenname)  # type: ignore[no-any-return]
    except AttributeError as exc:
        raise AdapterNotFound(
            f"Quelle '{quelle}': Das Modul '{modulname}' kennt '{klassenname}' nicht."
        ) from exc


def _load_entry_point(eintrag: EntryPoint, quelle: str) -> AdapterFactory:
    """Lädt eine über Entry Point angemeldete Fabrik (§8.3, Weg 1).

    Raises:
        AdapterNotFound: Wenn das anmeldende Paket unvollständig installiert ist.
    """
    try:
        return eintrag.load()  # type: ignore[no-any-return]
    except Exception as exc:
        raise AdapterNotFound(
            f"Quelle '{quelle}': Der Entry Point '{eintrag.name}' "
            f"({eintrag.value}) lässt sich nicht laden: {type(exc).__name__}: {exc}"
        ) from exc
