"""Prüfwerkzeug, das der Kern für fremde Erweiterungen bereitstellt (§22.3).

Der Inhalt ist Teil des Pakets und nicht der Testsuite des Repositories. Das ist Absicht: §8.6
verlangt, dass eine neue Quelle die Contract-Suite gegen ihre Implementierung laufen lässt — "sie
ist Teil des Kerns und wird nicht kopiert". Läge sie unter ``tests/``, könnte niemand sie
importieren, ohne sie abzuschreiben, und ab der ersten Abschrift gäbe es zwei Fassungen.

Das Modul setzt ``pytest`` voraus. Zur Laufzeit des Systems wird es nie importiert.
"""

from __future__ import annotations

from wissensgraph.testing.adapter_contract import AdapterContractTests

__all__ = ["AdapterContractTests"]
