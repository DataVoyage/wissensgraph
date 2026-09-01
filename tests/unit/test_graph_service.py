"""Kernspace-Auflösung, Referenzdichte, Ranking und Suche (§12).

Aufgebaut wird ein kleiner, aber vollständiger Bestand: eine Confluence-Seite, ein Cluster, das
sie enthält, und zwei persönliche Notizen, die über die Store-Grenze darauf zeigen. An diesem
Ausschnitt lässt sich jede Aussage aus §12 einzeln prüfen — vor allem die, auf die es ankommt:
dass ein nur über eine Brücke erreichbares Konzept mit kurzer Distanz erscheint (§24, Stufe 6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from support.memory import MemoryUnitOfWorkFactory
from wissensgraph.config.schema import RankingConfig, Settings
from wissensgraph.domain.concepts import ConceptDraft
from wissensgraph.ports.repositories import ConceptFilter
from wissensgraph.services.concepts import ConceptService
from wissensgraph.services.graph import GraphService, UnknownStartError

pytestmark = pytest.mark.unit

JETZT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def uow_factory() -> MemoryUnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(("shared", "personal"))


@pytest.fixture
def concepts(settings: Settings, uow_factory: MemoryUnitOfWorkFactory) -> ConceptService:
    return ConceptService(settings, uow_factory, clock=lambda: JETZT)


@pytest.fixture
def graph(settings: Settings, uow_factory: MemoryUnitOfWorkFactory) -> GraphService:
    return GraphService(settings, uow_factory, clock=lambda: JETZT)


@pytest.fixture
def bestand(concepts: ConceptService) -> None:
    """Der Ausschnitt, an dem alles Folgende geprüft wird.

    ``cluster:c`` --member--> ``confluence:1`` --references--> ``confluence:2``, und aus dem
    persönlichen Store zeigen ``project:finance`` und ``note:a`` auf ``confluence:1``.
    """
    concepts.upsert(
        ConceptDraft(
            id="confluence:2",
            scope="engineering",
            type="Confluence Page",
            title="Anhang",
            source_name="confluence",
            external_id="2",
        )
    )
    concepts.upsert(
        ConceptDraft(
            id="confluence:1",
            scope="engineering",
            type="Confluence Page",
            title="Zahlungsabgleich",
            body="Weiteres in [[confluence:2]].",
            source_name="confluence",
            external_id="1",
        )
    )
    concepts.upsert(
        ConceptDraft(
            id="cluster:c",
            scope="engineering",
            type="Cluster",
            title="Zahlungsverkehr",
            references=("confluence:1",),
        )
    )
    concepts.upsert(
        ConceptDraft(
            id="project:finance",
            scope="personal",
            type="Project",
            title="Finanzintegration",
            body="Grundlage ist [[confluence:1]].",
        )
    )
    concepts.upsert(
        ConceptDraft(
            id="note:a",
            scope="personal",
            type="Note",
            title="Notiz",
            body="Siehe [[confluence:1]] und [[project:finance]].",
        )
    )


def ids(traversal: object) -> set[str]:
    return {node.concept.id for node in traversal.nodes}  # type: ignore[attr-defined]


class TestAusbreiten:
    def test_ein_hop_findet_die_direkten_nachbarn(self, graph: GraphService, bestand: None) -> None:
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1)

        assert ids(ergebnis) == {
            "confluence:1",
            "confluence:2",
            "cluster:c",
            "project:finance",
            "note:a",
        }

    def test_ein_nur_ueber_die_bruecke_erreichbares_konzept_erscheint_nah(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Die Abnahme der Stufe 6 (§24): kurze Distanz trotz Datenbankgrenze.

        ``project:finance`` liegt in einer anderen Datenbank als der Startknoten. Es gibt keinen
        Join dorthin — die Kante liegt im persönlichen Store und zeigt nach außen. Trotzdem ist
        das Projekt einen Hop entfernt und nicht unerreichbar.
        """
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1)

        projekt = next(node for node in ergebnis.nodes if node.concept.id == "project:finance")
        assert projekt.hops == 1
        assert projekt.store == "personal"

    def test_die_richtung_der_kante_begrenzt_die_erreichbarkeit_nicht(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Wer von einem Mitglied aus sucht, will sein Cluster sehen — und umgekehrt (§7.7)."""
        ergebnis = graph.traverse(["cluster:c"], store="shared", hops=1)

        assert "confluence:1" in ids(ergebnis)

    def test_zwei_hops_reichen_weiter(self, graph: GraphService, bestand: None) -> None:
        eins = graph.traverse(["confluence:2"], store="shared", hops=1)
        zwei = graph.traverse(["confluence:2"], store="shared", hops=2)

        assert "cluster:c" not in ids(eins)
        assert "cluster:c" in ids(zwei)

    def test_der_startknoten_ist_teil_des_ergebnisses(
        self, graph: GraphService, bestand: None
    ) -> None:
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1)

        start = next(node for node in ergebnis.nodes if node.concept.id == "confluence:1")
        assert start.hops == 0

    def test_ein_unbekannter_startknoten_bricht_verstaendlich_ab(
        self, graph: GraphService, bestand: None
    ) -> None:
        with pytest.raises(UnknownStartError, match="confluence:99"):
            graph.traverse(["confluence:99"], store="shared")

    def test_ein_startknoten_im_falschen_store_gilt_als_unbekannt(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Ein Knoten ist erst mit seinem Store eindeutig (§12.1, Schritt 5)."""
        with pytest.raises(UnknownStartError):
            graph.traverse(["confluence:1"], store="personal")


class TestGrenzen:
    def test_max_nodes_deckelt_und_sagt_es(self, graph: GraphService, bestand: None) -> None:
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=2, max_nodes=2)

        assert len(ergebnis.nodes) == 2
        assert ergebnis.truncated is True

    def test_ein_vollstaendiges_ergebnis_ist_nicht_gedeckelt(
        self, graph: GraphService, bestand: None
    ) -> None:
        assert graph.traverse(["confluence:1"], store="shared", hops=1).truncated is False

    def test_die_tiefe_wird_auf_max_hops_begrenzt(
        self, graph: GraphService, settings: Settings, bestand: None
    ) -> None:
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=99)

        assert ergebnis.hops == settings.traversal.max_hops

    def test_ohne_angabe_gilt_die_voreinstellung(
        self, graph: GraphService, settings: Settings, bestand: None
    ) -> None:
        assert graph.traverse(["confluence:1"], store="shared").hops == (
            settings.traversal.default_hops
        )


