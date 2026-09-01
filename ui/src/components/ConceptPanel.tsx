/**
 * Das Seitenpanel eines ausgewählten Knotens (§17.2 Ansicht 1, §17.3).
 *
 * Die wichtigste Zeile dieser Datei ist die Sperre: §17.3 verlangt "Inhaltsfelder
 * quellgespiegelter Konzepte sind sichtbar gesperrt, nicht nur schreibgeschützt". Welche Felder
 * das sind, entscheidet **nicht** diese Oberfläche — sie liest `locked_fields` aus der Antwort
 * (§17.1). Eine hier nachgebaute Regel wäre eine zweite Wahrheit, die beim ersten
 * Konfigurationswechsel falsch würde.
 */

import { useState } from "react";

import { useEdgeAction, useHistory, usePatchConcept, useUndo } from "../api/hooks";
import { farbeFuerKante, istModellvorschlag, istUnbestaetigt } from "../theme";
import type { ChangeEntry, ConceptDetail, Edge } from "../api/types";
import { Markdown } from "./Markdown";
import { Schaltflaeche } from "./basis";

export interface ConceptPanelProps {
  detail: ConceptDetail;
  onOpen: (id: string, store: string) => void;
}

export function ConceptPanel({ detail, onOpen }: ConceptPanelProps): JSX.Element {
  const historie = useHistory(detail.id, detail.store);
  const patchen = usePatchConcept();
  const [status, setzeStatus] = useState(detail.status);
  const gesperrt = new Set(detail.locked_fields);

  return (
    // Kein eigener Rahmen mehr: Das Panel wohnt im Inspektor, und ein Rahmen im Rahmen sähe
    // aus wie ein Fenster im Fenster.
    <aside className="flex h-full flex-col gap-4 overflow-y-auto p-3" aria-label="Details">
      <header className="-m-3 mb-0 border-b border-ton-200 bg-ton-50 p-3">
        <h2 className="text-base font-semibold leading-tight text-ton-900">
          {detail.title ?? detail.id}
        </h2>
        <p className="mt-1 flex flex-wrap items-center gap-1">
          <span className="wg-chip">{detail.id}</span>
          <span className="wg-chip">{detail.type}</span>
          <span className="wg-chip">{detail.scope}</span>
        </p>
        <StoreMarke store={detail.store} />
        {detail.resource !== null && /^https?:\/\//i.test(detail.resource) && (
          <p className="mt-1.5">
            <a
              href={detail.resource}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-signal-700 underline decoration-signal-200 hover:decoration-signal-500"
            >
              zur Quelle ↗
            </a>
          </p>
        )}
      </header>

      <Feld
        label="Beschreibung"
        gesperrt={gesperrt.has("description")}
        wert={detail.description}
        bearbeitbar={detail.store === "personal" && !gesperrt.has("description")}
        speichern={(description) =>
          patchen.mutate({ id: detail.id, store: detail.store, patch: { description } })
        }
      >
        {detail.description === null ? "—" : <Markdown text={detail.description} />}
      </Feld>

      {(detail.body !== null || detail.store === "personal") && (
        <Feld
          label="Fließtext"
          gesperrt={gesperrt.has("body")}
          wert={detail.body}
          bearbeitbar={detail.store === "personal" && !gesperrt.has("body")}
          speichern={(body) =>
            patchen.mutate({ id: detail.id, store: detail.store, patch: { body } })
          }
        >
          {/* Gerendert, nicht roh (§17.2 Ansicht 2) — der Renderer baut React-Knoten und kein
              HTML, ein <script> aus einer gespiegelten Seite bleibt Text. */}
          {detail.body === null ? "—" : <Markdown text={detail.body} />}
        </Feld>
      )}

      <section>
        <h3 className="wg-panel-titel">Status</h3>
        <div className="flex gap-2">
          <input
            className="wg-input"
            aria-label="Status"
            value={status}
            onChange={(ereignis) => setzeStatus(ereignis.target.value)}
          />
          <button
            type="button"
            className="wg-button wg-button-primaer shrink-0"
            disabled={status === detail.status || patchen.isPending}
            onClick={() =>
              patchen.mutate({ id: detail.id, store: detail.store, patch: { status } })
            }
          >
            Setzen
          </button>
        </div>
        <p className="wg-hinweis mt-1.5">
          Status und Tags gehören dem Menschen — auch an gespiegelten Inhalten (§17.4).
        </p>
      </section>

      <Provenienz detail={detail} />
      <Kantenliste titel="Ausgehend" kanten={detail.outgoing} onOpen={onOpen} feld="to_id" />
      <Kantenliste titel="Eingehend" kanten={detail.incoming} onOpen={onOpen} feld="from_id" />

      {detail.clusters.length > 0 && (
        <section>
          <h3 className="wg-panel-titel">Cluster</h3>
          <ul className="space-y-0.5 text-sm text-ton-700">
            {detail.clusters.map((cluster) => (
              <li key={cluster.id}>{cluster.title ?? cluster.id}</li>
            ))}
          </ul>
        </section>
      )}

      <Historie eintraege={historie.data?.items ?? []} store={detail.store} />
    </aside>
  );
}

