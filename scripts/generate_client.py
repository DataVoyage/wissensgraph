"""Erzeugt den TypeScript-Client aus dem OpenAPI-Schema (§24, Stufe 11).

Aufruf::

    uv run python scripts/generate_client.py

Warum generiert und nicht von Hand geschrieben: Die Eingabeformen der API stehen als
Pydantic-Modelle in :mod:`wissensgraph.api.schemas`, und eine handgepflegte TypeScript-Abschrift
davon driftet mit der ersten Änderung ab — still, weil ein zu viel gesendetes Feld keinen Fehler
erzeugt, sondern schlicht ignoriert wird. Ein Generator macht die Abweichung sichtbar: Nach einer
Schemaänderung ist die erzeugte Datei anders, und der Typprüfer der UI meldet, wo.

Was **nicht** generiert wird, sind die Antwortformen. Sie entstehen in den Diensten als
``as_dict()`` und stehen deshalb nicht vollständig im OpenAPI-Schema; sie von dort abzuleiten
hieße, eine Genauigkeit zu behaupten, die es nicht gibt. Die UI beschreibt sie in
``ui/src/api/types.ts`` von Hand und sagt damit, was sie tatsächlich benutzt.

Das Schema kommt aus der Anwendung selbst und nicht über HTTP: Der Generator braucht damit keinen
laufenden Server, keine Datenbank und keine Zugangsdaten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

ZIEL = REPO_ROOT / "ui" / "src" / "api" / "schema.ts"

KOPF = """/**
 * ERZEUGT — NICHT VON HAND ÄNDERN.
 *
 * Quelle: das OpenAPI-Schema der HTTP-API (§16.1).
 * Neu erzeugen mit: `uv run python scripts/generate_client.py`
 *
 * Enthalten sind die Eingabeformen der API und die Liste ihrer Endpunkte. Die Antwortformen
 * stehen in `types.ts` und sind bewusst von Hand beschrieben: Sie entstehen in den Diensten und
 * nicht in einem Schema, und eine abgeleitete Beschreibung behauptete eine Genauigkeit, die es
 * nicht gibt.
 */

"""


def _typ(schema: dict[str, Any], komponenten: dict[str, Any]) -> str:
    """Übersetzt ein JSON-Schema in einen TypeScript-Typ."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        varianten = [_typ(teil, komponenten) for teil in schema["anyOf"]]
        return " | ".join(dict.fromkeys(varianten))
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(wert) for wert in schema["enum"])
    art = schema.get("type")
    if art == "array":
        inhalt = _typ(schema.get("items", {}), komponenten)
        return f"Array<{inhalt}>"
    if art == "object":
        werte = schema.get("additionalProperties")
        inhalt = "unknown" if werte in (None, True) else _typ(werte, komponenten)
        return f"Record<string, {inhalt}>"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(art), "unknown")


def _schnittstelle(name: str, schema: dict[str, Any], komponenten: dict[str, Any]) -> str:
    """Baut eine TypeScript-Schnittstelle aus einem Komponenten-Schema."""
    pflicht = set(schema.get("required", ()))
    zeilen = [f"export interface {name} {{"]
    if beschreibung := schema.get("description"):
        zeilen.insert(0, f"/** {beschreibung.splitlines()[0]} */")
    for feld, teil in schema.get("properties", {}).items():
        marke = "" if feld in pflicht else "?"
        zeilen.append(f"  {feld}{marke}: {_typ(teil, komponenten)};")
    zeilen.append("}")
    return "\n".join(zeilen)


def erzeuge(schema: dict[str, Any]) -> str:
    """Baut den Inhalt der erzeugten Datei."""
    komponenten = schema.get("components", {}).get("schemas", {})
    teile = [KOPF]

    for name in sorted(komponenten):
        # Die Fehlerformen von FastAPI selbst bleiben draußen: Die API antwortet nach RFC 7807
        # (§16.1), und ``HTTPValidationError`` beschriebe eine Form, die es dort nicht gibt.
        if name in {"HTTPValidationError", "ValidationError"}:
            continue
        teile.append(_schnittstelle(name, komponenten[name], komponenten))

    pfade = sorted(schema.get("paths", {}))
    teile.append(
        "/** Alle Pfade der API — eine Vertippsicherung für den Client. */\n"
        "export type ApiPath =\n" + "\n".join(f"  | {json.dumps(pfad)}" for pfad in pfade) + ";"
    )
    return "\n\n".join(teile) + "\n"


def main() -> int:
    """Schreibt die erzeugte Datei und meldet, ob sie sich geändert hat."""
    from wissensgraph.api.app import create_app
    from wissensgraph.config import defaults
    from wissensgraph.config.schema import Settings

    # Eine Konfiguration, die nur für die Schemaerzeugung reicht: Es wird keine Verbindung
    # aufgebaut und kein Lauf gestartet, nur die Routentabelle gelesen.
    settings = Settings.model_validate(
        {
            "stores": {
                "shared": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
                "personal": {"dsn": "sqlite+pysqlite:///:memory:", "allow_remote": False},
            },
            "scopes": [
                {"name": "engineering", "store": defaults.STORE_SHARED},
                {"name": "personal", "store": defaults.STORE_PERSONAL},
            ],
            "concept_types": [
                {"name": "Note", "stores": [defaults.STORE_PERSONAL]},
                {"name": defaults.CONCEPT_TYPE_CLUSTER, "stores": ["shared", "personal"]},
            ],
            "edge_kinds": {
                "structural": [defaults.EDGE_KIND_MEMBER, defaults.EDGE_KIND_RELATED],
                "semantic": [defaults.EDGE_KIND_REFERENCES],
            },
            "api": {"auth_mode": "token", "token": "schema"},
            "embedding_dim": 768,
        }
    )
    inhalt = erzeuge(create_app(settings).openapi())
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    vorher = ZIEL.read_text(encoding="utf-8") if ZIEL.exists() else ""
    ZIEL.write_text(inhalt, encoding="utf-8")
    print(f"{'geändert' if inhalt != vorher else 'unverändert'}: {ZIEL.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
