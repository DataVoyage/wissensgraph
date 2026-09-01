# Der Wissensgraph für Agenten

Dieses Dokument beschreibt, wie ein Agent mit dem Wissensgraphen arbeitet. Es ist dafür gedacht,
einem Agenten mitgegeben zu werden — als Systemprompt, als Kontextdatei oder als Anhang zur
Werkzeugbeschreibung.

**Wenn du nur einen Satz mitnimmst:** Fang mit `graph_schema` und `graph_overview` an, bewege dich
über `graph_traverse` weiter, und benutze `graph_search` erst, wenn beides nicht weiterhilft.

---

## 1. Was dieser Graph ist

Ein Wissensgraph über zwei getrennte Bestände:

| Store | Inhalt | Für dich |
|---|---|---|
| `shared` | Gespiegelte Inhalte aus Confluence, Jira und weiteren Quellen, dazu automatisch gebildete Themengruppen und erkannte Beziehungen. | **nur lesbar** |
| `personal` | Notizen und Projekte — deine und die des Menschen, für den du arbeitest. Verlässt den Rechner nicht. | lesbar und **schreibbar** |

Beides sind eigene Datenbanken. Kanten dürfen von `personal` nach `shared` zeigen, nie umgekehrt:
Der geteilte Bestand weiß nichts von persönlichen Notizen. Beim Traversieren wird diese Brücke
trotzdem mitverfolgt — du bekommst also von einer Confluence-Seite aus die eigenen Notizen zu
sehen, die auf sie zeigen.

Der Graph ist **kein Dateisystem**. Es gibt keine Ordner, keine Pfade und keine Hierarchie, an der
man sich entlanghangeln könnte. Was es gibt, sind Konzepte mit einem Typ, einem Scope und Kanten
zueinander.

---

## 2. Verbindung

| Transport | Adresse | Wann |
|---|---|---|
| Streamable HTTP | `http://<host>:8800/mcp` | Vorgabe. Ein Endpunkt, alle Methoden. |
| stdio | `wg mcp --transport stdio` | Wenn der Agent den Server als Unterprozess startet. |

Der HTTP-Server läuft **ohne Authentifizierung**. Er gehört deshalb hinter eine Netzgrenze und
nicht ins offene Netz.

```json
{
  "mcpServers": {
    "wissensgraph": {
      "type": "http",
      "url": "http://localhost:8800/mcp"
    }
  }
}
```

---

## 3. Die Reihenfolge, die zählt

```
graph_schema      einmal je Sitzung — welche Werte darf ich einsetzen?
      ↓
graph_overview    worum geht es in diesem Graphen überhaupt?
      ↓
graph_traverse    ← hier verbringst du die meiste Zeit
      ↓
graph_search      nur, wenn die beiden darüber keinen Startpunkt liefern
```

**Warum nicht einfach suchen?** Eine Suche setzt voraus, dass du weißt, wonach du suchst. Bei einer
fremden Sammlung weißt du das nicht: Du kennst weder die Begriffe, die dort benutzt werden, noch
die Abkürzungen, noch die Systemnamen. `graph_overview` kostet einen Aufruf und beantwortet die
Frage, worum es geht — danach suchst du mit den richtigen Wörtern oder brauchst gar nicht mehr zu
suchen.

**Warum traversieren statt suchen?** Eine Kante ist eine Aussage, die jemand oder etwas getroffen
hat: „diese Seite verweist auf jene", „dieses Dokument gehört zu diesem Thema". Ein Suchtreffer ist
eine Ähnlichkeit. Die Kante ist die belastbarere Auskunft.

---

## 4. Die Werkzeuge

### `graph_schema` — die Regeln dieser Installation

Ohne Argumente. Statisch: Innerhalb einer Sitzung ändert sich die Antwort nicht, ein Aufruf genügt.

Sie beantwortet die Fragen, die du sonst raten müsstest:

