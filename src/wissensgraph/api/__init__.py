"""HTTP-API (§16) — eine der drei dünnen Hüllen um den Kern (Leitprinzip 14)."""

from __future__ import annotations

from wissensgraph.api.app import create_app

__all__ = ["create_app"]
