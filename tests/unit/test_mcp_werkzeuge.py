"""Die sieben Werkzeuge des MCP-Layers (§18).

Zwei der drei Abnahmekriterien aus §24, Stufe 12, stehen hier:

* Der Agent legt eine Notiz an, verlinkt sie auf ein ``shared``-Cluster und findet die Verbindung
  über ``graph_traverse`` sofort wieder.
* Eine Sitzung beginnt nachweislich mit ``graph_overview`` statt mit ``graph_search``.

Das dritte — ein Schreibversuch auf ``shared`` scheitert auf **Datenbankebene** — gehört nach
``tests/guards``: Es ist eine Aussage über die Verbindung und nicht über den Code, und mit
speicherresidenten Fakes ließe es sich nur behaupten, nicht prüfen.
"""

from __future__ import annotations

from typing import Any

import pytest

from support.semantik import DIM, baue, befuellen, konzept, korpus
from wissensgraph.config import defaults
from wissensgraph.config.schema import Settings
from wissensgraph.mcp.tools import TRUNCATED_KEY, Toolbox, ToolError
from wissensgraph.services.catalog import CatalogService
from wissensgraph.services.curation import CurationService

pytestmark = pytest.mark.unit

SITZUNG = "sitzung-1"


@pytest.fixture
def umgebung(semantik_settings: Settings) -> Any:
    """Der Testkorpus mit Embeddings und Clustern — der Bestand, den ein Agent vorfindet."""
    aufbau = baue(semantik_settings)
    befuellen(aufbau, korpus())
    befuellen(
        aufbau,
        [
            konzept(
                "note:eigen",
                title="Meine Notiz",
                description="Was ich mir gemerkt habe.",
                scope="personal",
                store="personal",
                concept_type="Note",
            )
        ],
        store="personal",
    )
    aufbau.embeddings.run(scope="engineering")
    aufbau.clusters.run(scope="engineering")
    aufbau.clusters.run(scope="engineering")
    return aufbau


@pytest.fixture
def werkzeuge(semantik_settings: Settings, umgebung: Any) -> Toolbox:
    return Toolbox(
        semantik_settings,
        graph=umgebung.graph,
        catalog=CatalogService(semantik_settings, umgebung.uow, router=umgebung.router),
        curation=CurationService(semantik_settings, umgebung.uow),
        clusters=umgebung.clusters,
        session=SITZUNG,
    )


class TestWerkzeugliste:
    def test_es_sind_die_sieben_aus_paragraf_181(self, werkzeuge: Toolbox) -> None:
        namen = [spec.name for spec in werkzeuge.specs()]

        assert namen == [
            "graph_overview",
            "graph_traverse",
            "graph_search",
            "concept_get",
            "concept_upsert",
            "link_add",
            "cluster_project",
        ]

    def test_die_suche_nennt_sich_selbst_den_fallback(self, werkzeuge: Toolbox) -> None:
        """§18.2: "die einzige wirksame Stelle, an der sich das Verhalten steuern lässt"."""
        suche = next(spec for spec in werkzeuge.specs() if spec.name == "graph_search")

        assert "Fallback" in suche.description
        assert "graph_overview" in suche.description

    def test_die_uebersicht_nennt_sich_den_ersten_aufruf(self, werkzeuge: Toolbox) -> None:
        uebersicht = next(spec for spec in werkzeuge.specs() if spec.name == "graph_overview")

        assert "erste Aufruf" in uebersicht.description

    def test_das_schreibwerkzeug_nennt_seine_grenze(self, werkzeuge: Toolbox) -> None:
        """§17.4: "Ein Mensch darf die geteilte Struktur ordnen; ein Agent nicht"."""
        upsert = next(spec for spec in werkzeuge.specs() if spec.name == "concept_upsert")

        assert "persönlichen Store" in upsert.description
        assert "store" not in upsert.input_schema["properties"]

    def test_das_schreibwerkzeug_zaehlt_die_zulaessigen_typen_auf(
        self, werkzeuge: Toolbox, semantik_settings: Settings
    ) -> None:
        """Sonst rät der Agent — und die Taxonomie aus §7.2 prüft exakt.

        Ein Aufruf mit ``type: "note"`` scheiterte an "Unbekannter Typ 'note'", weil die
        Taxonomie ``Note`` heißt. Der Agent hatte keine Möglichkeit, darauf zu kommen: Das Schema
        nannte nur "string". Die Aufzählung stammt aus der Konfiguration und nicht aus einer
        Liste im Code — wer die Taxonomie erweitert, muss diese Datei nicht anfassen.

        ``Cluster`` bleibt draußen, obwohl die Taxonomie ihn für ``personal`` zulässt: Eine
        Themengruppe entsteht aus dem Clustering-Lauf. Von Hand angelegt hätte sie keine
        Mitglieder, und der nächste Lauf wüsste nichts von ihr.
        """
        upsert = next(spec for spec in werkzeuge.specs() if spec.name == "concept_upsert")
        erlaubt = upsert.input_schema["properties"]["type"]["enum"]
        taxonomie = [
            eintrag.name
            for eintrag in semantik_settings.concept_types
            if defaults.STORE_PERSONAL in eintrag.stores
        ]

        assert erlaubt == [name for name in taxonomie if name != defaults.CONCEPT_TYPE_CLUSTER]
        assert "Note" in erlaubt
        assert defaults.CONCEPT_TYPE_CLUSTER not in erlaubt

    def test_jedes_werkzeug_hat_ein_geschlossenes_schema(self, werkzeuge: Toolbox) -> None:
        """Ein durchgereichtes Feld sähe für den Agenten aus wie eine angenommene Angabe."""
        for spec in werkzeuge.specs():
            assert spec.input_schema["additionalProperties"] is False


