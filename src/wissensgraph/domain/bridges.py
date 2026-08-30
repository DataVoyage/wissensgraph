"""Die Richtung einer Brücke (§7.3, §12.1, §20.1).

Eine Kante führt ihren Zielstore explizit mit, weil ein Fremdschlüssel über zwei Datenbanken
hinweg technisch unmöglich ist (§7.3). Damit stellt sich eine Frage, die kein Constraint für sich
beantworten kann: *In welche Richtung* darf eine solche Kante zeigen?

§12.1 legt es fest:

    "Kanten von ``personal`` nach ``shared`` sind erlaubt und der Normalfall. Kanten von
    ``shared`` nach ``personal`` sind durch einen CHECK-Constraint im shared-Store verboten. Der
    geteilte Store weiß nicht, dass es persönliche Konzepte gibt. Die Rückrichtung wird beim
    Traversieren aus dem personal-Store rekonstruiert."

Die Regel steht hier und nicht verstreut in Repositories und Diensten, weil sie an drei Stellen
gebraucht wird, die einander nie widersprechen dürfen: beim Anlegen einer Kante (wohin darf eine
Referenz zeigen), beim erneuten Auflösen (welche fremden Stores werden befragt) und beim
Traversieren (wo liegt die Gegenrichtung). Ein Constraint in der Datenbank ist die letzte
Verteidigungslinie; diese Datei ist die erste.

Warum die Asymmetrie überhaupt: Wüsste der geteilte Store von persönlichen Konzepten, stünde die
Existenz einer persönlichen Notiz — ihre ID, ihr Zeitpunkt, ihre Verknüpfung — in der Datenbank,
die eines Tages auf einem zentralen Server liegen soll (§5.1). Leitprinzip 2 wäre damit gebrochen,
noch bevor ein einziges Inhaltsfeld die Grenze überquert.
"""

from __future__ import annotations

from collections.abc import Iterable

from wissensgraph.config import defaults


def may_bridge(*, from_store: str, to_store: str) -> bool:
    """Ob eine Kante von einem Store in einen anderen zeigen darf (§12.1).

    Innerhalb eines Stores ist immer alles erlaubt. Über die Grenze hinweg darf ausschließlich
    der ``personal``-Store hinauszeigen — er ist der private, der vom geteilten weiß, und nicht
    umgekehrt.
    """
    if from_store == to_store:
        return True
    return from_store == defaults.STORE_PERSONAL


def bridge_targets(from_store: str, known_stores: Iterable[str]) -> tuple[str, ...]:
    """Die *fremden* Stores, in die ein Store Kanten schlagen darf — ohne den eigenen.

    Die Reihenfolge ist die der Konfiguration, damit die Auflösung einer Referenz bei mehreren
    möglichen Zielen reproduzierbar bleibt: Zwei Läufe über denselben Bestand sollen dieselbe
    Kante erzeugen.
    """
    return tuple(
        store
        for store in known_stores
        if store != from_store and may_bridge(from_store=from_store, to_store=store)
    )


def bridge_sources(to_store: str, known_stores: Iterable[str]) -> tuple[str, ...]:
    """Die fremden Stores, aus denen Kanten in diesen Store zeigen dürfen.

    Die Umkehrung von :func:`bridge_targets` und die Grundlage zweier Vorgänge: der Rückrichtung
    beim Traversieren (§12.1) und dem erneuten Auflösen von Brücken, nachdem sich in ``to_store``
    etwas geändert hat. Eine Notiz in ``personal``, die auf eine noch nicht synchronisierte
    Confluence-Seite zeigt, wird auflösbar, ohne dass sich an ihr selbst etwas geändert hätte.
    """
    return tuple(
        store
        for store in known_stores
        if store != to_store and may_bridge(from_store=store, to_store=to_store)
    )


def resolution_order(from_store: str, known_stores: Iterable[str]) -> tuple[str, ...]:
    """Die Stores, in denen eine Referenz gesucht wird — der eigene zuerst (§8.5).

    Der eigene Store hat Vorrang, weil eine ID dort die naheliegendere Bedeutung hat: Ein Verweis
    ``[[note:abc]]`` in einer persönlichen Notiz meint die persönliche Notiz und nicht ein
    gleichnamiges Konzept anderswo. Erst wenn die ID im eigenen Store unbekannt ist, wird die
    Grenze überquert.
    """
    return (from_store, *bridge_targets(from_store, known_stores))
