"""Der Budget-Wächter (§11.6).

Eine Zeile Tabelle in §11.6, aber die einzige harte Grenze zwischen einem fehlkonfigurierten Lauf
und einer Rechnung: "vor jedem Aufruf gegen ``budget.max_model_calls_per_run`` und
``max_estimated_cost_per_run_eur`` geprüft; bei ``on_exceed: abort`` endet der Lauf sauber mit
Teilergebnis."

Drei Entscheidungen stecken in dieser kleinen Klasse:

**Geprüft wird *vor* dem Aufruf.** Eine Grenze, die erst nach der Antwort greift, hat den Aufruf
schon bezahlt. Der Wächter arbeitet deshalb mit dem bisher Verbrauchten und lässt den nächsten
Aufruf zu oder nicht — er kann dessen Kosten nicht kennen, aber er kann verhindern, dass sie
überhaupt entstehen.

**Der Zählerstand kommt von außen.** Ein Lauf kann über mehrere Prozesse verteilt sein; der
Verbrauch steht in ``model_calls`` und nicht in einem Attribut. Der Wächter ist deshalb eine
reine Entscheidung über übergebene Zahlen und hält selbst keinen Zustand, den ein Neustart
verlöre.

**``warn`` ist keine abgeschwächte Grenze, sondern eine andere Aussage.** Es heißt: zählen und
weitermachen. Wer das wählt, hat sich entschieden, den Rahmen als Beobachtung zu führen und nicht
als Schranke — und soll dafür nicht mit einer stillen Halbierung des Ergebnisses bezahlen.
"""

from __future__ import annotations

from dataclasses import dataclass

from wissensgraph.config import defaults


@dataclass(frozen=True)
class BudgetVerdict:
    """Das Urteil des Wächters über den nächsten Aufruf."""

    allowed: bool
    exceeded: bool
    reason: str | None = None

    @property
    def warned(self) -> bool:
        """Ob der Rahmen gesprengt ist, der Aufruf aber trotzdem stattfinden darf."""
        return self.exceeded and self.allowed


class BudgetGuard:
    """Entscheidet vor jedem Modellaufruf, ob er noch im Rahmen liegt (§11.6)."""

    def __init__(
        self,
        *,
        max_calls: int,
        max_cost_eur: float,
        on_exceed: str = defaults.BUDGET_ON_EXCEED,
    ) -> None:
        """
        Args:
            max_calls: Höchstzahl Modellaufrufe je Lauf. ``0`` schaltet jeden Aufruf ab — ein
                zulässiger und ausdrücklich nützlicher Zustand: So läuft eine Pipeline vollständig
                durch, ohne ein einziges Token zu verbrauchen.
            max_cost_eur: Geschätzte Höchstkosten je Lauf.
            on_exceed: ``abort`` oder ``warn``.
        """
        self._max_calls = max_calls
        self._max_cost = max_cost_eur
        self._abort = on_exceed != "warn"

    def check(self, *, calls: int, cost_eur: float) -> BudgetVerdict:
        """Beurteilt den nächsten Aufruf anhand des bisher Verbrauchten.

        Args:
            calls: Wie viele Modellaufrufe dieser Lauf bereits verursacht hat.
            cost_eur: Wie viel er dabei geschätzt gekostet hat.

        Returns:
            Das Urteil. ``allowed=False`` heißt: Der Aufruf findet nicht statt.
        """
        if calls >= self._max_calls:
            return self._urteil(
                f"max_model_calls_per_run = {self._max_calls} erreicht",
            )
        if self._max_cost > 0.0 and cost_eur >= self._max_cost:
            return self._urteil(
                f"max_estimated_cost_per_run_eur = {self._max_cost} erreicht",
            )
        return BudgetVerdict(allowed=True, exceeded=False)

    def _urteil(self, grund: str) -> BudgetVerdict:
        """Übersetzt einen überschrittenen Rahmen in ein Urteil — je nach ``on_exceed``."""
        return BudgetVerdict(allowed=not self._abort, exceeded=True, reason=grund)
