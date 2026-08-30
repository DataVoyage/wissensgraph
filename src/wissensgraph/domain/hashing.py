"""Content-Hash — die Inhaltsebene der Änderungserkennung (§10.3).

Der Hash ist die billigste Frage im ganzen System: "Hat sich überhaupt etwas geändert?" Lautet
die Antwort nein, entfallen UPDATE, ``change_log``-Eintrag, Re-Embedding, Modellaufruf und
Cluster-Neubewertung (§10.2 Regel 3). Er ist damit der wirksamste Schutz vor unnötigem
Token-Verbrauch — jeder gesparte Hash-Vergleich ist ein nicht bezahlter Modellaufruf.

§10.3 legt fest, worüber gehasht wird: ``title`` + ``description`` + ``body``. Bewusst *nicht*
darin: ``tags``, ``status``, ``resource`` und die Verifikationsfelder. Sie ändern den Inhalt
nicht, und ihre Änderung soll kein Re-Embedding auslösen.
"""

from __future__ import annotations

import hashlib

from wissensgraph.config import defaults


def content_hash(
    *, title: str | None = None, description: str | None = None, body: str | None = None
) -> str:
    """Der SHA-256 über die drei Inhaltsfelder eines Konzepts (§10.3).

    Fehlende Felder gehen als leere Zeichenkette ein; ein Konzept ohne jeden Inhalt hat also
    einen wohldefinierten Hash und keinen Sonderfall.

    Args:
        title: Anzeigename des Konzepts.
        description: Kurzsummary.
        body: Freitext.

    Returns:
        Hexadezimale Darstellung des Hashes.
    """
    joined = defaults.CONTENT_HASH_FIELD_SEPARATOR.join(
        value or "" for value in (title, description, body)
    )
    digest = hashlib.new(defaults.CONTENT_HASH_ALGORITHM)
    digest.update(joined.encode("utf-8"))
    return digest.hexdigest()