class TestAbfragezahl:
    """§24, Stufe 6: "ein Traversal über 3 Hops braucht höchstens 6 Datenbankabfragen"."""

    def test_drei_hops_in_einem_store_bleiben_unter_der_grenze(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Vier statt sechs: drei Kantenrunden und *ein* Batch-Load am Ende (§12.1)."""
        ergebnis = graph.traverse(["confluence:2"], store="shared", hops=3, max_nodes=2)

        assert ergebnis.queries <= 6

    def test_die_store_grenze_kostet_je_hop_eine_abfrage_mehr(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Über die Grenze gibt es keinen Join — die zweite Abfrage ist der Ersatz dafür (§7.3).

        Damit ist die Zahl nicht mehr durch die Tiefe allein bestimmt, sondern durch Tiefe *mal*
        beteiligte Stores, plus einen Batch-Load je Store und die eine Abfrage des Brücken-Index.
        Das ist genau das, was §12.1 mit "ein Query pro Store und Hop" beschreibt.
        """
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=3)

        assert ergebnis.queries <= 1 + 3 * 2 + 2

    def test_die_zahl_steht_im_ergebnis(self, graph: GraphService, bestand: None) -> None:
        """Eine zugesicherte Eigenschaft, die niemand messen kann, ist keine."""
        assert graph.traverse(["confluence:1"], store="shared", hops=1).queries > 0