class TestLesen:
    def test_die_uebersicht_liefert_cluster_und_den_naechsten_schritt(
        self, werkzeuge: Toolbox
    ) -> None:
        ergebnis = werkzeuge.graph_overview({})

        assert ergebnis["clusters"]
        assert "graph_traverse" in ergebnis["next_step"]

    def test_traversierung_folgt_nur_den_angegebenen_kantenarten(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        cluster = umgebung.cluster_ids()[0]

        ergebnis = werkzeuge.graph_traverse(
            {"concept_id": cluster, "kinds": [defaults.EDGE_KIND_MEMBER]}
        )

        assert {kante["kind"] for kante in ergebnis["edges"]} == {defaults.EDGE_KIND_MEMBER}

    def test_ein_unbekannter_startknoten_ist_ein_werkzeugfehler(self, werkzeuge: Toolbox) -> None:
        with pytest.raises(ToolError):
            werkzeuge.graph_traverse({"concept_id": "confluence:0"})

    def test_die_suche_nennt_ihren_modus(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.graph_search({"query": "Faktentabellen"})

        assert ergebnis["mode"] in {"lexical", "cluster", "hybrid"}

    def test_die_suche_folgt_einem_scope_in_seinen_store(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.graph_search({"query": "Notiz", "scope": "personal"})

        assert ergebnis["store"] == "personal"

    def test_concept_get_liefert_den_volltext(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.concept_get({"concept_id": "confluence:100"})

        assert ergebnis["id"] == "confluence:100"
        assert "body" in ergebnis

    def test_ein_unbekanntes_konzept_ist_ein_werkzeugfehler(self, werkzeuge: Toolbox) -> None:
        with pytest.raises(ToolError, match="gibt es"):
            werkzeuge.concept_get({"concept_id": "confluence:0"})

    def test_ein_unbekannter_store_ist_ein_werkzeugfehler(self, werkzeuge: Toolbox) -> None:
        with pytest.raises(ToolError, match="Unbekannter Store"):
            werkzeuge.graph_overview({"store": "gibtsnicht"})


class TestSchreiben:
    def test_eine_notiz_traegt_den_akteur_der_sitzung(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        """§18.3: "Jeder Schreibvorgang landet mit ``actor = 'agent:<session>'`` im change_log"."""
        ergebnis = werkzeuge.concept_upsert({"type": "Note", "title": "Vom Agenten"})

        assert ergebnis["actor"] == f"agent:{SITZUNG}"
        eintraege = umgebung.state("personal").changes
        assert eintraege[-1].actor == f"agent:{SITZUNG}"

    def test_eine_notiz_landet_im_persoenlichen_store(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        ergebnis = werkzeuge.concept_upsert({"type": "Note", "title": "Vom Agenten"})

        assert ergebnis["concept"]["store"] == "personal"
        assert ergebnis["concept"]["id"] in umgebung.state("personal").concepts

    def test_ein_bestehendes_konzept_wird_fortgeschrieben(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        werkzeuge.concept_upsert({"concept_id": "note:eigen", "type": "Note", "title": "Neu"})

        assert umgebung.state("personal").concepts["note:eigen"].title == "Neu"

    def test_ein_geteilter_typ_wird_abgelehnt(self, werkzeuge: Toolbox) -> None:
        with pytest.raises(ToolError, match="nicht zugelassen"):
            werkzeuge.concept_upsert({"type": "Confluence Page", "title": "Von Hand"})

    def test_ein_unbekanntes_ziel_beim_fortschreiben_ist_ein_werkzeugfehler(
        self, werkzeuge: Toolbox
    ) -> None:
        with pytest.raises(ToolError):
            werkzeuge.concept_upsert(
                {"concept_id": "note:gibtsnicht", "type": "Note", "title": "X"}
            )

    def test_cluster_project_laeuft_ueber_den_scope_des_projekts(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.cluster_project({"project_id": "note:eigen"})

        assert ergebnis["scope"] == "personal"
        assert "report" in ergebnis

    def test_ein_unbekanntes_projekt_ist_ein_werkzeugfehler(self, werkzeuge: Toolbox) -> None:
        with pytest.raises(ToolError, match="gibt es"):
            werkzeuge.cluster_project({"project_id": "note:gibtsnicht"})


class TestDerAgentFindetSeineBrueckeWieder:
    def test_notiz_anlegen_verlinken_und_ueber_traverse_wiederfinden(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        """§24, Stufe 12: das zentrale Abnahmekriterium."""
        cluster = umgebung.cluster_ids()[0]
        notiz = werkzeuge.concept_upsert(
            {"type": "Note", "title": "Was ich zum Warehouse gelernt habe"}
        )["concept"]["id"]

        werkzeuge.link_add({"from_id": notiz, "to_id": cluster, "to_store": "shared"})

        gefunden = werkzeuge.graph_traverse({"concept_id": notiz, "store": "personal"})
        assert cluster in {knoten["id"] for knoten in gefunden["nodes"]}

    def test_die_kante_liegt_im_persoenlichen_store(
        self, werkzeuge: Toolbox, umgebung: Any
    ) -> None:
        """§12.1: Der geteilte Store weiß nicht, dass es persönliche Konzepte gibt."""
        cluster = umgebung.cluster_ids()[0]
        werkzeuge.link_add({"from_id": "note:eigen", "to_id": cluster, "to_store": "shared"})

        assert len(umgebung.state("personal").edges) == 1
        assert all(kante.from_id != "note:eigen" for kante in umgebung.state("shared").edges)

    def test_eine_kante_von_einem_unbekannten_ausgangspunkt_ist_ein_werkzeugfehler(
        self, werkzeuge: Toolbox
    ) -> None:
        with pytest.raises(ToolError):
            werkzeuge.link_add({"from_id": "note:gibtsnicht", "to_id": "cluster:x"})


class TestAntwortdeckel:
    def test_eine_zu_grosse_antwort_wird_gekuerzt_und_markiert(
        self, minimal_config_dict: dict[str, Any], umgebung: Any
    ) -> None:
        """§18.3: "überschreitende Ergebnisse werden gekürzt und mit truncated: true markiert"."""
        eng = Settings.model_validate(
            {
                **minimal_config_dict,
                "embedding_dim": DIM,
                "clustering": {"neighbors_k": 4},
                "mcp": {"max_response_tokens": 100},
            }
        )
        kiste = Toolbox(
            eng,
            graph=umgebung.graph,
            catalog=CatalogService(eng, umgebung.uow, router=umgebung.router),
            curation=CurationService(eng, umgebung.uow),
            clusters=umgebung.clusters,
        )

        ergebnis = kiste.graph_overview({})

        assert ergebnis[TRUNCATED_KEY] is True
        assert len(ergebnis["clusters"]) < len(umgebung.cluster_ids())

    def test_eine_kleine_antwort_bleibt_unmarkiert(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.concept_get({"concept_id": "confluence:100"})

        assert TRUNCATED_KEY not in ergebnis

    def test_die_kennfelder_ueberleben_die_kuerzung(
        self, minimal_config_dict: dict[str, Any], umgebung: Any
    ) -> None:
        """Gekürzt wird der lange Text, nicht die Kennung.

        Die frühere Fassung zog von *jeder* Zeichenkette den gesamten Überschuss ab. Bei einem
        Konzept mit langem ``body`` traf das zuerst ``id``, ``store``, ``scope``, ``type``,
        ``title`` und ``description`` — alle kurz, alle sofort leer — während der lange Text
        stehen blieb. Der Agent bekam einen Fließtext, den er nicht zuordnen und über den er
        nicht weitergehen konnte: Ohne ``id`` gibt es kein ``graph_traverse``.
        """
        eng = Settings.model_validate(
            {
                **minimal_config_dict,
                "embedding_dim": DIM,
                "clustering": {"neighbors_k": 4},
                "mcp": {"max_response_tokens": 100},
            }
        )
        kiste = Toolbox(
            eng,
            graph=umgebung.graph,
            catalog=CatalogService(eng, umgebung.uow, router=umgebung.router),
            curation=CurationService(eng, umgebung.uow),
            clusters=umgebung.clusters,
        )

        ergebnis = kiste.concept_get({"concept_id": "confluence:100"})

        assert ergebnis[TRUNCATED_KEY] is True
        assert ergebnis["id"] == "confluence:100"
        assert ergebnis["store"] == defaults.STORE_SHARED
        assert ergebnis["scope"] and ergebnis["type"]


class TestOhnePersoenlichenScope:
    def test_ein_agent_ohne_persoenlichen_scope_bekommt_eine_auskunft(
        self, minimal_config_dict: dict[str, Any], umgebung: Any
    ) -> None:
        """Kein Absturz, sondern ein Satz: In dieser Installation ist er nicht vorgesehen."""
        ohne = Settings.model_validate(
            {
                **minimal_config_dict,
                "embedding_dim": DIM,
                "scopes": [{"name": "engineering", "store": "shared"}],
            }
        )
        kiste = Toolbox(
            ohne,
            graph=umgebung.graph,
            catalog=CatalogService(ohne, umgebung.uow),
            curation=CurationService(ohne, umgebung.uow),
            clusters=umgebung.clusters,
        )

        with pytest.raises(ToolError, match="kein Scope"):
            kiste.concept_upsert({"type": "Note", "title": "X"})


class TestSitzung:
    def test_ohne_angabe_traegt_der_akteur_eine_erkennbare_ersatzkennung(
        self, semantik_settings: Settings, umgebung: Any
    ) -> None:
        kiste = Toolbox(
            semantik_settings,
            graph=umgebung.graph,
            catalog=CatalogService(semantik_settings, umgebung.uow),
            curation=CurationService(semantik_settings, umgebung.uow),
            clusters=umgebung.clusters,
        )

        assert kiste.actor == f"agent:{defaults.MCP_DEFAULT_SESSION}"


class TestLeereClustersuche:
    """Eine leere Trefferliste ohne Erklärung ist die schlechteste Antwort (§12.4)."""

    def test_sie_sagt_dem_agenten_wie_es_weitergeht(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.graph_search(
            {"query": "etwas, das mit nichts hier zu tun hat", "granularity": "cluster"}
        )

        if not ergebnis["hits"]:
            assert "document" in ergebnis["next_step"]

    def test_eine_treffende_suche_traegt_keinen_hinweis(self, werkzeuge: Toolbox) -> None:
        ergebnis = werkzeuge.graph_search({"query": "Datenbank", "granularity": "document"})

        assert "next_step" not in ergebnis
