"""Der Embedding-Lauf (§13.1).

Der Schwerpunkt liegt auf den vier Regeln, die §13.1 aufstellt — und darauf, dass ein zweiter Lauf
über einen unveränderten Bestand wirklich nichts kostet. Das ist keine Nebensache: Es ist der
Unterschied zwischen einem Verfahren, das man nächtlich laufen lassen kann, und einem, das man
sich überlegen muss.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from support.semantik import DIM, baue, befuellen, konzept, korpus, models_config
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.ports.models import PromptSpec

pytestmark = pytest.mark.unit


class TestEinbetten:
    def test_alle_konzepte_eines_scopes_bekommen_einen_vektor(
        self, semantik_settings: Settings
    ) -> None:
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.considered == 14
        assert bericht.embedded == 14
        assert len(umgebung.state().embeddings) == 14

    def test_ein_zweiter_lauf_ueber_unveraenderten_bestand_tut_nichts(
        self, semantik_settings: Settings
    ) -> None:
        """§13.1: "Neu eingebettet wird nur, wenn ``source_hash`` vom ``content_hash`` abweicht"."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())
        umgebung.embeddings.run(scope="engineering")

        wieder = umgebung.embeddings.run(scope="engineering")

        assert wieder.considered == 0
        assert wieder.embedded == 0

    def test_ein_geaenderter_inhalt_wird_neu_eingebettet(self, semantik_settings: Settings) -> None:
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())
        umgebung.embeddings.run(scope="engineering")

        befuellen(
            umgebung,
            [konzept("confluence:100", title="Ganz anderer Titel", description="Neuer Inhalt.")],
        )
        wieder = umgebung.embeddings.run(scope="engineering")

        assert wieder.considered == 1
        assert wieder.embedded == 1

    def test_rebuild_fasst_alles_an(self, semantik_settings: Settings) -> None:
        """``wg embed --rebuild`` nach einem Modellwechsel bei gleicher Dimension (§11.7)."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())
        umgebung.embeddings.run(scope="engineering")

        wieder = umgebung.embeddings.run(scope="engineering", rebuild=True)

        assert wieder.considered == 14

    def test_der_body_geht_nicht_in_das_embedding_ein(self, semantik_settings: Settings) -> None:
        """§13.1: Er "würde lange Dokumente überproportional gewichten"."""
        umgebung = baue(semantik_settings)
        kurz = konzept("confluence:900", title="Titel", description="Beschreibung")
        lang = konzept(
            "confluence:901",
            title="Titel",
            description="Beschreibung",
            body="Ein sehr langer Text " * 200,
        )
        befuellen(umgebung, [kurz, lang])

        umgebung.embeddings.run(scope="engineering")

        with umgebung.uow("shared") as uow:
            a = uow.embeddings.get(concept_id=kurz.id, model_key="p:m")
            b = uow.embeddings.get(concept_id=lang.id, model_key="p:m")
        assert a == b

    def test_ein_konzept_ohne_titel_und_beschreibung_wird_uebersprungen(
        self, semantik_settings: Settings
    ) -> None:
        """Ein Vektor daraus wäre eine Aussage über nichts."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, [konzept("confluence:902", title="")])

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.skipped_empty == 1
        assert bericht.embedded == 0

    def test_grabsteine_werden_nicht_eingebettet(self, semantik_settings: Settings) -> None:
        from wissensgraph.domain.concepts import ConceptStatus

        umgebung = baue(semantik_settings)
        tot = konzept("confluence:903", title="Gelöscht", description="weg").model_copy(
            update={"status": ConceptStatus.TOMBSTONE}
        )
        befuellen(umgebung, [tot])

        assert umgebung.embeddings.run(scope="engineering").considered == 0


