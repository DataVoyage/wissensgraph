"""Die Richtung einer Brücke und die Store-Policy (§12.1, §11.5, §20.1).

Zwei kleine Module mit je einer Regel, und beide Regeln entscheiden über die Datenschutzgrenze.
Sie werden hier ohne Datenbank, ohne Netzwerk und ohne Konfigurationsdatei geprüft — genau das
ist der Grund, warum sie im Domänenkern stehen und nicht im Router oder im Repository.
"""

from __future__ import annotations

import pytest

from wissensgraph.domain.bridges import (
    bridge_sources,
    bridge_targets,
    may_bridge,
    resolution_order,
)
from wissensgraph.domain.policies import ProviderNotAllowedError, check_store_policy

pytestmark = pytest.mark.unit

STORES = ("shared", "personal")


class TestRichtung:
    def test_innerhalb_eines_stores_ist_alles_erlaubt(self) -> None:
        assert may_bridge(from_store="shared", to_store="shared") is True
        assert may_bridge(from_store="personal", to_store="personal") is True

    def test_persoenlich_darf_auf_geteilt_zeigen(self) -> None:
        """§12.1: "Kanten von personal nach shared sind erlaubt und der Normalfall.\""""
        assert may_bridge(from_store="personal", to_store="shared") is True

    def test_geteilt_darf_nicht_auf_persoenlich_zeigen(self) -> None:
        """Der geteilte Store weiß nicht, dass es persönliche Konzepte gibt (§12.1).

        Die Umkehrung ist keine Kleinigkeit: Stünde die ID einer persönlichen Notiz im geteilten
        Store, verließe die Information den Rechner in dem Augenblick, in dem dieser Store auf
        einen zentralen Server zieht — noch bevor ein einziges Inhaltsfeld die Grenze überquert.
        """
        assert may_bridge(from_store="shared", to_store="personal") is False


class TestZiellisten:
    def test_der_geteilte_store_hat_keine_brueckenziele(self) -> None:
        assert bridge_targets("shared", STORES) == ()

    def test_der_persoenliche_store_zeigt_auf_den_geteilten(self) -> None:
        assert bridge_targets("personal", STORES) == ("shared",)

    def test_brueckenquellen_sind_die_umkehrung(self) -> None:
        assert bridge_sources("shared", STORES) == ("personal",)
        assert bridge_sources("personal", STORES) == ()

    def test_der_eigene_store_wird_zuerst_befragt(self) -> None:
        """Ein Verweis '[[note:abc]]' in einer Notiz meint die Notiz und nicht etwas Fremdes."""
        assert resolution_order("personal", STORES) == ("personal", "shared")

    def test_die_reihenfolge_folgt_der_konfiguration(self) -> None:
        """Sonst erzeugten zwei Läufe über denselben Bestand verschiedene Kanten."""
        assert bridge_targets("personal", ("a", "shared", "b")) == ("a", "shared", "b")


class TestStorePolicy:
    def test_geteilte_inhalte_duerfen_ueberallhin(self) -> None:
        check_store_policy(
            store="shared",
            provider="gemini",
            provider_is_local=False,
            allow_remote_personal=False,
        )

    def test_persoenliche_inhalte_duerfen_an_lokale_provider(self) -> None:
        check_store_policy(
            store="personal",
            provider="ollama",
            provider_is_local=True,
            allow_remote_personal=False,
        )

    def test_persoenliche_inhalte_an_einen_fernen_provider_werfen(self) -> None:
        """§20.1, Guard 3 — und §11.5: nie ein stiller Ausweichpfad, sondern ein Fehler."""
        with pytest.raises(ProviderNotAllowedError, match="gemini"):
            check_store_policy(
                store="personal",
                provider="gemini",
                provider_is_local=False,
                allow_remote_personal=False,
            )

    def test_die_ausnahme_laesst_sich_bewusst_einschalten(self) -> None:
        check_store_policy(
            store="personal",
            provider="gemini",
            provider_is_local=False,
            allow_remote_personal=True,
        )

    def test_die_meldung_nennt_den_weg_zurueck(self) -> None:
        """Ein Fehler, der nur verbietet, führt zu Ratlosigkeit oder zu einem groben Workaround."""
        with pytest.raises(ProviderNotAllowedError, match="WG_PERSONAL_ALLOW_REMOTE_MODELS"):
            check_store_policy(
                store="personal",
                provider="vertex",
                provider_is_local=False,
                allow_remote_personal=False,
            )