```json
{
  "stores":  [{"name": "shared", "you_can_write": false, "note": "…"}, …],
  "scopes":  [{"name": "engineering", "store": "shared", "description": "…"}, …],
  "concept_types": [
    {"name": "Confluence Page", "stores": ["shared"], "source_mirrored": true, "you_can_create": false},
    {"name": "Note", "stores": ["personal"], "source_mirrored": false, "you_can_create": true}
  ],
  "edge_kinds": {"structural": ["member", "related"], "semantic": ["depends_on", "extends", …]},
  "limits": {"traverse_max_hops": 3, "traverse_max_nodes": 200, "max_response_tokens": 4000, …},
  "you": {"actor": "agent:…", "writable_stores": ["personal"], "creatable_types": ["Project", "Note"],
          "default_scope": "personal"},
  "next_step": "…"
}
```

Die Taxonomie ist **je Installation verschieden** und wird **exakt** geprüft, Groß- und
Kleinschreibung eingeschlossen. `note` ist nicht `Note`. Rate nicht — frag.

### `graph_overview` — der Einstieg

`(scope?, store?, limit?, cursor?)` → die Themengruppen mit Titel, Beschreibung und Mitgliederzahl.

```json
{"store": "shared",
 "clusters": [{"id": "cluster:0810…", "title": "Zugangskontrolle …", "description": "…", "member_count": 3}],
 "next_step": "…",
 "next_cursor": "cluster:0def…"}
```

Steht ein `next_cursor` in der Antwort, ist die Liste **nicht vollständig**. Ruf dasselbe Werkzeug
mit `cursor: "<next_cursor>"` erneut auf, wenn du alle Themen brauchst.

### `graph_traverse` — der Hauptaufruf

`(concept_id, hops?, kinds?, store?)` → Knoten, Kanten und eine Bewertung.

```json
{"store": "shared", "hops": 1,
 "nodes": [{"id": "…", "store": "…", "scope": "…", "type": "Cluster", "title": "…",
            "status": "stable", "hops": 0, "density": 4, "score": 0.999}],
 "edges": [{"from_id": "…", "to_id": "…", "to_store": "shared", "kind": "depends_on",
            "confidence": null, "verified": false}],
 "score_kind": "ranking", "score_hint": "…"}
```

* `hops` je Knoten ist die Entfernung zum Startpunkt, `hops` in der Antwort die gelaufene Tiefe.
* `kinds` schränkt beim **Ausbreiten** ein, nicht erst am Ergebnis: Wer nur `member` verfolgt,
  erreicht auch nur, was über `member` erreichbar ist.
* Die Tiefe ist gedeckelt (`traverse_max_hops`). Größere Werte werden **stillschweigend gekappt** —
  wenn du eine bestimmte Tiefe brauchst, sieh in `graph_schema` nach, ob sie zulässig ist.

Die zwei Bewegungsrichtungen:

| Kantenart | Richtung | Frage dahinter |
|---|---|---|
| `member` | abwärts, in eine Themengruppe hinein | „Was gehört zu diesem Thema?" |
| `related` und die semantischen Arten | seitwärts | „Was hat noch damit zu tun?" |

### `graph_search` — der Fallback

`(query, scope?, granularity?, store?)` → Treffer, plus die Angabe, **wie** gesucht wurde.

```json
{"store": "shared", "query": "Token", "mode": "cluster",
 "score_kind": "similarity", "hits": [...], "score_hint": "…"}
```

`mode` ist wichtig: `cluster` heißt, es wurde über Themengruppen gesucht; `hybrid` kombiniert
Vektor- und Volltextsuche; `lexical` heißt, es stand **kein Embedding-Modell zur Verfügung** und
es wurde nur nach Wörtern gesucht. Im letzten Fall sind schlechte Treffer kein Zeichen dafür, dass
es nichts gibt — nur dafür, dass anders gesucht wurde.

Bleibt eine Cluster-Suche leer, ruf sie mit `granularity: "document"` erneut auf.

### `concept_get` — der Volltext

`(concept_id, store?)` → das vollständige Konzept mit `body`, `outgoing`, `incoming`, `clusters`,
`locked_fields` und der Provenienz.

Lange Texte werden gekürzt. Steht `truncated: true` in der Antwort, hast du **nicht alles**
gesehen.

### `concept_upsert` — schreiben

`(type, title, description?, body?, tags?, scope?, concept_id?)` → das angelegte Konzept.

