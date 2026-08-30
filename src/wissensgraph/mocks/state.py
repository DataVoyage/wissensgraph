"""Der Zustand des Mock-Quellservers (§9.1, §9.3).

§9.1 legt den Ansatz fest: "Gemockt wird **nicht der Adapter, sondern das Quellsystem**." Der
Unterschied ist der ganze Punkt dieser Stufe. Ein gemockter Adapter würde beweisen, dass der Kern
mit einem Adapter umgehen kann, der sich wie erwartet verhält. Ein gemocktes Quellsystem beweist,
dass der *echte* Adapter mit Paginierung, Rate-Limits und Fehlerantworten umgehen kann — genau
mit den Dingen, "die man gegen ein Live-System nicht provozieren kann" (§9.3).

Der Zustand liegt deshalb hier und nicht in den Endpunkten: Er wird geladen, verändert (Szenario,
Latenz, erzwungene Fehler) und zurückgesetzt, und die Endpunkte lesen ihn nur.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Dateinamen der Seed-Daten (§9.2).
SPACES_FILE = "spaces.json"
LINKS_FILE = "links.json"
BOARDS_FILE = "boards.json"
PAGES_DIR = "pages"
ISSUES_DIR = "issues"
CONFLUENCE_DIR = "confluence"
JIRA_DIR = "jira"
SCENARIOS_DIR = "scenarios"

#: Systemnamen in den Szenariodateien.
SYSTEM_CONFLUENCE = "confluence"
SYSTEM_JIRA = "jira"


class FixturesNotFound(RuntimeError):
    """Das Seed-Verzeichnis fehlt oder ist unvollständig."""


class ScenarioNotFound(KeyError):
    """Ein Szenario mit diesem Namen gibt es nicht."""


@dataclass
class FailRule:
    """Eine erzwungene Fehlerantwort (§9.3, ``POST /_control/fail``).

    ``after_requests`` ist der Grund, warum das mehr ist als ein Schalter: Damit lässt sich ein
    Abbruch *mitten* in einer Iteration auslösen — der Fall aus §22.3, in dem der Cursor
    unverändert bleiben muss. Ein Fehler ab der ersten Anfrage würde das nie prüfen.
    """

    status: int = 500
    count: int = 1
    after_requests: int = 0
    retry_after: float | None = None
    path_prefix: str | None = None

    def matches(self, path: str) -> bool:
        """Ob diese Regel für einen Pfad gilt."""
        return self.path_prefix is None or path.startswith(self.path_prefix)

    @property
    def exhausted(self) -> bool:
        """Ob die Regel aufgebraucht ist."""
        return self.count <= 0


@dataclass
class MockState:
    """Alles, was der Mock-Server ausliefert und was die Steuerungs-API verändert."""

    fixtures_dir: Path
    spaces: list[dict[str, Any]] = field(default_factory=list)
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    boards: list[dict[str, Any]] = field(default_factory=list)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted_pages: list[str] = field(default_factory=list)
    deleted_issues: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0
    fail: FailRule | None = None
    applied_scenarios: list[str] = field(default_factory=list)
    request_count: int = 0

    # -- Laden und Zurücksetzen -------------------------------------------------

    @classmethod
    def from_fixtures(cls, fixtures_dir: Path) -> MockState:
        """Baut den Anfangszustand aus dem Seed-Verzeichnis (§9.2).

        Raises:
            FixturesNotFound: Wenn das Verzeichnis fehlt. Ein Mock-Server ohne Daten ist keine
                halbe Hilfe, sondern eine Fehlerquelle: Jeder Lauf gegen ihn meldete "null
                Objekte", und das sieht aus wie eine leere Quelle statt wie ein
                Konfigurationsfehler.
        """
        if not fixtures_dir.is_dir():
            raise FixturesNotFound(
                f"Seed-Verzeichnis '{fixtures_dir}' existiert nicht. Erwartet wird der Aufbau aus "
                f"§9.2 mit '{CONFLUENCE_DIR}/' und '{JIRA_DIR}/'."
            )
        state = cls(fixtures_dir=fixtures_dir)
        state.reset()
        return state

    def reset(self) -> None:
        """Setzt auf den Seed-Zustand zurück (§9.3, ``POST /_control/reset``).

        Latenz und erzwungene Fehler fallen mit zurück: Ein Test, der ein Rate-Limit erzwungen
        hat und danach vergisst aufzuräumen, würde sonst jeden folgenden Test vergiften.
        """
        confluence = self.fixtures_dir / CONFLUENCE_DIR
        jira = self.fixtures_dir / JIRA_DIR

        self.spaces = _read_list(confluence / SPACES_FILE)
        self.pages = {str(page["id"]): page for page in _read_dir(confluence / PAGES_DIR, key="id")}
        self.links = {
            str(key): [str(item) for item in value]
            for key, value in _read_mapping(confluence / LINKS_FILE).items()
        }
        self.boards = _read_list(jira / BOARDS_FILE)
        self.issues = {
            str(issue["key"]): issue for issue in _read_dir(jira / ISSUES_DIR, key="key")
        }

        self.deleted_pages = []
        self.deleted_issues = []
        self.latency_seconds = 0.0
        self.fail = None
        self.applied_scenarios = []
        self.request_count = 0

    # -- Szenarien (§9.3) -------------------------------------------------------

    def apply_scenario(self, name: str) -> dict[str, Any]:
        """Wendet ein Szenario an und meldet, was es getan hat.

        Szenarien sind additiv und kumulativ: Wer den Ausgangszustand will, ruft ``reset`` auf.
        So lassen sich Änderung und Löschung nacheinander prüfen, ohne dazwischen neu zu laden.

        Raises:
            ScenarioNotFound: Wenn es keine Datei dieses Namens gibt.
        """
        pfad = self.fixtures_dir / SCENARIOS_DIR / f"{name}.json"
        if not pfad.is_file():
            verfuegbar = sorted(
                item.stem for item in (self.fixtures_dir / SCENARIOS_DIR).glob("*.json")
            )
            raise ScenarioNotFound(
                f"Szenario '{name}' gibt es nicht. Verfügbar: {', '.join(verfuegbar) or '—'}."
            )
        szenario = json.loads(pfad.read_text(encoding="utf-8"))

        bericht = {
            SYSTEM_CONFLUENCE: self._apply_confluence(szenario.get(SYSTEM_CONFLUENCE, {})),
            SYSTEM_JIRA: self._apply_jira(szenario.get(SYSTEM_JIRA, {})),
        }
        self.applied_scenarios.append(name)
        return {"scenario": name, "description": szenario.get("description", ""), **bericht}

    def _apply_confluence(self, teil: dict[str, Any]) -> dict[str, int]:
        """Änderungen an den Confluence-Seiten."""
        for seite in teil.get("create", []):
            self.pages[str(seite["id"])] = seite
        for aenderung in teil.get("update", []):
            _update_page(self.pages, aenderung)
        for page_id in teil.get("delete", []):
            self.pages.pop(str(page_id), None)
            self.links.pop(str(page_id), None)
            if str(page_id) not in self.deleted_pages:
                self.deleted_pages.append(str(page_id))
        for page_id, ziele in teil.get("links", {}).items():
            self.links[str(page_id)] = [str(ziel) for ziel in ziele]
        return {
            "created": len(teil.get("create", [])),
            "updated": len(teil.get("update", [])),
            "deleted": len(teil.get("delete", [])),
        }

    def _apply_jira(self, teil: dict[str, Any]) -> dict[str, int]:
        """Änderungen an den Jira-Vorgängen."""
        for vorgang in teil.get("create", []):
            self.issues[str(vorgang["key"])] = vorgang
        for aenderung in teil.get("update", []):
            _update_issue(self.issues, aenderung)
        for key in teil.get("delete", []):
            self.issues.pop(str(key), None)
            if str(key) not in self.deleted_issues:
                self.deleted_issues.append(str(key))
        return {
            "created": len(teil.get("create", [])),
            "updated": len(teil.get("update", [])),
            "deleted": len(teil.get("delete", [])),
        }

    # -- Störungen (§9.3) -------------------------------------------------------

    def next_failure(self, path: str) -> FailRule | None:
        """Verbraucht eine erzwungene Fehlerantwort, falls für diesen Pfad eine ansteht."""
        self.request_count += 1
        regel = self.fail
        if regel is None or regel.exhausted or not regel.matches(path):
            return None
        if self.request_count <= regel.after_requests:
            return None
        regel.count -= 1
        return regel

    def as_dict(self) -> dict[str, Any]:
        """Der Zustand für ``GET /_control/state`` — Grundlage von Zusicherungen im Test."""
        return {
            "pages": len(self.pages),
            "issues": len(self.issues),
            "spaces": len(self.spaces),
            "boards": len(self.boards),
            "links": sum(len(ziele) for ziele in self.links.values()),
            "deleted_pages": list(self.deleted_pages),
            "deleted_issues": list(self.deleted_issues),
            "latency_seconds": self.latency_seconds,
            "fail": None if self.fail is None else vars(self.fail),
            "applied_scenarios": list(self.applied_scenarios),
            "request_count": self.request_count,
        }


# ---------------------------------------------------------------------------
# Lesen und Verändern der Rohdaten
# ---------------------------------------------------------------------------


def _read_list(path: Path) -> list[dict[str, Any]]:
    """Liest eine JSON-Datei mit einer Liste; eine fehlende Datei ergibt eine leere Liste."""
    if not path.is_file():
        return []
    inhalt = json.loads(path.read_text(encoding="utf-8"))
    return list(inhalt) if isinstance(inhalt, list) else []


def _read_mapping(path: Path) -> dict[str, Any]:
    """Liest eine JSON-Datei mit einem Mapping; eine fehlende Datei ergibt ein leeres."""
    if not path.is_file():
        return {}
    inhalt = json.loads(path.read_text(encoding="utf-8"))
    return dict(inhalt) if isinstance(inhalt, dict) else {}


def _read_dir(path: Path, *, key: str) -> list[dict[str, Any]]:
    """Liest ein Verzeichnis von Einzeldateien (§9.2: ``pages/*.json``), sortiert nach Namen."""
    if not path.is_dir():
        return []
    objekte = []
    for datei in sorted(path.glob("*.json")):
        inhalt = json.loads(datei.read_text(encoding="utf-8"))
        if isinstance(inhalt, dict) and key in inhalt:
            objekte.append(inhalt)
    return objekte


def _update_page(pages: dict[str, dict[str, Any]], aenderung: dict[str, Any]) -> None:
    """Schreibt die logischen Felder eines Szenarios in die Nutzlast einer Seite.

    Ein Szenario nennt ``title`` und ``body``, nicht ``body.storage.value``. Die Übersetzung
    gehört hierher: Wer ein Szenario schreibt, soll über den Inhalt nachdenken und nicht über die
    Verschachtelung der Confluence-Antwort.
    """
    seite = pages.get(str(aenderung["id"]))
    if seite is None:
        return
    if "title" in aenderung:
        seite["title"] = aenderung["title"]
    if "excerpt" in aenderung:
        seite["excerpt"] = aenderung["excerpt"]
    if "body" in aenderung:
        seite.setdefault("body", {}).setdefault("storage", {})["value"] = aenderung["body"]
    if "labels" in aenderung:
        seite.setdefault("metadata", {})["labels"] = [
            {"name": name} for name in aenderung["labels"]
        ]
    if "version_when" in aenderung:
        seite.setdefault("version", {})["when"] = aenderung["version_when"]


def _update_issue(issues: dict[str, dict[str, Any]], aenderung: dict[str, Any]) -> None:
    """Dasselbe für einen Jira-Vorgang."""
    vorgang = issues.get(str(aenderung["key"]))
    if vorgang is None:
        return
    felder = vorgang.setdefault("fields", {})
    if "summary" in aenderung:
        felder["summary"] = aenderung["summary"]
    if "description" in aenderung:
        felder["description"] = aenderung["description"]
    if "labels" in aenderung:
        felder["labels"] = list(aenderung["labels"])
    if "updated" in aenderung:
        felder["updated"] = aenderung["updated"]
    if "references" in aenderung:
        vorgang["references"] = list(aenderung["references"])
