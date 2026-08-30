"""Alembic-Migrationen beider Stores (§7.3, §7.4).

Ein einziger Satz Versionsskripte bedient beide Datenbanken. §7.3 verlangt "zwei PostgreSQL-
Datenbanken mit identischem Schema" — getrennte Skriptbäume wären die naheliegende, aber falsche
Antwort darauf: Sie würden über die Zeit auseinanderdriften, und genau das soll ausgeschlossen
sein. Die einzige zulässige Abweichung, der CHECK-Constraint gegen personal-Verweise im
shared-Store (§7.4), steht deshalb als Fallunterscheidung *innerhalb* der Migration.

Getrennt sind dagegen die Versionstabellen: ``alembic_version_shared`` und
``alembic_version_personal``.

Die Skripte liegen im Paket und nicht neben dem Repository-Wurzelverzeichnis, damit sie im
Container-Image mit installiert werden. ``wg migrate`` funktioniert dadurch überall dort, wo das
Paket installiert ist, ohne dass eine ``alembic.ini`` oder ein Arbeitsverzeichnis stimmen muss.
"""

from __future__ import annotations

from pathlib import Path

#: Verzeichnis dieses Pakets — von Alembic als ``script_location`` benutzt.
SCRIPT_LOCATION = Path(__file__).resolve().parent
