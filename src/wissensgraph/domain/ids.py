"""ID-Konvention der Konzepte (§7.5).

Eine Konzept-ID besteht aus einem Präfix und einem lokalen Teil, getrennt durch einen
Doppelpunkt: ``confluence:184320``, ``cluster:3f2a…``, ``project:finance-integration``.

Zwei Festlegungen aus dem Dokument prägen dieses Modul:

1. **Das Quellpräfix steht nicht im Code.** Es kommt aus ``sources.yaml`` (``id_prefix``). Hier
   wird deshalb nur die *Form* geprüft, nie eine Liste erlaubter Präfixe. Eine neue Quelle darf
   IDs vergeben, ohne dass der Kern angefasst wird (§8.1).
2. **Eine ID ist unveränderlich.** Wird ein Quellobjekt umbenannt, bleibt die ID gleich. Es gibt
   deshalb bewusst keine Funktion, die eine ID aus einem Titel ableitet — daraus entstünde eine
   ID, die sich mit dem Inhalt ändert.
"""

from __future__ import annotations

import re
from uuid import uuid4

from wissensgraph.config import defaults

#: Erlaubte Form eines Präfixes: kleingeschrieben, beginnt mit einem Buchstaben. Die Enge ist
#: Absicht — das Präfix erscheint in URLs, Logs und Dateinamen von Exporten.
_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

#: Erlaubte Form des lokalen Teils. Verboten sind Leerraum (eine ID mit Leerzeichen lässt sich in
#: ``[[id]]``-Referenzen nicht sauber abgrenzen) und eckige Klammern (sie sind die Syntax der
#: Referenz selbst).
_LOCAL_PATTERN = re.compile(r"^[^\s\[\]]+$")


class InvalidConceptIdError(ValueError):
    """Eine Zeichenkette erfüllt die ID-Konvention aus §7.5 nicht."""

    def __init__(self, value: str, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(
            f"'{value}' ist keine gültige Konzept-ID: {reason} "
            f"Erwartet wird '<präfix>{defaults.ID_SEPARATOR}<lokaler-teil>' (§7.5)."
        )


def split_concept_id(value: str) -> tuple[str, str]:
    """Zerlegt eine ID in Präfix und lokalen Teil.

    Getrennt wird am *ersten* Doppelpunkt: ``jira:PROJ-123`` ergibt ``('jira', 'PROJ-123')``,
    und eine Quelle, deren externe IDs selbst Doppelpunkte enthalten, bleibt darstellbar.

    Raises:
        InvalidConceptIdError: Wenn die Form nicht stimmt.
    """
    prefix, separator, local = value.partition(defaults.ID_SEPARATOR)
    if not separator:
        raise InvalidConceptIdError(value, "Es fehlt der Doppelpunkt.")
    if not _PREFIX_PATTERN.match(prefix):
        raise InvalidConceptIdError(
            value, f"Das Präfix '{prefix}' ist leer oder enthält unerlaubte Zeichen."
        )
    if not _LOCAL_PATTERN.match(local):
        raise InvalidConceptIdError(
            value, "Der lokale Teil ist leer oder enthält Leerraum bzw. eckige Klammern."
        )
    return prefix, local


def is_valid_concept_id(value: str) -> bool:
    """Ob eine Zeichenkette die ID-Konvention erfüllt — ohne Ausnahme bei Verstoß.

    Für die Referenzauflösung: Ein ``[[…]]`` im Fließtext, das keine ID ist, ist kein Fehler,
    sondern schlicht keine Referenz (§8.5).
    """
    try:
        split_concept_id(value)
    except InvalidConceptIdError:
        return False
    return True


def validate_concept_id(value: str) -> str:
    """Gibt die ID unverändert zurück, wenn sie gültig ist — sonst mit Begründung abbrechen.

    Raises:
        InvalidConceptIdError: Wenn die Form nicht stimmt.
    """
    split_concept_id(value)
    return value


def concept_id(prefix: str, local: str) -> str:
    """Setzt eine ID aus Präfix und lokalem Teil zusammen und prüft sie sofort.

    Raises:
        InvalidConceptIdError: Wenn das Ergebnis die Konvention verletzt.
    """
    value = f"{prefix}{defaults.ID_SEPARATOR}{local}"
    split_concept_id(value)
    return value


def source_concept_id(id_prefix: str, external_id: str) -> str:
    """Die ID eines gespiegelten Quellobjekts (§7.5, Zeile 1 der Tabelle).

    Args:
        id_prefix: Präfix der Quelle aus ``sources.yaml``.
        external_id: ID des Objekts im Quellsystem.
    """
    return concept_id(id_prefix, external_id)


def new_cluster_id() -> str:
    """Eine neue Cluster-ID. Erzeuger sind Clustering und Kuration (§7.5)."""
    return concept_id(defaults.ID_PREFIX_CLUSTER, uuid4().hex)


def new_note_id() -> str:
    """Eine neue Notiz-ID. Erzeuger sind Agent und UI (§7.5)."""
    return concept_id(defaults.ID_PREFIX_NOTE, uuid4().hex)


def project_id(slug: str) -> str:
    """Die ID eines Brücken-Konzepts. Erzeuger ist ein Mensch, der Slug ist deshalb sprechend."""
    return concept_id(defaults.ID_PREFIX_PROJECT, slug)
