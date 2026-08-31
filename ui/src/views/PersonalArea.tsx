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
      <div className="wg-panel space-y-3 overflow-y-auto border-l-4 border-personal">
        <header>
          <h2 className="text-base font-semibold text-personal">Persönlicher Bereich</h2>
          <p className="text-xs text-slate-600">
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
          <label className="block text-sm">
            Titel
            <input
              className="wg-input"
              aria-label="Titel"
              value={titel}
              onChange={(ereignis) => setzeTitel(ereignis.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <label className="text-sm">
              Typ
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
            <label className="text-sm">
              Scope
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
          <button type="submit" className="wg-button" disabled={titel.trim() === ""}>
            Anlegen
          </button>
          {anlegen.isError && (
            <p role="alert" className="text-xs text-red-700">
              {anlegen.error.message}
            </p>
          )}
        </form>

        <section>
          <h3 className="text-sm font-medium">Eigene Konzepte</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {(notizen.data?.items ?? []).map((eintrag) => (
              <li key={eintrag.id}>
                <button
                  type="button"
                  className={`text-left underline ${
                    state.id === eintrag.id ? "font-medium" : ""
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
        <h2 className="text-base font-semibold">Brücke schlagen</h2>
        <p className="text-xs text-slate-600">
          Aus der ausgewählten Notiz auf ein Cluster im geteilten Store. Die Kante liegt danach im
          persönlichen Store — der geteilte weiß nichts von ihr (§12.1).
        </p>
        {state.id === undefined ? (
          <p className="text-sm text-slate-500">Links eine Notiz auswählen.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {(cluster.data?.items ?? []).map((eintrag) => (
              <li key={eintrag.id} className="flex items-center gap-2">
                <span className="flex-1 truncate">{eintrag.title ?? eintrag.id}</span>
                <button
                  type="button"
                  className="wg-button"
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
          <p role="alert" className="text-xs text-red-700">
            {verlinken.error.message}
          </p>
        )}
        {verlinken.isSuccess && (
          <p role="status" className="text-xs text-manuell">
            Brücke gesetzt.
          </p>
        )}
      </div>
    </div>
  );
}
