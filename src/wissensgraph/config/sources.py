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
from wissensgraph.config.errors import ConfigFileError, ConfigValidationError
from wissensgraph.config.loader import load_yaml_mapping
from wissensgraph.config.network import extract_host
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
    web_base_url: str | None = Field(
        default=None,
        description=(
            "Adresse der Weboberfläche derselben Instanz, für Links im erzeugten Text. Ohne "
            "Angabe wird 'base_url' benutzt — hinter einem API-Gateway sind das zwei "
            "verschiedene Hosts, und ein Leser käme mit der API-Adresse nicht weit."
        ),
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

    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Zusätzliche Kopfzeilen jeder Anfrage. Für Gateways, die neben dem Token noch einen "
            "eigenen Schlüssel verlangen — etwa 'x-apikey'. Werte gehören in ENV (§20.2)."
        ),
    )
    api_prefix: str | None = Field(
        default=None,
        description=(
            "Pfadpräfix vor den Endpunkten des Adapters. Ohne Angabe gilt die Vorgabe des "
            "Adapters. Ein Gateway, dessen base_url schon auf die API zeigt, setzt hier ''."
        ),
    )
    internal: bool | None = Field(
        default=None,
        description=(
            "Ob dieser Host ohne Proxy erreichbar sein muss. Ohne Angabe wird es aus dem Namen "
            "abgeleitet (siehe 'is_internal'); die Angabe übersteuert die Ableitung."
        ),
    )

    # Ein Platzhalter mit leerem Rückfallwert (``${WG_SOURCE_JIRA__TOKEN:-}``) liefert einen
    # leeren String. Ohne diese Umwandlung ginge er als gesetztes Token durch, und jede Anfrage
    # trüge ein leeres 'Authorization: Bearer' — eine Kopfzeile, die manche Server anders
    # beantworten als gar keine.
    _normalize = field_validator("base_url", "web_base_url", "token", mode="before")(empty_to_none)

    @property
    def web_url(self) -> str:
        """Die Adresse fuer Links im Text: die eigene, sonst die der API."""
        return (self.web_base_url or self.base_url or "").rstrip("/")

    @field_validator("extra_headers")
    @classmethod
    def _check_reserved(cls, value: dict[str, str]) -> dict[str, str]:
        """Verbietet Kopfzeilen, die der Adapter selbst setzt.

        Ohne diese Prüfung könnte ein ``Authorization``-Eintrag in ``extra_headers`` das aus
        ``token`` gebaute Token still verdrängen. Beide sähen in der Konfiguration richtig aus,
        und welcher gewinnt, hinge an der Reihenfolge im Code — genau die Art Fehler, die erst
        beim ersten Aufruf gegen das echte System auffällt.
        """
        belegt = sorted(name for name in value if name.lower() in defaults.SOURCE_RESERVED_HEADERS)
        if belegt:
            raise ValueError(
                f"Die Kopfzeilen {belegt} setzt der Adapter selbst und dürfen nicht in "
                f"'extra_headers' stehen. Ein Bearer-Token gehört in 'connection.token'."
            )
        return value

    @property
    def is_internal(self) -> bool:
        """Ob diese Quelle *ohne* Proxy erreicht werden muss (§5.2).

        Die Unterscheidung ist im Unternehmensnetz entscheidend und läuft in beide Richtungen:
        ``mock-sources`` **muss** am Proxy vorbei, sonst versucht der Proxy einen Containernamen
        aufzulösen und der Fehler sieht aus wie ein Ausfall des Nachbarn. ``jira.schwarz`` muss
        umgekehrt **über** den Proxy, weil es von innen sonst gar nicht erreichbar ist.

        Abgeleitet wird es aus der Form des Namens: Ein Compose-Dienst heißt ``broker`` oder
        ``mock-sources`` und hat keinen Punkt, ein Host im Netz heißt ``jira.schwarz`` und hat
        einen. Die Faustregel trifft die üblichen Fälle; wo sie danebenliegt — ein interner Dienst
        unter seinem FQDN —, entscheidet ``internal:`` in der Konfiguration.
        """
        if self.internal is not None:
            return self.internal
        host = extract_host(self.base_url or "")
        if not host:
            return False
        return "." not in host or host in defaults.SOURCE_INTERNAL_HOSTS


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
    shared_id_prefix: bool = Field(
        default=False,
        description=(
            "Erlaubt es, dieses Präfix mit anderen Quellen zu teilen. Für mehrere Ausschnitte "
            "*einer* Instanz, deren Objekt-IDs instanzweit eindeutig sind."
        ),
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
        praefixe: dict[str, SourceConfig] = {}
        for source in self.sources:
            vorher = praefixe.get(source.id_prefix)
            if vorher is None:
                praefixe[source.id_prefix] = source
                continue
            _check_shared_prefix(vorher, source)
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


def _check_shared_prefix(erste: SourceConfig, zweite: SourceConfig) -> None:
    """Prüft, ob zwei Quellen sich ein ID-Präfix teilen dürfen (§7.5).

    Der Regelfall ist, dass sie es nicht dürfen: Zwei Quellen mit demselben Präfix vergeben
    dieselben Konzept-IDs und überschreiben einander. Es gibt aber einen Fall, in dem das Teilen
    nicht der Unfall ist, sondern die Absicht — mehrere Ausschnitte *einer* Instanz.

    Vier Confluence-Spaces, die in vier Scopes gehören, brauchen vier Quellblöcke, weil
    ``target.scope`` je Block gilt. Ihre Seiten liegen trotzdem in einer Confluence-Instanz, und
    Seiten-IDs sind dort instanzweit eindeutig. Sie müssen sich das Präfix sogar teilen: Verlinkt
    eine Seite aus dem einen Space eine aus dem anderen, kennt der Adapter nur deren Seiten-ID —
    aus welchem Space sie stammt und welcher Quellblock sie einmal holen wird, weiß er nicht. Mit
    Präfixen je Block ließe sich diese Referenz nicht aufschreiben.

    Deshalb ist das Teilen eine Erklärung, die beide Seiten abgeben müssen, und keine Ableitung.
    Wer sie abgibt, sagt: Die Objekt-IDs dieser Quellen stammen aus einem Nummernkreis.

    Raises:
        ValueError: Wenn eine der beiden Quellen nicht zugestimmt hat oder die beiden
            verschiedene Adapter benutzen.
    """
    stumm = [q.name for q in (erste, zweite) if not q.shared_id_prefix]
    if stumm:
        raise ValueError(
            f"Die Quellen '{erste.name}' und '{zweite.name}' benutzen beide das ID-Präfix "
            f"'{erste.id_prefix}'. Ihre Objekte bekämen dieselben Konzept-IDs und würden sich "
            f"gegenseitig überschreiben (§7.5). Ist das gewollt — mehrere Ausschnitte einer "
            f"Instanz mit einem Nummernkreis —, dann brauchen alle beteiligten Quellen "
            f"'shared_id_prefix: true'; es fehlt bei: {', '.join(stumm)}."
        )
    if erste.adapter != zweite.adapter:
        raise ValueError(
            f"Die Quellen '{erste.name}' ({erste.adapter}) und '{zweite.name}' "
            f"({zweite.adapter}) teilen sich das ID-Präfix '{erste.id_prefix}', benutzen aber "
            f"verschiedene Adapter. Ein geteiltes Präfix behauptet einen gemeinsamen "
            f"Nummernkreis; zwei Systeme haben keinen."
        )


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
    benannt = path is not None or bool(umgebung.get(defaults.SOURCES_FILE_ENV, "").strip())
    ziel = sources_file(settings, umgebung) if path is None else path
    if not ziel.is_file():
        # Wie beim Model-Router: Eine fehlende Datei am Standardort heißt "keine Quellen", ein
        # falsch benannter Pfad heißt Vertipper. Das zweite still zu behandeln, hieße einen
        # Sync-Lauf ohne eine einzige Quelle als Erfolg zu melden.
        if benannt:
            raise ConfigFileError(
                f"Die angegebene Quellkonfiguration '{ziel}' existiert nicht "
                f"({defaults.SOURCES_FILE_ENV} oder --sources). Ohne Angabe wird "
                f"'{Path(settings.config_dir) / defaults.SOURCES_CONFIG_FILENAME}' verwendet."
            )
        return SourcesConfig()

    roh = load_yaml_mapping(ziel)
    aufgeloest = resolve_placeholders(roh, umgebung, path=ziel.name)

    try:
        config = SourcesConfig.model_validate(aufgeloest)
    except ValidationError as exc:
        raise ConfigValidationError(_format_error(exc, ziel)) from exc

    for source in config.sources:
        _check_against_settings(source, settings, ziel)
    _check_shared_prefix_stores(config, settings, ziel)
    return config


def _check_shared_prefix_stores(config: SourcesConfig, settings: Settings, path: Path) -> None:
    """Quellen mit geteiltem Präfix müssen in denselben Store schreiben (§7.3, §20.1).

    Ein geteiltes Präfix sagt: Diese IDs kommen aus einem Nummernkreis. Lägen zwei davon in
    verschiedenen Stores, gäbe es ``confluence:123`` zweimal — einmal in ``shared``, einmal in
    ``personal``. Eine Referenz darauf nennt aber nur die ID; welche der beiden gemeint ist,
    stünde nirgends, und die Auflösung entschiede es nach Fundreihenfolge.

    Das ist keine Formalie: Über diese Grenze läuft Leitprinzip 2. Ein Verweis, der in
    ``personal`` landet statt in ``shared``, zieht persönliche Inhalte in einen Zusammenhang, in
    den sie nicht gehören.
    """
    stores: dict[str, tuple[str, str]] = {}
    for source in config.sources:
        if not source.shared_id_prefix:
            continue
        store = settings.store_of_scope(source.target.scope)
        vorher = stores.get(source.id_prefix)
        if vorher is not None and vorher[1] != store:
            raise ConfigValidationError(
                f"Die Quellen '{vorher[0]}' und '{source.name}' in '{path}' teilen sich das "
                f"ID-Präfix '{source.id_prefix}', schreiben aber in verschiedene Stores "
                f"('{vorher[1]}' und '{store}'). Dieselbe Konzept-ID gäbe es dann zweimal, und "
                f"eine Referenz darauf könnte nicht mehr sagen, welche gemeint ist (§7.3)."
            )
        stores.setdefault(source.id_prefix, (source.name, store))


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
