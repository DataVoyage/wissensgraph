"""Dienste — die Anwendungsfälle des Systems (§4.2, §23).

Sie sprechen mit den Ports, nie mit der Infrastruktur; ein import-linter-Kontrakt hält das fest.
Damit ist jeder Anwendungsfall ohne Datenbank und ohne Netzwerk prüfbar.
"""

from __future__ import annotations

from wissensgraph.services.concepts import ConceptService, ConceptValidationError, UpsertResult
from wissensgraph.services.sources import IngestReport, SourceIngestService, SourceMapper

__all__ = [
    "ConceptService",
    "ConceptValidationError",
    "IngestReport",
    "SourceIngestService",
    "SourceMapper",
    "UpsertResult",
]
