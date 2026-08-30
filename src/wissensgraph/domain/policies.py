"""Store-Policy: welcher Provider welche Inhalte sehen darf (§11.5, §20.1).

§11.5 schließt eine Lücke, die eine reine Datenbanktrennung offen lässt:

    "Wenn persönliche Notizen zum Einbetten an eine Cloud-API gehen, verlässt der Inhalt den
    Rechner, obwohl die Datenbank ihn nie verlassen hat."

Die Regel dagegen ist knapp: Jeder Modellaufruf trägt seinen ``store``. Stammt der Inhalt aus
``personal``, darf ihn nur ein Provider sehen, der auf demselben Rechner läuft — es sei denn,
jemand hat die Ausnahme ausdrücklich eingeschaltet (``WG_PERSONAL_ALLOW_REMOTE_MODELS=true``).

**Warum diese Prüfung hier steht und nicht im Router.** Der Router kommt erst in Stufe 7. Die
Regel, die er einhalten muss, ist aber schon jetzt prüfbar — und §20.1 führt sie als einen der
fünf Guard-Tests der Pflicht-Testsuite. Sie in den Domänenkern zu legen hat einen zweiten Grund:
Es gibt damit genau eine Stelle, die entscheidet, und sie braucht dafür weder Netzwerk noch
Konfigurationsdatei noch Provider-Objekt. Der Router wird sie aufrufen; er wird sie nicht
nachbilden.

Ein Verstoß führt nie zu einem stillen Ausweichen auf einen erlaubten, aber schlechteren
Anbieter. §11.5 ist an dieser Stelle ausdrücklich: Er führt zu einem Fehler. Der Preis steht
ebenfalls dort — ohne lokalen Modellserver bleiben persönliche Konzepte ohne Embedding, "kein
Fehler, sondern der Preis von Leitprinzip 2".
"""

from __future__ import annotations

from wissensgraph.config import defaults


class ProviderNotAllowedError(PermissionError):
    """Ein Modellaufruf hätte Inhalte an einen dafür nicht zugelassenen Provider gegeben (§11.5).

    Bewusst ein :class:`PermissionError` und keine gewöhnliche ``ValueError``: Es ist kein
    Programmierfehler und kein ungültiger Wert, sondern eine verweigerte Berechtigung.
    """

    def __init__(self, *, store: str, provider: str) -> None:
        self.store = store
        self.provider = provider
        super().__init__(
            f"Inhalte aus dem Store '{store}' dürfen nicht an den Provider '{provider}' gehen: "
            f"Er läuft nicht auf diesem Rechner (§11.5, Leitprinzip 2). Zulässig sind hier nur "
            f"lokale Provider. Wer die Grenze bewusst öffnen will, setzt "
            f"WG_PERSONAL_ALLOW_REMOTE_MODELS=true — das wird protokolliert."
        )


def check_store_policy(
    *, store: str, provider: str, provider_is_local: bool, allow_remote_personal: bool
) -> None:
    """Prüft, ob Inhalte aus ``store`` an ``provider`` gegeben werden dürfen (§11.5).

    Args:
        store: Herkunft der zu verarbeitenden Inhalte.
        provider: Name des Providers — nur für die Fehlermeldung.
        provider_is_local: Ob der Provider auf demselben Rechner läuft. Die Beurteilung selbst
            gehört nicht hierher: Sie hängt an einer URL und damit an der Konfiguration
            (:func:`wissensgraph.config.network.is_local_dsn` beantwortet dieselbe Frage für
            Datenbanken).
        allow_remote_personal: Die bewusste Ausnahme aus ``WG_PERSONAL_ALLOW_REMOTE_MODELS``.

    Raises:
        ProviderNotAllowedError: Wenn persönliche Inhalte an einen nicht-lokalen Provider gingen.
    """
    if store != defaults.STORE_PERSONAL:
        return
    if provider_is_local or allow_remote_personal:
        return
    raise ProviderNotAllowedError(store=store, provider=provider)
