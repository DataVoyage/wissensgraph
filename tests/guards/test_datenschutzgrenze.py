"""Die Guard-Tests der Datenschutzgrenze (§20.1).

§20.1 führt fünf Guard-Tests als Teil der Pflicht-Testsuite auf. Vier davon stehen hier, der
vierte — der CHECK-Constraint gegen Kanten nach ``personal`` — in
``test_store_invarianten.py``, weil er eine laufende PostgreSQL-Instanz braucht:

1. "Ein Modul, das den personal-Store öffnet, darf keine ausgehende Netzwerkverbindung aufbauen."
2. "``personal.allow_remote = false`` mit nicht-lokalem DSN muss den Start verhindern."
3. "Ein Router-Aufruf mit ``store = 'personal'`` gegen einen nicht-lokalen Provider muss werfen."
5. "Die MCP-Verbindung auf ``shared`` muss bei jedem Schreibversuch einen Datenbankfehler
   erzeugen." — der Teil, der ohne Datenbank prüfbar ist; der Rest im Integrationstest.

Der Unterschied zu einem gewöhnlichen Test ist die Fragestellung: nicht "tut die Funktion, was
sie soll", sondern "hält die Schutzregel auch dann, wenn jemand sie umgehen will".
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from wissensgraph.config.schema import Settings
from wissensgraph.domain.concepts import ConceptDraft
from wissensgraph.domain.policies import ProviderNotAllowedError, check_store_policy
from wissensgraph.infrastructure.db import StoreRegistry
from wissensgraph.infrastructure.db.migrations import upgrade_all
from wissensgraph.infrastructure.db.uow import UnitOfWorkFactory
from wissensgraph.services.concepts import ConceptService

pytestmark = pytest.mark.guard


class NetzwerkVersuch(AssertionError):
    """Es wurde eine ausgehende Verbindung aufgebaut, wo keine sein darf."""


@contextmanager
def kein_netz() -> Iterator[list[Any]]:
    """Lässt jeden ausgehenden Verbindungsaufbau scheitern und protokolliert ihn.

    Gepatcht wird ``socket.socket.connect`` und nicht eine höhere Schicht: Jede Bibliothek, die
    irgendetwas ins Netz schickt — HTTP-Client, Datenbanktreiber, Telemetrie —, kommt am Ende
    hier vorbei. Eine Prüfung auf der Ebene von ``httpx`` sagte nur etwas über ``httpx``.
    """
    versuche: list[Any] = []
    original = socket.socket.connect

    def verboten(self: socket.socket, address: Any) -> None:
        versuche.append(address)
        raise NetzwerkVersuch(f"Ausgehende Verbindung nach {address!r} versucht.")

    socket.socket.connect = verboten  # type: ignore[method-assign]
    try:
        yield versuche
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]


@pytest.mark.integration
class TestGuard1KeinNetzZugang:
    """§20.1, Guard 1: "darf keine ausgehende Netzwerkverbindung aufbauen".

    Gemeint ist die Aussage dahinter (Leitprinzip 2): Aus dem Bearbeiten persönlicher Inhalte
    entsteht kein Verkehr an irgendein anderes Ziel als den persönlichen Store selbst. Gesperrt
    wird deshalb jede Verbindung, die der Python-Prozess selbst aufbaut; der Store bleibt über
    libpq erreichbar. Ein Adapter, ein Modell-Provider oder eine Telemetriebibliothek, die sich
    hier einschlichen, ließen den Test sofort scheitern.
    """

    def test_der_patch_faengt_eine_echte_verbindung_ab(self) -> None:
        """Die Gegenprobe zuerst: Ein Test, der nichts abfangen kann, prüft nichts.

        192.0.2.1 ist TEST-NET-1 (RFC 5737) und braucht keine Namensauflösung — ein Hostname
        scheiterte schon beim Lookup, also *vor* dem Verbindungsaufbau, und der Patch käme nie
        zum Zug.
        """
        with kein_netz() as versuche, pytest.raises(NetzwerkVersuch):
            socket.create_connection(("192.0.2.1", 80), timeout=1)

        assert versuche == [("192.0.2.1", 80)]

    def test_ein_schreibvorgang_im_persoenlichen_store_geht_nicht_ins_netz(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Eine persönliche Notiz anlegen, während jeder Weg nach draußen versperrt ist.

        Der Datenbanktreiber ist davon nicht betroffen: ``psycopg`` spricht über libpq, und
        dessen Socket entsteht in C, nicht über ``socket.socket``. Genau das macht diesen Guard
        scharf — er sperrt alles, was der Python-Prozess selbst aufbauen könnte (HTTP-Client,
        Telemetrie, Modell-Provider), und lässt nur den Weg offen, der erlaubt ist.
        """
        upgrade_all(postgres_settings, postgres_registry)
        service = ConceptService(postgres_settings, UnitOfWorkFactory(postgres_registry))

        with kein_netz() as versuche:
            ergebnis = service.upsert(
                ConceptDraft(
                    id="note:geheim",
                    scope="personal",
                    type="Note",
                    title="Nichts davon verlässt den Rechner",
                    body="Ein Gedanke.",
                )
            )

        assert versuche == []
        assert ergebnis.store == "personal"

    def test_auch_das_lesen_geht_nicht_ins_netz(
        self, postgres_settings: Settings, postgres_registry: StoreRegistry
    ) -> None:
        """Traversierung und Suche sind derselben Regel unterworfen wie das Schreiben."""
        from wissensgraph.services.graph import GraphService

        upgrade_all(postgres_settings, postgres_registry)
        factory = UnitOfWorkFactory(postgres_registry)
        ConceptService(postgres_settings, factory).upsert(
            ConceptDraft(id="note:a", scope="personal", type="Note", title="Notiz")
        )

        with kein_netz() as versuche:
            ergebnis = GraphService(postgres_settings, factory).traverse(
                ["note:a"], store="personal"
            )

        assert versuche == []
        assert len(ergebnis.nodes) == 1


