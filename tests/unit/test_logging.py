"""Tests des strukturierten Loggings (§21.1, §20.2)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from wissensgraph.config.defaults import SECRET_MASK
from wissensgraph.observability.logging import (
    REQUIRED_FIELDS,
    configure_logging,
    drop_forbidden_fields,
    ensure_required_fields,
    get_logger,
    mask_secret_fields,
)

pytestmark = pytest.mark.unit


class TestDropForbiddenFields:
    def test_entfernt_body(self) -> None:
        result = drop_forbidden_fields(None, "info", {"concept_id": "confluence:1", "body": "..."})

        assert result == {"concept_id": "confluence:1"}

    def test_entfernt_weitere_inhaltsfelder(self) -> None:
        event = {"prompt": "geheim", "text": "auch", "content": "und das", "id": "bleibt"}

        assert drop_forbidden_fields(None, "info", event) == {"id": "bleibt"}

    def test_behaelt_konzept_ids(self) -> None:
        # §21.1: "Konzept-IDs ja, body nie."
        event = {"concept_id": "jira:PROJ-1", "cluster_id": "cluster:abc"}

        assert drop_forbidden_fields(None, "info", dict(event)) == event


class TestMaskSecretFields:
    def test_maskiert_token(self) -> None:
        result = mask_secret_fields(None, "info", {"api_token": "geheim", "port": 8080})

        assert result == {"api_token": SECRET_MASK, "port": 8080}

    def test_laesst_none_unveraendert(self) -> None:
        assert mask_secret_fields(None, "info", {"token": None}) == {"token": None}


class TestEnsureRequiredFields:
    def test_ergaenzt_alle_pflichtfelder(self) -> None:
        processor = ensure_required_fields("api")

        result = processor(None, "info", {"event": "gestartet"})

        for field in REQUIRED_FIELDS:
            assert field in result

    def test_setzt_service_namen(self) -> None:
        processor = ensure_required_fields("worker")

        assert processor(None, "info", {})["service"] == "worker"

    def test_ueberschreibt_gesetzte_werte_nicht(self) -> None:
        processor = ensure_required_fields("api")

        result = processor(None, "info", {"run_id": "abc", "service": "cli"})

        assert result["run_id"] == "abc"
        assert result["service"] == "cli"


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _reset_structlog(self) -> Any:
        yield
        structlog.reset_defaults()

    def test_json_format_erzeugt_gueltiges_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(level="INFO", log_format="json", service="api")

        get_logger("test").info("lauf_gestartet", concept_id="note:1", body="darf nicht erscheinen")

        entry = json.loads(capsys.readouterr().out.strip())
        assert entry["event"] == "lauf_gestartet"
        assert entry["concept_id"] == "note:1"
        assert "body" not in entry
        assert entry["service"] == "api"
        for field in REQUIRED_FIELDS:
            assert field in entry

    def test_console_format_ist_kein_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(level="INFO", log_format="console", service="cli")

        get_logger("test").info("hallo")

        output = capsys.readouterr().out
        assert "hallo" in output
        with pytest.raises(json.JSONDecodeError):
            json.loads(output.strip())

    def test_fremde_bibliotheken_werden_ebenso_strukturiert(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Alembic, SQLAlchemy und uvicorn loggen über die Standardbibliothek.

        Ohne die Verdrahtung in :func:`configure_logging` liefen ihre Einträge an den
        Pflichtfeldern aus §21.1 und an der Secret-Maskierung aus §20.2 vorbei.
        """
        import logging

        configure_logging(level="INFO", log_format="json", service="api")

        logging.getLogger("alembic.runtime.migration").info("Running upgrade")

        entry = json.loads(capsys.readouterr().out.strip())
        assert entry["event"] == "Running upgrade"
        for field in REQUIRED_FIELDS:
            assert field in entry

    def test_secrets_fremder_bibliotheken_werden_maskiert(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """§20.2 gilt unabhängig davon, wer den Eintrag geschrieben hat."""
        import logging

        configure_logging(level="INFO", log_format="json", service="api")

        logging.getLogger("fremd").info("verbindung", extra={"api_key": "sehr-geheim"})

        entry = json.loads(capsys.readouterr().out.strip())
        assert "sehr-geheim" not in json.dumps(entry)

    def test_level_filtert_leisere_eintraege(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(level="WARNING", log_format="json", service="api")

        get_logger("test").info("wird_verworfen")

        assert capsys.readouterr().out == ""

    def test_unbekanntes_level_faellt_auf_info_zurueck(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level="GIBTSNICHT", log_format="json", service="api")

        get_logger("test").info("erscheint")

        assert "erscheint" in capsys.readouterr().out

    def test_secrets_werden_auch_bei_debug_maskiert(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # §20.2: maskiert "unabhängig vom Log-Level".
        configure_logging(level="DEBUG", log_format="json", service="api")

        get_logger("test").debug("konfiguriert", api_token="sehr-geheim")

        entry = json.loads(capsys.readouterr().out.strip())
        assert entry["api_token"] == SECRET_MASK
