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
import type { ChangeEntry, ConceptDetail, Edge } from "../api/types";

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
    <aside className="wg-panel flex h-full flex-col gap-3 overflow-y-auto" aria-label="Details">
      <header>
        <h2 className="text-base font-semibold">{detail.title ?? detail.id}</h2>
        <p className="text-xs text-slate-500">
          {detail.id} · {detail.type} · {detail.scope}
        </p>
        <StoreMarke store={detail.store} />
      </header>

      <Feld label="Beschreibung" gesperrt={gesperrt.has("description")}>
        {detail.description ?? "—"}
      </Feld>

      {detail.body !== null && (
        <Feld label="Fließtext" gesperrt={gesperrt.has("body")}>
          <pre className="whitespace-pre-wrap text-xs">{detail.body}</pre>
        </Feld>
      )}

      <section>
        <h3 className="text-sm font-medium">Status</h3>
        <div className="flex gap-2">
          <input
            className="wg-input"
            aria-label="Status"
            value={status}
            onChange={(ereignis) => setzeStatus(ereignis.target.value)}
          />
          <button
            type="button"
            className="wg-button"
            disabled={status === detail.status || patchen.isPending}
            onClick={() =>
              patchen.mutate({ id: detail.id, store: detail.store, patch: { status } })
            }
          >
            Setzen
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Status und Tags gehören dem Menschen — auch an gespiegelten Inhalten (§17.4).
        </p>
      </section>

      <Provenienz detail={detail} />
      <Kantenliste titel="Ausgehend" kanten={detail.outgoing} onOpen={onOpen} feld="to_id" />
      <Kantenliste titel="Eingehend" kanten={detail.incoming} onOpen={onOpen} feld="from_id" />

      {detail.clusters.length > 0 && (
        <section>
          <h3 className="text-sm font-medium">Cluster</h3>
          <ul className="text-sm">
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
      className={`mt-1 inline-block rounded px-2 py-0.5 text-xs text-white ${
        persoenlich ? "bg-personal" : "bg-shared"
      }`}
    >
      {persoenlich ? "persönlich — verlässt diesen Rechner nicht" : "geteilt"}
    </span>
  );
}

function Feld({
  label,
  gesperrt,
  children,
}: {
  label: string;
  gesperrt: boolean;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section>
      <h3 className="flex items-center gap-2 text-sm font-medium">
        {label}
        {gesperrt && (
          <span
            data-testid={`gesperrt-${label}`}
            title="Dieses Feld stammt aus der Quelle und ist gesperrt (§17.4)."
            className="rounded bg-slate-200 px-1 text-xs text-slate-600"
          >
            🔒 aus der Quelle
          </span>
        )}
      </h3>
      <div className={`text-sm ${gesperrt ? "wg-locked rounded border px-2 py-1" : ""}`}>
        {children}
      </div>
    </section>
  );
}

function Provenienz({ detail }: { detail: ConceptDetail }): JSX.Element {
  return (
    <section>
      <h3 className="text-sm font-medium">Provenienz</h3>
      <dl className="grid grid-cols-2 gap-x-2 text-xs">
        <dt>Quelle</dt>
        <dd>{detail.source_name ?? "lokal angelegt"}</dd>
        <dt>Erzeugt von</dt>
        <dd>{detail.generated_by ?? "—"}</dd>
        <dt>Bestätigt von</dt>
        <dd>{detail.verified_by ?? "—"}</dd>
        <dt>Kuratiert</dt>
        <dd>{detail.curated ? "ja" : "nein"}</dd>
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
      <h3 className="text-sm font-medium">{titel}</h3>
      <ul className="space-y-1 text-sm">
        {kanten.map((kante) => {
          const ziel = kante[feld];
          const zielStore = feld === "to_id" ? kante.to_store : kante.from_store;
          const unbestaetigt =
            kante.generated_by !== null && !kante.curated && kante.verified_at === null;
          return (
            <li key={kante.id} className="flex items-center gap-2">
              <span className="rounded bg-slate-100 px-1 text-xs">{kante.kind}</span>
              <button
                type="button"
                className="flex-1 truncate text-left underline"
                onClick={() => onOpen(ziel, zielStore)}
              >
                {ziel}
              </button>
              {unbestaetigt && (
                <>
                  <span className="text-xs text-modell" title="Vorschlag eines Modells">
                    unbestätigt
                  </span>
                  <button
                    type="button"
                    className="wg-button"
                    onClick={() =>
                      aktion.mutate({ id: kante.id, action: "verify", store: kante.from_store })
                    }
                  >
                    ✓
                  </button>
                  <button
                    type="button"
                    className="wg-button"
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
      <h3 className="text-sm font-medium">Änderungsjournal</h3>
      {eintraege.length === 0 ? (
        <p className="text-xs text-slate-500">Noch keine Änderung.</p>
      ) : (
        <ul className="space-y-1 text-xs">
          {eintraege.slice(0, 12).map((eintrag) => (
            <li key={eintrag.id ?? `${eintrag.changed_at}`} className="flex items-center gap-2">
              <span className="rounded bg-slate-100 px-1">{eintrag.change_type}</span>
              <span className="flex-1 truncate">{eintrag.actor}</span>
              {eintrag.undoable && eintrag.id !== null && (
                <button
                  type="button"
                  className="wg-button"
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
        <p role="alert" className="mt-1 text-xs text-red-700">
          {rueckgaengig.error.message}
        </p>
      )}
    </section>
  );
}
