"""Anwendungsdienste — die Abläufe, die API, CLI und MCP-Server gemeinsam benutzen (§4.2).

Leitprinzip 14: Jede Fachlogik ist eine Funktion, die auch ohne Schnittstelle aufrufbar ist. Die
Dienste sprechen ausschließlich mit den Ports aus :mod:`wissensgraph.ports`; ein
import-linter-Kontrakt hält sie von der Infrastruktur fern.
"""

from __future__ import annotations

from wissensgraph.services.concepts import (
    ConceptService,
    ConceptValidationError,
    UpsertResult,
)

__all__ = [
    "ConceptService",
    "ConceptValidationError",
    "UpsertResult",
]
