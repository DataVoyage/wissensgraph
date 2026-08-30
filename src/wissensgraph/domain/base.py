"""Gemeinsame Basis der Domänenmodelle.

Alle Domänenmodelle sind unveränderlich. Das ist keine Stilfrage: Die Kernoperation aus §10.2
verschmilzt einen vorhandenen Zustand mit einem Entwurf zu einem *neuen* Zustand. Wäre das
vorhandene Konzept veränderbar, ließe sich nach dem Zusammenführen nicht mehr feststellen, was
vorher galt — und genau das braucht der ``change_log``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Unveränderliches Modell ohne unbekannte Felder.

    ``extra='forbid'`` fängt Tippfehler in einem Adapter-Mapping ab: Ein Feld ``titel`` statt
    ``title`` soll den Lauf abbrechen und nicht als stillschweigend verworfener Wert dazu führen,
    dass jedes Konzept ohne Titel in der Datenbank landet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


def unique_strings(value: object) -> object:
    """Normiert eine Liste von Zeichenketten: getrimmt, ohne Leereinträge, ohne Dubletten.

    Die Reihenfolge des ersten Vorkommens bleibt erhalten — Tags und ``audience``-Werte werden
    angezeigt, und eine sich bei jedem Lauf umsortierende Liste erzeugt Scheinänderungen.
    """
    if not isinstance(value, list | tuple):
        return value
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return value
        stripped = item.strip()
        if stripped and stripped not in result:
            result.append(stripped)
    return tuple(result)