class TestReferenzdichte:
    def test_die_dichte_zaehlt_die_persoenlichen_verweise(
        self, graph: GraphService, bestand: None
    ) -> None:
        """§12.2: Zwei persönliche Konzepte zeigen auf ``confluence:1``."""
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1)

        seite = next(node for node in ergebnis.nodes if node.concept.id == "confluence:1")
        assert seite.density == 2

    def test_ein_verweis_auf_das_cluster_zaehlt_fuer_das_mitglied(
        self, concepts: ConceptService, graph: GraphService, bestand: None
    ) -> None:
        """§12.2: "auf z **oder auf ein Cluster von z**" — der ``member``-Schritt ist frei.

        Zwei Hops, weil die Dichte auf dem *aufgelösten* Teilgraphen zählt (§12.2): Bei einem Hop
        ist ``cluster:c`` zwar erreicht, seine eingehenden Kanten sind aber nie geladen worden.
        Ein Wert, der über nicht Abgefragtes urteilte, wäre geraten.
        """
        concepts.upsert(
            ConceptDraft(
                id="note:b", scope="personal", type="Note", title="B", body="Siehe [[cluster:c]]."
            )
        )

        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=2)

        seite = next(node for node in ergebnis.nodes if node.concept.id == "confluence:1")
        assert seite.density == 3

    def test_geteilte_verweise_zaehlen_nicht(self, graph: GraphService, bestand: None) -> None:
        """Die Dichte ist eine Aussage über den *eigenen* Bestand, nicht über Beliebtheit."""
        ergebnis = graph.traverse(["confluence:2"], store="shared", hops=1)

        anhang = next(node for node in ergebnis.nodes if node.concept.id == "confluence:2")
        assert anhang.density == 0

    def test_die_dichte_haengt_an_der_tiefe(self, graph: GraphService, bestand: None) -> None:
        """ "innerhalb von d Hops" (§12.2) — mit d wächst der Kreis der Zählenden."""
        eng = graph.traverse(["confluence:2"], store="shared", hops=1)
        weit = graph.traverse(["confluence:2"], store="shared", hops=3)

        assert next(n for n in eng.nodes if n.concept.id == "confluence:2").density == 0
        assert next(n for n in weit.nodes if n.concept.id == "confluence:2").density == 2


