/**
 * Ansicht 5 — Persönlicher Bereich (§17.2).
 *
 * Drei Dinge macht diese Ansicht, und alle sind Leitprinzip 2 in Bedienung übersetzt:
 *
 * 1. **Notizen und Projekte anlegen.** Ausschließlich im `personal`-Store — die API lässt nichts
 *    anderes zu (§16.2), und die Ansicht bietet deshalb gar nichts anderes an.
 * 2. **Lesen und bearbeiten.** Die ausgewählte Notiz steht im Inspektor rechts — gerendert, und
 *    weil sie die eigene ist, mit „bearbeiten" an Beschreibung und Fließtext.
 * 3. **Brücken setzen.** Aus einer Notiz heraus ein Cluster im geteilten Store verlinken. Die
 *    Kante liegt danach im *persönlichen* Store; der geteilte weiß nichts von ihr (§12.1).
 *
 * Der Hinweis, ob Embeddings lokal verfügbar sind, steht sichtbar dabei (§11.5, §17.2): Ohne
 * lokalen Anbieter bleibt der persönliche Bestand ohne Vektoren — kein Fehler, aber etwas, das
 * man wissen muss, bevor man sich über eine leere Ähnlichkeitsliste wundert.
 */

import { useState } from "react";

import {
  useAddEdge,
  useClusters,
  useConcept,
  useConcepts,
  useCreateConcept,
  useModels,
} from "../api/hooks";
import type { EffectiveConfig } from "../api/types";
import { ConceptPanel } from "../components/ConceptPanel";
import { Inspektor } from "../components/Inspektor";
import { Auswahl, Fehler, Feld, Leer, Schaltflaeche } from "../components/basis";
import type { UiState } from "../state";
import type { WerkbankZustand } from "../werkbank";

export interface PersonalAreaProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  config: EffectiveConfig;
  werkbank: WerkbankZustand;
  onWerkbank: (aenderung: Partial<WerkbankZustand>) => void;
}

export function PersonalArea({
  state,
  onChange,
  config,
  werkbank,
  onWerkbank,
}: PersonalAreaProps): JSX.Element {
  const notizen = useConcepts({ store: "personal", limit: 100 });
  const cluster = useClusters("shared");
  const anlegen = useCreateConcept();
  const verlinken = useAddEdge();
  const modelle = useModels();
  const detail = useConcept(state.id ?? null, "personal");

  const [titel, setzeTitel] = useState("");
  const [typ, setzeTyp] = useState("Note");
  const [scope, setzeScope] = useState(
    config.scopes.find((eintrag) => eintrag.store === "personal")?.name ?? "personal",
  );

  const persoenlicheTypen = config.concept_types
    .filter((eintrag) => eintrag.stores.includes("personal"))
    .map((eintrag) => eintrag.name);
  const persoenlicheScopes = config.scopes
    .filter((eintrag) => eintrag.store === "personal")
    .map((eintrag) => eintrag.name);
  const lokal = modelle.data?.tasks.find((route) => route.task === "embedding")?.local ?? false;

  return (
    <div className="flex h-full gap-3">
      <div className="wg-panel flex w-[340px] shrink-0 flex-col gap-4 overflow-y-auto border-l-4 border-personal">
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
          <Feld
            label="Titel"
            value={titel}
            onChange={(ereignis) => setzeTitel(ereignis.target.value)}
          />
          <div className="grid grid-cols-2 gap-2">
            <Auswahl
              label="Typ der Notiz"
              optionen={persoenlicheTypen}
              value={typ}
              onChange={(ereignis) => setzeTyp(ereignis.target.value)}
            />
            <Auswahl
              label="Scope der Notiz"
              optionen={persoenlicheScopes}
              value={scope}
              onChange={(ereignis) => setzeScope(ereignis.target.value)}
            />
          </div>
          <Schaltflaeche
            type="submit"
            art="primaer"
            disabled={titel.trim() === ""}
            beschaeftigt={anlegen.isPending}
          >
            Anlegen
          </Schaltflaeche>
          {anlegen.isError && <Fehler>{anlegen.error.message}</Fehler>}
        </form>

        <section className="min-h-0">
          <h3 className="wg-panel-titel">Eigene Konzepte</h3>
          {(notizen.data?.items ?? []).length === 0 ? (
            <Leer titel="Noch nichts hier.">
              Die erste Notiz entsteht oben — sie verlässt diesen Rechner nicht.
            </Leer>
          ) : (
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
          )}
        </section>
      </div>

      <div className="wg-panel min-w-0 flex-1 space-y-3 overflow-y-auto">
        <h2 className="wg-panel-titel">Brücke schlagen</h2>
        <p className="wg-hinweis">
          Aus der ausgewählten Notiz auf ein Cluster im geteilten Store. Die Kante liegt danach im
          persönlichen Store — der geteilte weiß nichts von ihr (§12.1).
        </p>
        {state.id === undefined ? (
          <Leer titel="Links eine Notiz auswählen.">
            Danach lässt sie sich hier mit dem geteilten Bestand verbinden — und rechts lesen und
            bearbeiten.
          </Leer>
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
                <Schaltflaeche
                  klein
                  beschaeftigt={verlinken.isPending}
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
                </Schaltflaeche>
              </li>
            ))}
          </ul>
        )}
        {verlinken.isError && <Fehler>{verlinken.error.message}</Fehler>}
        {verlinken.isSuccess && (
          <p role="status" className="rounded bg-ton-100 px-2.5 py-1.5 text-xs text-ton-700">
            Brücke gesetzt.
          </p>
        )}
      </div>

      <Inspektor
        titel="Inspektor"
        breite={werkbank.inspektorBreite}
        zu={werkbank.inspektorZu}
        onBreite={(inspektorBreite) => onWerkbank({ inspektorBreite })}
        onZu={(inspektorZu) => onWerkbank({ inspektorZu })}
      >
        {detail.data ? (
          <ConceptPanel detail={detail.data} onOpen={(id, store) => onChange({ id, store })} />
        ) : (
          <Leer titel="Keine Notiz ausgewählt.">
            Der Inspektor zeigt die Notiz gerendert — mit „bearbeiten" an Beschreibung und
            Fließtext.
          </Leer>
        )}
      </Inspektor>
    </div>
  );
}
