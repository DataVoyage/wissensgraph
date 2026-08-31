"""Aufbau der Konfiguration entlang der Präzedenzkette aus §6.2.

Von niedriger nach hoher Priorität::

    Code-Defaults  <  config/*.yaml  <  .env-Datei  <  Prozess-ENV  <  CLI-Flag / API-Parameter

Die Code-Defaults stehen in :mod:`wissensgraph.config.defaults` und wirken als Feld-Defaults des
Pydantic-Schemas — sie greifen also überall dort, wo keine höhere Ebene etwas sagt. Das Ergebnis
wird einmal validiert und danach unveränderlich gehalten (§6.1 Regel 4).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wissensgraph.config import defaults
from wissensgraph.config.dotenv import export_dotenv, load_dotenv
from wissensgraph.config.env_mapping import ENV_BINDINGS
from wissensgraph.config.errors import ConfigFileError, ConfigValidationError
from wissensgraph.config.placeholders import resolve_placeholders
from wissensgraph.config.schema import Settings


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Liest eine YAML-Datei und stellt sicher, dass sie ein Mapping enthält.

    Raises:
        ConfigFileError: Wenn die Datei fehlt, unlesbar ist oder kein Mapping auf oberster Ebene
            enthält.
    """
    if not path.is_file():
        raise ConfigFileError(f"Config-Datei '{path}' existiert nicht.")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigFileError(f"Config-Datei '{path}' ist nicht lesbar: {exc}") from exc
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"Config-Datei '{path}' enthält kein gültiges YAML: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigFileError(
            f"Config-Datei '{path}' muss ein Mapping auf oberster Ebene enthalten, "
            f"gefunden: {type(parsed).__name__}."
        )
    return parsed


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Führt zwei Mappings rekursiv zusammen; ``override`` gewinnt.

    Listen werden *ersetzt*, nicht verkettet. Eine in einer höheren Präzedenzstufe angegebene
    Liste (etwa ``scopes``) soll die niedrigere vollständig ablösen — sonst könnte man einen
    Eintrag nie wieder loswerden.
    """
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def set_path(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Setzt einen Wert an einem verschachtelten Pfad und legt fehlende Ebenen an."""
    cursor = target
    for key in path[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cursor[key] = existing
        cursor = existing
    cursor[path[-1]] = value


def apply_env_overrides(data: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """Legt die ENV-Schnittstelle aus §6.4 über die YAML-Ebene.

    Leere Variablen werden ignoriert. Eine gesetzte, aber leere Variable ist im Docker-Umfeld ein
    häufiges Versehen (``WG_API_TOKEN=``); sie soll nicht einen sinnvollen YAML-Wert überschreiben.
    """
    result = dict(data)
    for binding in ENV_BINDINGS:
        raw = env.get(binding.variable)
        if raw is None or raw.strip() == "":
            continue
        try:
            converted = binding.convert(raw)
        except ConfigValidationError as exc:
            raise ConfigValidationError(f"{binding.variable}: {exc}") from exc
        set_path(result, binding.path, converted)
    return result


def build_settings(
    *,
    config_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    dotenv_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Baut die vollständig aufgelöste, validierte Konfiguration.

    Args:
        config_file: Pfad zu ``wissensgraph.yaml``. Ohne Angabe wird er aus ``WG_CONFIG_DIR``
            (oder dem Code-Default) abgeleitet.
        env: Die Prozessumgebung. Ohne Angabe ``os.environ`` — als Parameter, damit Tests ohne
            globalen Zustand auskommen.
        dotenv_file: Pfad zu einer ``.env``-Datei. Ohne Angabe wird ``.env`` im Arbeitsverzeichnis
            versucht; eine fehlende Datei ist kein Fehler.
        overrides: Höchste Präzedenzstufe — CLI-Flags oder API-Parameter, bereits als
            verschachteltes Mapping.

    Returns:
        Die validierte :class:`Settings`-Instanz.

    Raises:
        ConfigFileError: Bei fehlender oder fehlerhafter Config-Datei.
        PlaceholderResolutionError: Bei nicht auflösbarem ``${...}``-Platzhalter.
        ConfigValidationError: Bei jedem Verstoß gegen die Regeln aus §6.5.
    """
    prozessumgebung_verwendet = env is None
    process_env = dict(os.environ if env is None else env)

    dotenv_path = Path(".env") if dotenv_file is None else dotenv_file
    file_env = load_dotenv(dotenv_path)

    # §6.2: Prozess-ENV schlägt .env-Datei.
    effective_env = {**file_env, **process_env}

    if prozessumgebung_verwendet:
        export_dotenv(file_env)

    resolved_config_file = _resolve_config_file(config_file, effective_env)
    raw = load_yaml_mapping(resolved_config_file)

    resolved = resolve_placeholders(raw, effective_env, path=resolved_config_file.name)
    if not isinstance(resolved, dict):  # pragma: no cover — load_yaml_mapping garantiert dict
        raise ConfigFileError(f"Config-Datei '{resolved_config_file}' ist kein Mapping.")

    with_env = apply_env_overrides(resolved, effective_env)
    merged = deep_merge(with_env, overrides or {})

    # Die Nachbardateien liegen beim Kern: Wer eine Config-Datei benennt, meint auch die
    # 'models.yaml' und 'sources.yaml' daneben. Ohne diese Zeile bliebe 'config_dir' auf dem
    # Container-Default stehen, und der Router suchte anderswo als der Kern.
    merged.setdefault("config_dir", str(resolved_config_file.parent))

    try:
        return Settings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigValidationError(_format_validation_error(exc, resolved_config_file)) from exc


def _resolve_config_file(config_file: Path | None, env: Mapping[str, str]) -> Path:
    """Bestimmt den Pfad der Kern-Config-Datei aus Argument, ENV oder Code-Default.

    Der Code-Default ``/app/config`` ist der Pfad **im Container** — dort bindet Compose das
    Verzeichnis dorthin ein. Auf dem Host gibt es ihn nicht, und ein Aufruf wie ``uv run wg
    doctor`` scheiterte deshalb mit "Config-Datei '/app/config/wissensgraph.yaml' existiert
    nicht", obwohl das Verzeichnis danebenliegt.

    Deshalb wird ohne ausdrückliche Angabe zuerst ``./config`` im Arbeitsverzeichnis versucht.
    Das ist keine Rateübung mit zwei Ausgängen: Im Container ist ``/app`` das Arbeitsverzeichnis,
    ``./config`` und ``/app/config`` bezeichnen dort also dieselbe Stelle. Die Regel ändert nur
    den Fall, in dem der bisherige Default ohnehin ins Leere zeigte.
    """
    if config_file is not None:
        return config_file
    angegeben = env.get("WG_CONFIG_DIR", "").strip()
    if angegeben:
        return Path(angegeben) / defaults.CORE_CONFIG_FILENAME
    daneben = Path(defaults.LOCAL_CONFIG_DIR) / defaults.CORE_CONFIG_FILENAME
    if daneben.is_file():
        return daneben
    return Path(defaults.CONFIG_DIR) / defaults.CORE_CONFIG_FILENAME


def _format_validation_error(exc: ValidationError, source: Path) -> str:
    """Formt Pydantic-Fehler in eine Meldung um, die den Ort des Problems benennt.

    Die Standardausgabe von Pydantic ist für eine Startfehlermeldung zu technisch. Wer einen
    Container startet, will wissen: welches Feld, welche Datei, was ist die Erwartung.
    """
    lines = [f"Konfiguration aus '{source}' ist ungültig:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(Wurzel)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
