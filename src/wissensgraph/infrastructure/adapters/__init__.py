"""Quell-Adapter und ihre Registry (§8, §23).

Die Umsetzungen des Ports :class:`~wissensgraph.ports.sources.SourceAdapter`. Der Kern kennt
keine davon namentlich — er kennt die Registry, und die Registry kennt ``sources.yaml``.
"""

from __future__ import annotations

from wissensgraph.infrastructure.adapters.base import BaseAdapter, HttpSourceAdapter
from wissensgraph.infrastructure.adapters.confluence import ConfluenceAdapter
from wissensgraph.infrastructure.adapters.fixture import FixtureAdapter
from wissensgraph.infrastructure.adapters.jira import JiraAdapter
from wissensgraph.infrastructure.adapters.jsonpath import JsonPath, JsonPathError
from wissensgraph.infrastructure.adapters.mapping import DocumentMapping, MappingError
from wissensgraph.infrastructure.adapters.registry import (
    BUILTIN_ADAPTERS,
    AdapterNotFound,
    AdapterRegistry,
    RegisteredSource,
)

__all__ = [
    "BUILTIN_ADAPTERS",
    "AdapterNotFound",
    "AdapterRegistry",
    "BaseAdapter",
    "ConfluenceAdapter",
    "DocumentMapping",
    "FixtureAdapter",
    "HttpSourceAdapter",
    "JiraAdapter",
    "JsonPath",
    "JsonPathError",
    "MappingError",
    "RegisteredSource",
]