class TestBeschreibungErzeugen:
    def test_eine_fehlende_beschreibung_wird_einmal_erzeugt(
        self, semantik_settings: Settings
    ) -> None:
        """§13.1: "Fehlt eine ``description``, wird sie einmalig über Task ``summarization`` …"."""
        umgebung = baue(semantik_settings)
        befuellen(
            umgebung, [konzept("confluence:910", title="Ohne", body="Ein Fließtext mit Inhalt.")]
        )

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.described == 1
        gespeichert = umgebung.state().concepts["confluence:910"]
        assert gespeichert.description == "Eine erzeugte Beschreibung."
        assert gespeichert.generated_by is not None

    def test_der_content_hash_bleibt_dabei_unberuehrt(self, semantik_settings: Settings) -> None:
        """Sonst meldete der nächste Sync eine Änderung, die es in der Quelle nie gab."""
        umgebung = baue(semantik_settings)
        vorher = konzept("confluence:911", title="Ohne", body="Ein Fließtext mit Inhalt.")
        befuellen(umgebung, [vorher])

        umgebung.embeddings.run(scope="engineering")

        assert umgebung.state().concepts["confluence:911"].content_hash == vorher.content_hash

    def test_ein_zweiter_lauf_erzeugt_sie_nicht_erneut(self, semantik_settings: Settings) -> None:
        umgebung = baue(semantik_settings)
        befuellen(
            umgebung, [konzept("confluence:912", title="Ohne", body="Ein Fließtext mit Inhalt.")]
        )
        umgebung.embeddings.run(scope="engineering")

        assert umgebung.embeddings.run(scope="engineering").described == 0

    def test_ohne_body_wird_nichts_erzeugt(self, semantik_settings: Settings) -> None:
        """Aus nichts lässt sich nichts zusammenfassen — und ein Modellaufruf darauf wäre teuer."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, [konzept("confluence:913", title="Nur ein Titel")])

        assert umgebung.embeddings.run(scope="engineering").described == 0
        assert umgebung.clients.chat_client.calls == []

    def test_ein_misslungener_satz_bricht_den_lauf_nicht_ab(
        self, semantik_settings: Settings
    ) -> None:
        """Der Titel allein trägt weiter; ein Abbruch wäre der schlechtere Tausch."""

        def kaputt(prompt: PromptSpec) -> str:
            raise RuntimeError("Modell nicht erreichbar")

        umgebung = baue(semantik_settings, chat=kaputt)
        befuellen(
            umgebung, [konzept("confluence:914", title="Ohne", body="Ein Fließtext mit Inhalt.")]
        )

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.embedded == 1
        assert bericht.errors


class TestGrenzen:
    def test_die_store_policy_beendet_den_lauf_nicht(self, semantik_settings: Settings) -> None:
        """§11.5: "kein Fehler, sondern der Preis von Leitprinzip 2" — und er wird beziffert."""
        umgebung = baue(semantik_settings)
        befuellen(
            umgebung,
            [
                konzept(
                    "note:privat",
                    title="Privat",
                    description="Text",
                    scope="personal",
                    store="personal",
                    concept_type="Note",
                )
            ],
            store="personal",
        )

        bericht = umgebung.embeddings.run(scope="personal")

        assert bericht.skipped_policy == 1
        assert bericht.embedded == 0

    def test_ein_lokaler_anbieter_darf_persoenliche_inhalte_sehen(
        self, semantik_settings: Settings
    ) -> None:
        umgebung = baue(semantik_settings, models=models_config(dim=DIM, local=True))
        befuellen(
            umgebung,
            [
                konzept(
                    "note:privat",
                    title="Privat",
                    description="Text",
                    scope="personal",
                    store="personal",
                    concept_type="Note",
                )
            ],
            store="personal",
        )

        bericht = umgebung.embeddings.run(scope="personal")

        assert bericht.embedded == 1

    def test_ein_erschoepftes_budget_endet_mit_teilergebnis(
        self, minimal_config_dict: dict[str, Any]
    ) -> None:
        """§24, Stufe 7: "beendet den Lauf sauber mit Teilergebnis"."""
        knapp = Settings.model_validate(
            {
                **minimal_config_dict,
                "clustering": {"neighbors_k": 4},
                "budget": {"max_model_calls_per_run": 0},
            }
        )
        umgebung = baue(knapp)
        befuellen(umgebung, korpus())

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.budget_exceeded is True
        assert bericht.embedded == 0

    def test_ein_leerer_scope_ist_ein_erfolgreicher_lauf(self, semantik_settings: Settings) -> None:
        umgebung = baue(semantik_settings)

        bericht = umgebung.embeddings.run(scope="engineering")

        assert bericht.considered == 0
        assert bericht.as_dict()["embedded"] == 0


class TestBericht:
    def test_der_bericht_enthaelt_keine_inhalte(self, semantik_settings: Settings) -> None:
        """§21.1: In eine Lauf-Statistik gehören Zahlen und Namen, nie Inhalte."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        bericht = umgebung.embeddings.run(scope="engineering").as_dict()

        serialisiert = json.dumps(bericht, ensure_ascii=False)
        assert "Faktentabellen" not in serialisiert
        assert bericht["model_key"] == "p:m"

    def test_der_modellschluessel_steht_im_bericht(self, semantik_settings: Settings) -> None:
        """Ohne ihn ließe sich später nicht sagen, mit welchem Modell ein Bestand entstand."""
        umgebung = baue(semantik_settings)

        assert umgebung.embeddings.run(scope="engineering").model_key == "p:m"


class TestAufrufzahlen:
    def test_texte_gehen_gebuendelt_hinaus(self, semantik_settings: Settings) -> None:
        """Die Bündelgröße kommt aus der Route (§11.6, "Batching") — hier acht."""
        umgebung = baue(semantik_settings)
        befuellen(umgebung, korpus())

        umgebung.embeddings.run(scope="engineering")

        aufrufe = [
            call for call in umgebung.state().model_calls if call.task == defaults.TASK_EMBEDDING
        ]
        # 14 Texte, Bündel zu acht: zwei Aufrufe.
        assert len(aufrufe) == 2