Es gibt **kein** `store`-Argument, und das ist die Aussage: Du schreibst ausschließlich nach
`personal`. Ohne `concept_id` entsteht ein neues Konzept, mit ihr wird ein bestehendes
fortgeschrieben.

Zulässige Werte für `type` stehen im Eingabeschema und in `graph_schema` unter
`you.creatable_types`. `Cluster` ist nicht darunter: Eine Themengruppe entsteht aus einem
Clustering-Lauf. Von Hand angelegt hätte sie keine Mitglieder, und der nächste Lauf wüsste nichts
von ihr — dafür gibt es `cluster_project`.

### `link_add` — verknüpfen

`(from_id, to_id, to_store?, kind?)` → die angelegte Kante.

Die Kante geht **immer** von deinem persönlichen Konzept aus. Bevorzugtes Ziel ist eine
Themengruppe im geteilten Store, nicht ein einzelnes Dokument: Eine Notiz, die an einem Thema
hängt, bleibt auffindbar, auch wenn das einzelne Dokument später verschwindet.

### `cluster_project` — Themen neu bilden

`(project_id)` → Bericht über den Lauf. Nützlich, nachdem du mehrere Notizen zu einem Projekt
angelegt hast. Verändert nichts im geteilten Store.

---

## 5. Felder, die du verstehen musst

### `score` bedeutet je nach Herkunft etwas anderes

Deshalb steht neben jedem Score ein `score_kind`. **Vergleiche niemals Scores verschiedener
Herkunft** — 0,016 aus einer Hybrid-Suche ist kein schlechterer Treffer als 0,73 aus einer
Cluster-Suche.

| `score_kind` | Skala | Typische Werte |
|---|---|---|
| `ranking` | Nähe, Dichte und Aktualität nach der Formel der Traversierung, auf den größten Wert normiert | 0 … 1 |
| `similarity` | Kosinusähnlichkeit zum Zentroid einer Themengruppe | 0,5 … 0,9 |
| `rrf` | Reciprocal Rank Fusion aus Vektor- und Volltextsuche | um 0,016 |
| `lexical` | Rang der Volltextsuche | — |

### `truncated` und `next_cursor`

* `truncated: true` — die Antwort wurde gekürzt, weil sie zu lang war. Du hast nicht alles.
* `next_cursor` — es gibt weitere Seiten. Ohne einen zweiten Aufruf hast du nicht alles.

Beides bedeutet: **Schließe nicht aus dem Fehlen auf das Nichtvorhandensein.**

### `next_step`

Mehrere Antworten enthalten ein `next_step` mit einem konkreten Vorschlag, oft samt dem Aufruf, den
du als Nächstes machen solltest. Er ist keine Höflichkeit, sondern der billigste Weg zum Ziel.

### Provenienz — wer das behauptet

Jedes Konzept und jede Kante trägt, woher sie stammt:

| `generated_by` | Bedeutung | Wie belastbar |
|---|---|---|
| `null` | Ein Mensch hat es gesetzt. | am belastbarsten |
| `code:…` | Aus dem Inhalt abgeleitet, etwa ein Link in einer Seite oder ein Clustering-Ergebnis. | belastbar |
| `gemini:…`, `openai:…` | **Vorschlag eines Modells.** | ungeprüft |

Ist zusätzlich `verified: false` (bzw. `verified_at: null` und `curated: false`), hat **niemand**
diese Kante bestätigt. Behandle sie als Hinweis, nicht als Tatsache — und schreibe das dazu, wenn
du dich darauf beziehst.

---

## 6. Was du darfst und was nicht

| Ziel | Erlaubt |
|---|---|
| `personal`: alles anlegen, ändern, verknüpfen | **ja** |
| `shared`: lesen | **ja** |
| `shared`: irgendetwas schreiben | **nein** |

Die Beschränkung ist auf **Datenbankebene** erzwungen, nicht nur in der Werkzeugbeschreibung. Ein
Schreibversuch auf `shared` scheitert, auch wenn du einen Weg daran vorbei findest. Versuch es
nicht — es ist keine Lücke, sondern eine Entscheidung: Ein Mensch darf die geteilte Struktur
ordnen, ein Agent nicht.