function StoreMarke({ store }: { store: string }): JSX.Element {
  // §17.2 Ansicht 5: "Deutliche visuelle Trennung: der personal-Bereich ist durchgehend als
  // solcher gekennzeichnet."
  const persoenlich = store === "personal";
  return (
    <span
      className={`mt-2 inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-2xs font-medium
        text-ton-0 ${persoenlich ? "bg-personal" : "bg-shared"}`}
    >
      {persoenlich ? "persönlich — verlässt diesen Rechner nicht" : "geteilt"}
    </span>
  );
}

function Feld({
  label,
  gesperrt,
  bearbeitbar = false,
  wert = null,
  speichern,
  children,
}: {
  label: string;
  gesperrt: boolean;
  /** Nur im persönlichen Store: Notizen werden hier bearbeitet (§17.2 Ansicht 5). */
  bearbeitbar?: boolean;
  wert?: string | null;
  speichern?: (wert: string) => void;
  children: React.ReactNode;
}): JSX.Element {
  const [entwurf, setzeEntwurf] = useState<string | null>(null);

  return (
    <section>
      <h3 className="wg-panel-titel">
        {label}
        {gesperrt && (
          <span
            data-testid={`gesperrt-${label}`}
            title="Dieses Feld stammt aus der Quelle und ist gesperrt (§17.4)."
            className="wg-chip normal-case tracking-normal"
          >
            🔒 aus der Quelle
          </span>
        )}
        {bearbeitbar && entwurf === null && (
          <Schaltflaeche
            art="still"
            klein
            className="ml-auto"
            aria-label={`${label} bearbeiten`}
            onClick={() => setzeEntwurf(wert ?? "")}
          >
            bearbeiten
          </Schaltflaeche>
        )}
      </h3>
      {entwurf === null ? (
        <div
          className={`text-sm leading-relaxed text-ton-700 ${
            gesperrt ? "wg-locked rounded border px-2 py-1" : ""
          }`}
        >
          {children}
        </div>
      ) : (
        <div className="space-y-1.5">
          <textarea
            className="wg-input min-h-28 font-mono text-xs"
            aria-label={`${label} (Markdown)`}
            value={entwurf}
            onChange={(ereignis) => setzeEntwurf(ereignis.target.value)}
          />
          <div className="flex gap-1.5">
            <Schaltflaeche
              art="primaer"
              klein
              onClick={() => {
                speichern?.(entwurf);
                setzeEntwurf(null);
              }}
            >
              Speichern
            </Schaltflaeche>
            <Schaltflaeche art="still" klein onClick={() => setzeEntwurf(null)}>
              Verwerfen
            </Schaltflaeche>
          </div>
        </div>
      )}
    </section>
  );
}

