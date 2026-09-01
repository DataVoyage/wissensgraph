/**
 * Ansicht 5 — Persönlicher Bereich (§17.2).
 *
 * Zwei Dinge macht diese Ansicht, und beide sind Leitprinzip 2 in Bedienung übersetzt:
 *
 * 1. **Notizen und Projekte anlegen.** Ausschließlich im `personal`-Store — die API lässt nichts
 *    anderes zu (§16.2), und die Ansicht bietet deshalb gar nichts anderes an.
 * 2. **Brücken setzen.** Aus einer Notiz heraus ein Cluster im geteilten Store suchen und
 *    verlinken. Die Kante liegt danach im *persönlichen* Store; der geteilte weiß nichts von ihr
 *    (§12.1).
 *
 * Der Hinweis, ob Embeddings lokal verfügbar sind, steht sichtbar dabei (§11.5, §17.2): Ohne
 * lokalen Anbieter bleibt der persönliche Bestand ohne Vektoren — kein Fehler, aber etwas, das
 * man wissen muss, bevor man sich über eine leere Ähnlichkeitsliste wundert.
 */

import { useState } from "react";

import { useAddEdge, useClusters, useConcepts, useCreateConcept, useModels } from "../api/hooks";
import type { EffectiveConfig } from "../api/types";
import type { UiState } from "../state";

export interface PersonalAreaProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  config: EffectiveConfig;
}

export function PersonalArea({ state, onChange, config }: PersonalAreaProps): JSX.Element {
  const notizen = useConcepts({ store: "personal", limit: 100 });
  const cluster = useClusters("shared");
  const anlegen = useCreateConcept();
  const verlinken = useAddEdge();
  const modelle = useModels();

  const [titel, setzeTitel] = useState("");
  const [typ, setzeTyp] = useState("Note");
  const [scope, setzeScope] = useState(
    config.scopes.find((eintrag) => eintrag.store === "personal")?.name ?? "personal",
  );

  const persoenlicheTypen = config.concept_types
    .filter((eintrag) => eintrag.stores.includes("personal"))
    .map((eintrag) => eintrag.name);
  const lokal = modelle.data?.tasks.find((route) => route.task === "embedding")?.local ?? false;

  return (
    <div className="grid h-full grid-cols-[1fr_1fr] gap-3">
      <div className="wg-panel flex flex-col gap-4 overflow-y-auto border-l-4 border-personal">
        <header className="-m-3 mb-0 border-b border-ton-200 bg-ton-100 p-3">
          <h2 className="flex items-center gap-2 text-base font-semibold text-ton-900">
            Persönlicher Bereich
            <span className="rounded bg-personal px-2 py-0.5 text-2xs font-medium text-ton-0">
              nur auf diesem Rechner
            </span>
          </h2>
          <p className="wg-hinweis mt-1">
            Alles hier bleibt auf diesem Rechner (Leitprinzip 2).{" "}
            {lokal
              ? "Embeddings laufen über einen lokalen Anbieter."
              : "Ohne lokalen Anbieter entstehen hier keine Embeddings (§11.5)."}
          </p>
        </header>

        <form
          className="space-y-2"
          onSubmit={(ereignis) => {
            ereignis.preventDefault();
            anlegen.mutate(
              { scope, type: typ, title: titel },
              {
                onSuccess: (ergebnis) => {
                  setzeTitel("");
                  if (ergebnis.concept) {
                    onChange({ id: ergebnis.concept.id, store: "personal" });
                  }
                },
              },
            );
          }}
        >
          <label className="block">
            <span className="wg-label">Titel</span>
            <input
              className="wg-input"
              aria-label="Titel"
              value={titel}
              onChange={(ereignis) => setzeTitel(ereignis.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <label className="flex-1">
              <span className="wg-label">Typ</span>
              <select
                className="wg-input"
                aria-label="Typ der Notiz"
                value={typ}
                onChange={(ereignis) => setzeTyp(ereignis.target.value)}
              >
                {persoenlicheTypen.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex-1">
              <span className="wg-label">Scope</span>
              <select
                className="wg-input"
                aria-label="Scope der Notiz"
                value={scope}
                onChange={(ereignis) => setzeScope(ereignis.target.value)}
              >
                {config.scopes
                  .filter((eintrag) => eintrag.store === "personal")
                  .map((eintrag) => (
                    <option key={eintrag.name} value={eintrag.name}>
                      {eintrag.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <button
            type="submit"
            className="wg-button wg-button-primaer"
            disabled={titel.trim() === ""}
          >
            Anlegen
          </button>
          {anlegen.isError && (
            <p role="alert" className="wg-fehler">
              {anlegen.error.message}
            </p>
          )}
        </form>

        <section>
          <h3 className="wg-panel-titel">Eigene Konzepte</h3>
          <ul className="-mx-1 space-y-0.5 text-sm">
            {(notizen.data?.items ?? []).map((eintrag) => (
              <li key={eintrag.id}>
                <button
                  type="button"
                  className={`wg-eintrag truncate ${
                    state.id === eintrag.id ? "wg-eintrag-aktiv" : ""
                  }`}
                  onClick={() => onChange({ id: eintrag.id, store: "personal" })}
                >
                  {eintrag.title ?? eintrag.id}
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="wg-panel space-y-3 overflow-y-auto">
        <h2 className="wg-panel-titel">Brücke schlagen</h2>
        <p className="wg-hinweis">
          Aus der ausgewählten Notiz auf ein Cluster im geteilten Store. Die Kante liegt danach im
          persönlichen Store — der geteilte weiß nichts von ihr (§12.1).
        </p>
        {state.id === undefined ? (
          <div className="wg-leer">
            <p className="text-sm font-medium text-ton-700">Links eine Notiz auswählen.</p>
          </div>
        ) : (
          <ul className="space-y-1 text-sm">
            {(cluster.data?.items ?? []).map((eintrag) => (
              <li
                key={eintrag.id}
                className="flex items-center gap-2 rounded px-1 py-1 hover:bg-ton-50"
              >
                <span className="flex-1 truncate text-ton-700">
                  {eintrag.title ?? eintrag.id}
                </span>
                <button
                  type="button"
                  className="wg-button wg-button-klein"
                  onClick={() =>
                    verlinken.mutate({
                      store: "personal",
                      from_id: state.id as string,
                      to_id: eintrag.id,
                      to_store: "shared",
                      kind: config.edge_kinds.semantic[0] ?? "references",
                    })
                  }
                >
                  verlinken
                </button>
              </li>
            ))}
          </ul>
        )}
        {verlinken.isError && (
          <p role="alert" className="wg-fehler">
            {verlinken.error.message}
          </p>
        )}
        {verlinken.isSuccess && (
          <p role="status" className="rounded bg-ton-100 px-2.5 py-1.5 text-xs text-ton-700">
            Brücke gesetzt.
          </p>
        )}
      </div>
    </div>
  );
}