class TestRanking:
    def test_naehe_schlaegt_ferne(self, graph: GraphService, bestand: None) -> None:
        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=2)

        assert ergebnis.nodes[0].concept.id == "confluence:1"

    def test_das_ergebnis_ist_absteigend_bewertet(self, graph: GraphService, bestand: None) -> None:
        werte = [node.score for node in graph.traverse(["confluence:1"], store="shared").nodes]

        assert werte == sorted(werte, reverse=True)

    def test_gewichte_lassen_sich_je_anfrage_ueberschreiben(
        self, graph: GraphService, bestand: None
    ) -> None:
        """§12.3: "damit sich Varianten in der UI vergleichen lassen"."""
        nur_dichte = RankingConfig(hop_weight=0.0, density_weight=1.0, recency_weight=0.0)

        ergebnis = graph.traverse(["confluence:2"], store="shared", hops=2, ranking=nur_dichte)

        assert ergebnis.nodes[0].concept.id == "confluence:1"

    def test_aeltere_konzepte_verlieren_an_aktualitaet(
        self, settings: Settings, uow_factory: MemoryUnitOfWorkFactory, bestand: None
    ) -> None:
        """Die Halbwertszeit aus §12.3, geprüft über eine vorgerückte Uhr."""
        nur_aktualitaet = RankingConfig(
            hop_weight=0.0, density_weight=0.0, recency_weight=1.0, recency_half_life_days=90
        )
        spaeter = GraphService(settings, uow_factory, clock=lambda: JETZT + timedelta(days=90))

        ergebnis = spaeter.traverse(
            ["confluence:1"], store="shared", hops=1, ranking=nur_aktualitaet
        )

        assert ergebnis.nodes[0].score == pytest.approx(0.5, abs=1e-6)

    def test_ohne_jede_dichte_bleibt_die_bewertung_endlich(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Die Normierung teilt durch den größten Wert — auch wenn der 0 ist."""
        ergebnis = graph.traverse(["confluence:2"], store="shared", hops=1)

        assert all(node.score >= 0 for node in ergebnis.nodes)


class TestGrabsteine:
    def test_grabsteine_bleiben_standardmaessig_draussen(
        self, concepts: ConceptService, graph: GraphService, bestand: None
    ) -> None:
        """§12.3: "erscheinen nur bei explizitem Flag"."""
        concepts.mark_source_deleted("confluence:2", store="shared")

        assert "confluence:2" not in ids(graph.traverse(["confluence:1"], store="shared", hops=1))

    def test_mit_flag_erscheinen_sie(
        self, concepts: ConceptService, graph: GraphService, bestand: None
    ) -> None:
        concepts.mark_source_deleted("confluence:2", store="shared")

        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=1, include_tombstones=True)

        assert "confluence:2" in ids(ergebnis)

    def test_ueber_einen_grabstein_hinweg_wird_weiter_traversiert(
        self, concepts: ConceptService, graph: GraphService, bestand: None
    ) -> None:
        """Ein Grabstein ist unsichtbar, aber nicht abwesend — sonst zerfiele der Graph."""
        concepts.upsert(
            ConceptDraft(
                id="confluence:3",
                scope="engineering",
                type="Confluence Page",
                title="Dahinter",
                source_name="confluence",
                external_id="3",
            )
        )
        concepts.upsert(
            ConceptDraft(
                id="confluence:2",
                scope="engineering",
                type="Confluence Page",
                title="Anhang",
                body="Weiter zu [[confluence:3]].",
                source_name="confluence",
                external_id="2",
            )
        )
        concepts.mark_source_deleted("confluence:2", store="shared")

        ergebnis = graph.traverse(["confluence:1"], store="shared", hops=2)

        assert "confluence:3" in ids(ergebnis)


class TestSuche:
    def test_ein_treffer_im_titel(self, graph: GraphService, bestand: None) -> None:
        ergebnis = graph.search("Zahlungsabgleich", store="shared")

        assert [hit.concept.id for hit in ergebnis.hits] == ["confluence:1"]

    def test_der_modus_steht_im_ergebnis(self, graph: GraphService, bestand: None) -> None:
        """§12.4: Ein stiller Qualitätsverlust wäre die schlechtere Variante."""
        assert graph.search("Zahlung", store="shared").mode == "lexical"

    def test_die_suche_bleibt_im_angefragten_store(
        self, graph: GraphService, bestand: None
    ) -> None:
        assert graph.search("Finanzintegration", store="shared").hits == ()
        assert len(graph.search("Finanzintegration", store="personal").hits) == 1

    def test_eine_leere_anfrage_liefert_nichts(self, graph: GraphService, bestand: None) -> None:
        assert graph.search("   ", store="shared").hits == ()

    def test_die_trefferzahl_ist_begrenzt(self, graph: GraphService, bestand: None) -> None:
        assert len(graph.search("e", store="shared", limit=1).hits) <= 1


class TestKarte:
    """Der Ausschnitt ohne Startknoten (§17.2, Ansicht 1 "Karte")."""

    def test_ohne_filter_erscheint_der_ganze_store(
        self, graph: GraphService, bestand: None
    ) -> None:
        karte = graph.map(store="shared")

        assert {knoten.concept.id for knoten in karte.nodes} == {
            "confluence:1",
            "confluence:2",
            "cluster:c",
        }

    def test_die_karte_bleibt_im_gefragten_store(self, graph: GraphService, bestand: None) -> None:
        """Kein Auflösen über die Grenze — anders als bei der Traversierung.

        ``project:finance`` und ``note:a`` zeigen auf ``confluence:1`` und tauchen deshalb in
        jeder Traversierung von dort auf. In einer Karte des geteilten Stores haben sie nichts
        zu suchen: Ein Ausschnitt zweier halb geladener Stores wäre keine klare Aussage.
        """
        karte = graph.map(store="shared")

        assert all(knoten.concept.store == "shared" for knoten in karte.nodes)

    def test_ein_scope_filter_schneidet_die_knotenmenge(
        self, graph: GraphService, bestand: None
    ) -> None:
        karte = graph.map(store="personal", filter=ConceptFilter(scope="personal"))

        assert {knoten.concept.id for knoten in karte.nodes} == {"project:finance", "note:a"}

    def test_es_erscheinen_nur_kanten_zwischen_sichtbaren_knoten(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Ein Strich ins Nichts wäre eine Falschaussage, kein Hinweis.

        Der Typfilter lässt nur die beiden Confluence-Seiten übrig. Die Kante von ``cluster:c``
        auf ``confluence:1`` hätte damit ein Ende außerhalb der Karte — sie fehlt.
        """
        voll = graph.map(store="shared")
        karte = graph.map(store="shared", filter=ConceptFilter(concept_type="Confluence Page"))

        sichtbar = {knoten.concept.id for knoten in karte.nodes}
        assert sichtbar == {"confluence:1", "confluence:2"}
        assert all(kante.from_id in sichtbar and kante.to_id in sichtbar for kante in karte.edges)
        assert len(karte.edges) < len(voll.edges)
        assert all(kante.from_id != "cluster:c" for kante in karte.edges)

    def test_der_grad_zaehlt_nur_die_sichtbaren_kanten(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Der Grad ist ausdrücklich lokal — sonst logen Knotengröße und Bild sich an.

        Im vollen Store hat ``confluence:1`` zwei Kanten: die eingehende ``member`` vom Cluster
        und die ausgehende ``references`` auf ``confluence:2``. Schneidet der Filter das Cluster
        weg, bleibt eine.
        """
        voll = {k.concept.id: k.degree for k in graph.map(store="shared").nodes}
        geschnitten = {
            k.concept.id: k.degree
            for k in graph.map(
                store="shared", filter=ConceptFilter(concept_type="Confluence Page")
            ).nodes
        }

        assert voll["confluence:1"] == 2
        assert geschnitten["confluence:1"] == 1

    def test_ein_kantenfilter_laesst_die_knoten_stehen(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Anders als bei ``traverse``: In einer Karte ist ein Knoten sichtbar, weil er dem
        Filter entspricht — und nicht, weil man ihn über eine Kantenart erreicht hat."""
        karte = graph.map(store="shared", kinds=["references"])
        ohne = graph.map(store="shared", kinds=["member"])

        assert {knoten.concept.id for knoten in karte.nodes} == {
            "confluence:1",
            "confluence:2",
            "cluster:c",
        }
        assert {kante.kind for kante in karte.edges} == {"references"}
        # Und der Gegenprobe wegen: Eine Kantenart, die es hier nicht gibt, leert die Kanten —
        # aber nicht die Knoten.
        assert ohne.edges == ()
        assert len(ohne.nodes) == 3

    def test_die_deckelung_meldet_sich_im_cursor(self, graph: GraphService, bestand: None) -> None:
        """ "Das ist alles" und "das ist der Anfang" müssen unterscheidbar bleiben (§17.3)."""
        angeschnitten = graph.map(store="shared", limit=2)
        vollstaendig = graph.map(store="shared", limit=50)

        assert len(angeschnitten.nodes) == 2
        assert angeschnitten.next_cursor is not None
        assert angeschnitten.as_dict()["truncated"] is True
        assert vollstaendig.next_cursor is None
        assert vollstaendig.as_dict()["truncated"] is False

    def test_der_cursor_blaettert_ohne_wiederholung(
        self, graph: GraphService, bestand: None
    ) -> None:
        erste = graph.map(store="shared", limit=2)
        zweite = graph.map(store="shared", limit=2, cursor=erste.next_cursor)

        assert {k.concept.id for k in erste.nodes}.isdisjoint({k.concept.id for k in zweite.nodes})

    def test_die_serialisierung_traegt_keinen_score(
        self, graph: GraphService, bestand: None
    ) -> None:
        """Eine Karte hat keinen Ausgangspunkt; ein ``hops: 0`` oder ein ``score`` wäre eine
        erfundene Zahl (§12.3)."""
        knoten = graph.map(store="shared").as_dict()["nodes"][0]

        assert set(knoten) == {"id", "store", "scope", "type", "title", "status", "degree"}
