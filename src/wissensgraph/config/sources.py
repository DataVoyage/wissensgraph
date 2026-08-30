"""Schema und Laden von ``config/sources.yaml`` (§8.4, §6.3).

Diese Datei ist die zweite Hälfte des Versprechens aus §8.1: Eine neue Quelle ist "eine Klasse,
die einen Kontrakt erfüllt, **plus ein Eintrag in ``sources.yaml``**". Der Kontrakt steht in
:mod:`wissensgraph.ports.sources`, der Eintrag hier.

Bewusst getrennt von :mod:`wissensgraph.config.schema`: Die Kernkonfiguration muss jeder Prozess
laden, die Quellkonfiguration nur, wer synchronisiert. Eine fehlende ``sources.yaml`` ist deshalb
kein Startfehler, sondern eine leere Liste — ein System ohne angebundene Quellen ist ein
zulässiger Zustand, etwa für eine reine UI-Umgebung (Compose-Profil ``minimal``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator

from wissensgraph.config import defaults
from wissensgraph.config.errors import ConfigValidationError
from wissensgraph.config.loader import load_yaml_mapping
from wissensgraph.config.placeholders import resolve_placeholders
from wissensgraph.config.schema import FrozenModel, Settings, empty_to_none

_PREFIX_PATTERN = re.compile(defaults.ID_PREFIX_PATTERN)


class SourceTargetConfig(FrozenModel):
    """Wohin die Objekte einer Quelle geschrieben werden (§8.4).

    Der Store steht hier *nicht*. Er ergibt sich aus dem Scope, und die Zuordnung Scope → Store
    steht in ``wissensgraph.yaml`` (§7.3, §20.1). §8.4 zeigt zwar ``store:`` im Beispiel; wir
    prüfen ihn deshalb gegen den aus dem Scope abgeleiteten Store, statt ihn zu benutzen — sonst
    gäbe es zwei Wahrheiten darüber, in welche Datenbank eine Quelle schreibt, und die
    Datenschutzgrenze aus Leitprinzip 2 hinge an der Sorgfalt beim Ausfüllen einer YAML-Datei.
    """

    store: str | None = Field(
        default=None,
        description=(
            "Erwarteter Store. Optional und nur zur Kontrolle: Weicht er vom Store des Scopes "
            "ab, ist das ein Startfehler."
        ),
    )
    scope: str = Field(min_length=1, description="Scope, in den die Objekte gehören.")
    default_type: str = Field(
        min_length=1, description="Konzepttyp, sofern ein Objekt keinen 'type_hint' mitbringt."
    )


class SourceConnectionConfig(FrozenModel):
    """Verbindungsdaten und Lastgrenzen einer Quellinstanz (§8.4).

    Rate-Limit und Retries stehen hier, weil §8.2 Regel 5 sie dem Adapter zuweist — "mit Werten
    aus seiner Config". Ein Adapter mit eingebauten Zahlen wäre gegen ein Testsystem mit
    engerem Limit nicht betreibbar, ohne ihn zu ändern.
    """

    base_url: str | None = Field(
        default=None,
        description="Basis-URL des Quellsystems. Der einzige Unterschied zwischen Mock und Live.",
    )
    token: str | None = Field(default=None, description="Zugangstoken; kommt aus ENV (§20.2).")
    timeout_seconds: float = Field(default=defaults.SOURCE_TIMEOUT_SECONDS, gt=0.0)
    rate_limit_per_second: float = Field(
        default=defaults.SOURCE_RATE_LIMIT_PER_SECOND,
        ge=0.0,
        description="Höchstzahl Anfragen je Sekunde; 0 schaltet die Drosselung ab.",
    )
    retries: int = Field(
        default=defaults.SOURCE_RETRIES,
        ge=0,
        description="Zusätzliche Versuche nach einem vorübergehenden Fehler (429, 5xx, Timeout).",
    )
    page_size: int = Field(default=defaults.SOURCE_PAGE_SIZE, ge=1)
    verify_tls: bool = True

    # Ein Platzhalter mit leerem Rückfallwert (``${WG_SOURCE_JIRA__TOKEN:-}``) liefert einen
    # leeren String. Ohne diese Umwandlung ginge er als gesetztes Token durch, und jede Anfrage
    # trüge ein leeres 'Authorization: Bearer' — eine Kopfzeile, die manche Server anders
    # beantworten als gar keine.
    _normalize = field_validator("base_url", "token", mode="before")(empty_to_none)


class SourceScheduleConfig(FrozenModel):
    """Zeitsteuerung einer Quelle (§8.4).

    In Stufe 3 wird sie gelesen und validiert, aber nicht ausgeführt: §24 nimmt die Zeitsteuerung
    für diese Stufe ausdrücklich aus. Der Eintrag steht trotzdem im Schema, damit eine
    ``sources.yaml`` aus dem Dokument unverändert lädt.
    """

    cron: str | None = None
    enabled: bool = False


class SourceConfig(FrozenModel):
    """Eine Quellinstanz — ein Adapter mit einem Ziel (§8.4).

    "Mehrere Instanzen desselben Adapters mit unterschiedlichen Zielen sind ausdrücklich
    vorgesehen (``confluence-eng``, ``confluence-finance``)." Deshalb sind ``name`` (die Instanz)
    und ``adapter`` (die Umsetzung) zwei Felder und nicht eines.
    """

    name: str = Field(min_length=1, description="Eindeutiger Name dieser Quellinstanz.")
    adapter: str = Field(min_length=1, description="Registry-Schlüssel des Adapters (§8.3).")
    adapter_class: str | None = Field(
        default=None,
        alias="class",
        description=(
            "Modulpfad 'paket.modul:Klasse' als zweiter Registrierungsweg (§8.3). Damit wird ein "
            "Adapter allein über einen Config-Eintrag aktiv, ohne Entry Point und ohne Kerncode."
        ),
    )
    enabled: bool = True
    id_prefix: str = Field(
        min_length=1,
        description="Präfix der erzeugten Konzept-IDs (§7.5). Nicht im Code, sondern hier.",
    )
    target: SourceTargetConfig
    connection: SourceConnectionConfig = SourceConnectionConfig()
    selection: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Quellspezifische Auswahl (Spaces, Boards, JQL). Vom Kern nicht interpretiert — er "
            "reicht sie an den Adapter durch."
        ),
    )
    mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Feldname des SourceDocument -> JSONPath in die Rohantwort der Quelle (§8.4).",
    )
    schedule: SourceScheduleConfig = SourceScheduleConfig()

    model_config = FrozenModel.model_config | {"populate_by_name": True}

    @field_validator("id_prefix")
    @classmethod
    def _check_prefix(cls, value: str) -> str:
        if not _PREFIX_PATTERN.match(value):
            raise ValueError(
                f"'{value}' ist kein gültiges ID-Präfix. Erlaubt sind Kleinbuchstaben, Ziffern, "
                f"'-' und '_', beginnend mit einem Buchstaben (§7.5)."
            )
        return value

    @field_validator("mapping")
    @classmethod
    def _check_mapping_fields(cls, value: dict[str, str]) -> dict[str, str]:
        unbekannt = sorted(set(value) - set(defaults.SOURCE_MAPPING_FIELDS))
        if unbekannt:
            raise ValueError(
                f"Die mapping-Sektion kennt die Felder {unbekannt} nicht. Abbildbar sind: "
                f"{', '.join(defaults.SOURCE_MAPPING_FIELDS)}. Alles Weitere gehört in 'extra' "
                f"und wird vom Kern nicht interpretiert (§8.2)."
            )
        return value


class SourcesConfig(FrozenModel):
    """Der Inhalt von ``sources.yaml`` als Ganzes."""

    sources: tuple[SourceConfig, ...] = ()

    @model_validator(mode="after")
    def _check_unique(self) -> SourcesConfig:
        namen = [source.name for source in self.sources]
        doppelt = sorted({name for name in namen if namen.count(name) > 1})
        if doppelt:
            raise ValueError(
                f"Quellnamen müssen eindeutig sein, doppelt: {doppelt}. Der Name identifiziert "
                f"den Lauf, den Cursor und den Advisory-Lock (§10.5)."
            )
        praefixe: dict[str, str] = {}
        for source in self.sources:
            vorher = praefixe.get(source.id_prefix)
            if vorher is not None:
                raise ValueError(
                    f"Die Quellen '{vorher}' und '{source.name}' benutzen beide das ID-Präfix "
                    f"'{source.id_prefix}'. Ihre Objekte bekämen dieselben Konzept-IDs und würden "
                    f"sich gegenseitig überschreiben (§7.5)."
                )
            praefixe[source.id_prefix] = source.name
        return self

    @property
    def enabled(self) -> tuple[SourceConfig, ...]:
        """Nur die eingeschalteten Quellen."""
        return tuple(source for source in self.sources if source.enabled)

    def get(self, name: str) -> SourceConfig:
        """Die Quelle zu einem Namen.

        Raises:
            KeyError: Wenn keine Quelle so heißt.
        """
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(f"Unbekannte Quelle '{name}'.")


def sources_file(settings: Settings, env: Mapping[str, str] | None = None) -> Path:
    """Der Pfad der Quellkonfiguration — aus ``WG_SOURCES_FILE`` oder aus dem Config-Verzeichnis.

    §6.4 sieht ``WG_SOURCES_FILE`` mit dem Default ``${WG_CONFIG_DIR}/sources.yaml`` vor.
    """
    umgebung = os.environ if env is None else env
    angegeben = umgebung.get(defaults.SOURCES_FILE_ENV, "").strip()
    if angegeben:
        return Path(angegeben)
    return Path(settings.config_dir) / defaults.SOURCES_CONFIG_FILENAME


def load_sources(
    settings: Settings,
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> SourcesConfig:
    """Lädt und validiert ``sources.yaml`` gegen die Kernkonfiguration.

    Die Querprüfungen gegen ``settings`` sind der Grund, warum das Laden hier und nicht im
    Pydantic-Modell steckt: Ob ein Scope existiert und ob ein Konzepttyp in dessen Store zulässig
    ist, weiß nur, wer beide Dateien kennt (§6.5).

    Args:
        settings: Die geprüfte Kernkonfiguration.
        path: Abweichender Pfad; sonst aus ``WG_SOURCES_FILE`` bzw. dem Config-Verzeichnis.
        env: Prozessumgebung — als Parameter, damit Tests ohne globalen Zustand auskommen.

    Returns:
        Die validierte Quellkonfiguration; eine fehlende Datei ergibt eine leere Liste.

    Raises:
        ConfigFileError: Wenn die Datei unlesbar ist oder kein Mapping enthält.
        PlaceholderResolutionError: Bei nicht auflösbarem ``${...}``-Platzhalter.
        ConfigValidationError: Bei jedem Verstoß gegen §8.4 oder §6.5.
    """
    umgebung = dict(os.environ if env is None else env)
    ziel = sources_file(settings, umgebung) if path is None else path
    if not ziel.is_file():
        return SourcesConfig()

    roh = load_yaml_mapping(ziel)
    aufgeloest = resolve_placeholders(roh, umgebung, path=ziel.name)

    try:
        config = SourcesConfig.model_validate(aufgeloest)
    except ValidationError as exc:
        raise ConfigValidationError(_format_error(exc, ziel)) from exc

    for source in config.sources:
        _check_against_settings(source, settings, ziel)
    return config


def _check_against_settings(source: SourceConfig, settings: Settings, path: Path) -> None:
    """Prüft eine Quelle gegen Scopes und Taxonomie der Kernkonfiguration (§6.5)."""
    try:
        store = settings.store_of_scope(source.target.scope)
    except KeyError as exc:
        bekannt = ", ".join(scope.name for scope in settings.scopes)
        raise ConfigValidationError(
            f"Quelle '{source.name}' in '{path}' zielt auf den unbekannten Scope "
            f"'{source.target.scope}'. Konfiguriert sind: {bekannt}."
        ) from exc

    if source.target.store is not None and source.target.store != store:
        raise ConfigValidationError(
            f"Quelle '{source.name}' in '{path}' nennt den Store '{source.target.store}', der "
            f"Scope '{source.target.scope}' liegt aber in '{store}'. Maßgeblich ist der Scope; "
            f"entweder den Eintrag korrigieren oder weglassen (§20.1)."
        )

    try:
        concept_type = settings.concept_type(source.target.default_type)
    except KeyError as exc:
        bekannt = ", ".join(item.name for item in settings.concept_types)
        raise ConfigValidationError(
            f"Quelle '{source.name}' in '{path}' nennt den unbekannten Konzepttyp "
            f"'{source.target.default_type}'. Konfiguriert sind: {bekannt}. Ein neuer Typ gehört "
            f"in die Taxonomie in config/{defaults.CORE_CONFIG_FILENAME} (§8.6 Schritt 4)."
        ) from exc

    if store not in concept_type.stores:
        raise ConfigValidationError(
            f"Quelle '{source.name}' in '{path}' schreibt in den Store '{store}', der Typ "
            f"'{concept_type.name}' ist dort aber nicht zugelassen "
            f"(erlaubt: {', '.join(concept_type.stores)}) (§7.2)."
        )


def _format_error(exc: ValidationError, source: Path) -> str:
    """Formt Pydantic-Fehler in eine Meldung um, die den Ort des Problems benennt."""
    lines = [f"Quellkonfiguration aus '{source}' ist ungültig:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(Wurzel)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