class TestGuard2StartVerhindern:
    """§20.1, Guard 2: "``personal.allow_remote = false`` mit nicht-lokalem DSN"."""

    def test_ein_entfernter_persoenlicher_store_verhindert_den_start(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        minimal_config_dict["stores"]["personal"]["dsn"] = (
            "postgresql+psycopg://wg:wg@db.example.com:5432/wg_personal"
        )

        with pytest.raises(ValueError, match="allow_remote"):
            Settings.model_validate(minimal_config_dict)

    def test_auch_der_nur_lesende_zugang_muss_lokal_sein(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """Eine lesende Verbindung, die den Rechner verlässt, verletzt Leitprinzip 2 genauso."""
        minimal_config_dict["stores"]["personal"]["readonly_dsn"] = (
            "postgresql+psycopg://wg:wg@db.example.com:5432/wg_personal"
        )

        with pytest.raises(ValueError, match="readonly_dsn"):
            Settings.model_validate(minimal_config_dict)

    def test_die_grenze_laesst_sich_nur_ausdruecklich_oeffnen(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """Erlaubt, aber sichtbar: ``wg doctor`` warnt danach (``check_personal_locality``)."""
        minimal_config_dict["stores"]["personal"]["dsn"] = (
            "postgresql+psycopg://wg:wg@db.example.com:5432/wg_personal"
        )
        minimal_config_dict["stores"]["personal"]["allow_remote"] = True

        assert Settings.model_validate(minimal_config_dict).stores["personal"].allow_remote


class TestGuard3ModellPolicy:
    """§20.1, Guard 3: "Ein Router-Aufruf mit ``store = 'personal'`` … muss werfen"."""

    def test_persoenliche_inhalte_gehen_nicht_an_einen_fernen_provider(self) -> None:
        with pytest.raises(ProviderNotAllowedError):
            check_store_policy(
                store="personal",
                provider="gemini",
                provider_is_local=False,
                allow_remote_personal=False,
            )

    def test_die_voreinstellung_der_konfiguration_ist_die_strenge(self, settings: Settings) -> None:
        """Ohne ``WG_PERSONAL_ALLOW_REMOTE_MODELS`` gilt die Regel — nicht die Ausnahme."""
        assert settings.personal_allow_remote_models is False

        with pytest.raises(ProviderNotAllowedError):
            check_store_policy(
                store="personal",
                provider="gemini",
                provider_is_local=False,
                allow_remote_personal=settings.personal_allow_remote_models,
            )
