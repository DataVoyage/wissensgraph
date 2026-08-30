"""Mock-Quellen für die Entwicklung (§9).

Gemockt wird das Quellsystem, nicht der Adapter. Was der Kern in der Entwicklung sieht, ist
deshalb derselbe Codepfad wie im Betrieb — inklusive Paginierung, Rate-Limits und
Fehlerantworten.
"""

from __future__ import annotations

from wissensgraph.mocks.app import create_mock_app
from wissensgraph.mocks.state import FailRule, FixturesNotFound, MockState, ScenarioNotFound

__all__ = [
    "FailRule",
    "FixturesNotFound",
    "MockState",
    "ScenarioNotFound",
    "create_mock_app",
]