Jeder deiner Schreibvorgänge steht mit deiner Sitzungskennung im Änderungsjournal und lässt sich
zurücknehmen. Das ist der Grund, warum du schreiben darfst: Nichts, was du tust, ist endgültig.

---

## 7. Wenn etwas schiefgeht

| Meldung | Was sie heißt | Was du tust |
|---|---|---|
| `'x' ist kein zulässiger Wert für 'type'. Möglich sind: …` | Ein Wert außerhalb der Taxonomie. | Nimm einen der genannten Werte. Achte auf Groß- und Kleinschreibung. |
| `Keiner der Startknoten […] liegt im Store '…'` | Die ID gibt es nicht — oder nicht in diesem Store. | Prüf den Store. Eine ID ist erst mit ihrem Store eindeutig. |
| `Konzept '…' gibt es im Store 'personal' nicht.` | Bei `link_add`: `from_id` muss dein eigenes Konzept sein. | Erst `concept_upsert`, dann verknüpfen. |
| `Unbekannter Store '…'` | Es gibt genau die aus `graph_schema`. | — |

Jede Ablehnung nennt die zulässigen Werte. Wenn du dir unsicher bist, ruf `graph_schema` auf,
statt einen zweiten Wert zu probieren.

---

## 8. Drei Abläufe

### Eine Frage beantworten

```
graph_schema                                  (einmal, gemerkt)
graph_overview                                → welche Themen gibt es?
graph_traverse(cluster-id, kinds: ["member"]) → welche Dokumente gehören dazu?
concept_get(dokument-id)                      → der Volltext
```

Erst wenn keine Themengruppe passt: `graph_search`.

### Etwas festhalten

```
concept_upsert(type: "Note", title: "…", body: "…")
link_add(from_id: <neue Notiz>, to_id: <passende Themengruppe>, kind: "references")
```

Die Verknüpfung ist der eigentliche Wert. Eine Notiz ohne Kante ist ein loser Knoten, den
niemand wiederfindet — auch du nicht.

### Ein Projekt aufbauen

```
concept_upsert(type: "Project", title: "…")
concept_upsert(type: "Note", …)               mehrfach
link_add(…)                                   je Notiz auf Projekt oder Thema
cluster_project(project_id)                   → ordnet den persönlichen Bestand neu
```

---

## 9. Was du nicht tun solltest

* **Mit `graph_search` beginnen.** Du suchst dann mit deinen Wörtern statt mit denen der Sammlung.
* **Werte raten.** Typen, Scopes und Kantenarten stehen im Schema und in `graph_schema`.
* **`hops` hochdrehen, um „alles" zu sehen.** Die Tiefe ist gedeckelt, und ein breiter Ausschnitt
  ist kein besserer. Zwei gezielte Traversierungen schlagen eine große.
* **Ein `truncated: true` übergehen.** Du hast dann nicht alles gesehen und weißt es.
* **Einen unbestätigten Modellvorschlag als Tatsache wiedergeben.** Sag dazu, dass er unbestätigt
  ist.
* **Alles in eine Notiz schreiben.** Mehrere kleine, jede an ihrem Thema verknüpft, sind
  wiederauffindbar; eine große ist es nicht.
* **Versuchen, nach `shared` zu schreiben.** Es geht nicht, und es soll nicht gehen.

---

## 10. Grenzen

Sie stehen in `graph_schema` unter `limits` — und zwar mit den Werten **dieser** Installation, nicht
mit den unten genannten Beispielen:

| Grenze | Bedeutung |
|---|---|
| `traverse_max_hops` | Tiefere Anfragen werden stillschweigend gekappt. |
| `traverse_max_nodes` | Größere Nachbarschaften werden gedeckelt; die Antwort sagt es über `truncated`. |
| `max_response_tokens` | Ab hier wird gekürzt. Fließtexte trifft es zuerst, Kennfelder wie `id`, `store`, `scope` und `type` nie. |
| `overview_default_limit` | Themengruppen je Seite; der Rest kommt über `next_cursor`. |

Ein Modellaufruf im Hintergrund (etwa für eine semantische Suche) kann durch ein Budget begrenzt
sein. Ist es erschöpft, degradiert die Suche auf Volltext — erkennbar an `mode: "lexical"`.