function Provenienz({ detail }: { detail: ConceptDetail }): JSX.Element {
  return (
    <section>
      <h3 className="wg-panel-titel">Provenienz</h3>
      <dl className="grid grid-cols-[7rem_1fr] gap-x-2 gap-y-1 text-xs">
        <dt className="text-ton-500">Quelle</dt>
        <dd className="truncate text-ton-700">{detail.source_name ?? "lokal angelegt"}</dd>
        <dt className="text-ton-500">Erzeugt von</dt>
        <dd className="truncate text-ton-700">{detail.generated_by ?? "—"}</dd>
        <dt className="text-ton-500">Bestätigt von</dt>
        <dd className="truncate text-ton-700">{detail.verified_by ?? "—"}</dd>
        <dt className="text-ton-500">Kuratiert</dt>
        <dd className="text-ton-700">{detail.curated ? "ja" : "nein"}</dd>
      </dl>
    </section>
  );
}

function Kantenliste({
  titel,
  kanten,
  onOpen,
  feld,
}: {
  titel: string;
  kanten: Edge[];
  onOpen: (id: string, store: string) => void;
  feld: "to_id" | "from_id";
}): JSX.Element | null {
  const aktion = useEdgeAction();
  if (kanten.length === 0) {
    return null;
  }
  return (
    <section>
      <h3 className="wg-panel-titel">
        {titel}
        <span className="ml-auto font-normal tabular-nums">{kanten.length}</span>
      </h3>
      <ul className="space-y-0.5 text-sm">
        {kanten.map((kante) => {
          const ziel = kante[feld];
          const zielStore = feld === "to_id" ? kante.to_store : kante.from_store;
          const offen = istUnbestaetigt(kante);
          return (
            <li
              key={kante.id}
              className={`flex items-center gap-1.5 rounded px-1 py-1 ${
                istModellvorschlag(kante) ? "bg-signal-50" : ""
              }`}
            >
              {/* Die Herkunft steht als Strich davor — dieselbe Farbe wie im Graphen, damit die
                  Liste und das Bild dieselbe Sprache sprechen (§17.2). */}
              <span
                aria-hidden="true"
                title={offen ? "Modellvorschlag, unbestätigt" : "Herkunft dieser Kante"}
                className="h-4 w-0.5 shrink-0 rounded"
                style={{ backgroundColor: farbeFuerKante(kante.generated_by) }}
              />
              <span className="wg-chip shrink-0">{kante.kind}</span>
              <button
                type="button"
                className="flex-1 truncate text-left text-ton-700 hover:text-signal-600 hover:underline"
                onClick={() => onOpen(ziel, zielStore)}
              >
                {ziel}
              </button>
              {offen && (
                <>
                  <span className="sr-only">unbestätigt</span>
                  <button
                    type="button"
                    className="wg-button wg-button-klein"
                    title="Bestätigen"
                    onClick={() =>
                      aktion.mutate({ id: kante.id, action: "verify", store: kante.from_store })
                    }
                  >
                    ✓
                  </button>
                  <button
                    type="button"
                    className="wg-button wg-button-klein wg-button-gefahr"
                    title="Verwerfen"
                    onClick={() =>
                      aktion.mutate({ id: kante.id, action: "reject", store: kante.from_store })
                    }
                  >
                    ✕
                  </button>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Historie({
  eintraege,
  store,
}: {
  eintraege: ChangeEntry[];
  store: string;
}): JSX.Element {
  const rueckgaengig = useUndo();
  return (
    <section>
      <h3 className="wg-panel-titel">Änderungsjournal</h3>
      {eintraege.length === 0 ? (
        <p className="wg-hinweis">Noch keine Änderung.</p>
      ) : (
        <ul className="space-y-0.5 text-xs">
          {eintraege.slice(0, 12).map((eintrag) => (
            <li
              key={eintrag.id ?? `${eintrag.changed_at}`}
              className="flex items-center gap-1.5 rounded px-1 py-1 hover:bg-ton-50"
            >
              <span className="wg-chip shrink-0">{eintrag.change_type}</span>
              <span className="flex-1 truncate text-ton-600">{eintrag.actor}</span>
              {eintrag.undoable && eintrag.id !== null && (
                <button
                  type="button"
                  className="wg-button wg-button-klein wg-button-still"
                  onClick={() => rueckgaengig.mutate({ entry_id: eintrag.id as number, store })}
                >
                  rückgängig
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {rueckgaengig.isError && (
        <p role="alert" className="wg-fehler mt-1.5">
          {rueckgaengig.error.message}
        </p>
      )}
    </section>
  );
}
