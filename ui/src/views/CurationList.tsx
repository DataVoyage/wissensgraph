/**
 * Ansicht 4 — Kurationsliste (§17.2).
 *
 * Eine Warteschlange, die sich zügig abarbeiten lassen muss. Deshalb Tastaturbedienung: `j`/`k`
 * bewegen, `Enter` bestätigt, `x` verwirft, `s` schiebt auf. §17.2 nennt das ausdrücklich, und
 * der Grund ist Arithmetik — bei hundert Vorschlägen ist der Weg zur Maus der Engpass.
 *
 * Der Unterschied zwischen Bestätigen und Verwerfen ist der zwischen "stimmt" und "stimmt nicht".
 * Nur das zweite hinterlässt einen Negativvermerk und bindet damit den nächsten Lauf (§16.2).
 */

import { useEffect, useState } from "react";

import { useEdgeAction, useQueue } from "../api/hooks";
import type { CurationTask } from "../api/types";
import type { UiState } from "../state";

export interface CurationListProps {
  state: UiState;
}

export function CurationList({ state }: CurationListProps): JSX.Element {
  const warteschlange = useQueue(state.store);
  const aktion = useEdgeAction();
  const [position, setzePosition] = useState(0);
  const [zurueckgestellt, setzeZurueckgestellt] = useState<Set<string>>(new Set());

  const offen = (warteschlange.data?.items ?? []).filter(
    (aufgabe) => aufgabe.edge !== null && !zurueckgestellt.has(aufgabe.edge.id),
  );
  const aktuell = offen[Math.min(position, Math.max(offen.length - 1, 0))];

  useEffect(() => {
    function beiTaste(ereignis: KeyboardEvent): void {
      // Wer tippt, kuratiert nicht: Seit die Kopfzeile ein Suchfeld trägt (U3), wäre ein "x"
      // im Suchbegriff sonst ein verworfener Vorschlag.
      const ziel = ereignis.target as HTMLElement | null;
      if (ziel !== null && (ziel.tagName === "INPUT" || ziel.tagName === "TEXTAREA")) {
        return;
      }
      if (aktuell?.edge == null) {
        return;
      }
      const kante = aktuell.edge;
      if (ereignis.key === "j") {
        setzePosition((vorher) => Math.min(vorher + 1, offen.length - 1));
      } else if (ereignis.key === "k") {
        setzePosition((vorher) => Math.max(vorher - 1, 0));
      } else if (ereignis.key === "Enter") {
        aktion.mutate({ id: kante.id, action: "verify", store: kante.from_store });
      } else if (ereignis.key === "x") {
        aktion.mutate({ id: kante.id, action: "reject", store: kante.from_store });
      } else if (ereignis.key === "s") {
        setzeZurueckgestellt((vorher) => new Set(vorher).add(kante.id));
      }
    }
    window.addEventListener("keydown", beiTaste);
    return () => window.removeEventListener("keydown", beiTaste);
  }, [aktuell, offen.length, aktion]);

  if (offen.length === 0) {
    return (
      <div className="wg-panel text-sm text-ton-500">
        Nichts offen. Jede generierte Kante ist bestätigt oder verworfen.
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-[260px_1fr] gap-3">
      <ul className="wg-panel -mx-0 space-y-0.5 overflow-y-auto text-sm">
        {offen.map((aufgabe, index) => (
          <li key={aufgabe.edge?.id}>
            <button
              type="button"
              className={`wg-eintrag ${index === position ? "wg-eintrag-aktiv" : ""}`}
              onClick={() => setzePosition(index)}
            >
              <span className="flex items-center gap-2">
                <span className="wg-chip shrink-0">{aufgabe.edge?.kind}</span>
                <span className="truncate text-xs opacity-80">
                  {aufgabe.concepts[0]?.title ?? aufgabe.edge?.from_id}
                </span>
                <span className="ml-auto shrink-0 text-2xs tabular-nums opacity-70">
                  {aufgabe.confidence !== null ? aufgabe.confidence.toFixed(2) : "—"}
                </span>
              </span>
              {/* Die Confidence als Balken statt nur als Zahl: Eine Warteschlange ist nach ihr
                  sortiert, und wo sie abfällt, sieht man an einer Kante schneller als an einer
                  Nachkommastelle. */}
              {aufgabe.confidence !== null && (
                <span
                  aria-hidden="true"
                  className="mt-1 block h-0.5 rounded bg-signal-500"
                  style={{ width: `${Math.round(aufgabe.confidence * 100)}%` }}
                />
              )}
            </button>
          </li>
        ))}
      </ul>

      {aktuell && <Aufgabe aufgabe={aktuell} />}
    </div>
  );
}

function Aufgabe({ aufgabe }: { aufgabe: CurationTask }): JSX.Element {
  const aktion = useEdgeAction();
  const kante = aufgabe.edge;
  if (kante === null) {
    return <div className="wg-panel text-sm">Kein Kantenvorschlag.</div>;
  }
  const [links, rechts] = [
    aufgabe.concepts.find((eintrag) => eintrag.id === kante.from_id),
    aufgabe.concepts.find((eintrag) => eintrag.id === kante.to_id),
  ];

  return (
    <div className="wg-panel flex flex-col gap-4 overflow-y-auto">
      <header className="-m-3 mb-0 border-b border-ton-200 bg-ton-50 p-3">
        <h2 className="flex flex-wrap items-center gap-2 font-mono text-sm text-ton-800">
          {kante.from_id}
          <span className="rounded bg-signal-500 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider text-ton-0">
            {kante.kind} →
          </span>
          {kante.to_id}
        </h2>
        <p className="wg-hinweis mt-1.5">
          Modell: {kante.generated_by ?? "—"} · Confidence: {kante.confidence?.toFixed(2) ?? "—"}
        </p>
        {aufgabe.kind === "supersedes" && (
          <p className="wg-fehler mt-1.5">
            Eine Ablösung wirkt nicht von selbst — sie ist ein Vorschlag (§14.4).
          </p>
        )}
      </header>

      <div className="grid grid-cols-2 gap-3">
        {[links, rechts].map((konzept, index) => (
          <section key={index} className="rounded-lg border border-ton-200 bg-ton-50 p-3">
            <h3 className="text-sm font-semibold text-ton-900">{konzept?.title ?? "—"}</h3>
            <p className="mt-0.5 font-mono text-2xs text-ton-500">{konzept?.id}</p>
            <p className="mt-2 text-sm leading-relaxed text-ton-700">
              {konzept?.description ?? "—"}
            </p>
          </section>
        ))}
      </div>

      {kante.reasoning !== null && (
        <section>
          <h3 className="wg-panel-titel">Begründung des Modells</h3>
          <p className="rounded border-l-2 border-signal-500 bg-signal-50 px-3 py-2 text-sm leading-relaxed text-ton-700">
            {kante.reasoning}
          </p>
        </section>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-ton-200 pt-3">
        <button
          type="button"
          className="wg-button wg-button-primaer"
          onClick={() => aktion.mutate({ id: kante.id, action: "verify", store: kante.from_store })}
        >
          Bestätigen <kbd className="opacity-70">⏎</kbd>
        </button>
        <button
          type="button"
          className="wg-button wg-button-gefahr"
          onClick={() => aktion.mutate({ id: kante.id, action: "reject", store: kante.from_store })}
        >
          Verwerfen <kbd className="opacity-70">x</kbd>
        </button>
        <span className="ml-auto text-2xs text-ton-500">
          Später <kbd className="wg-chip">s</kbd> · Weiter <kbd className="wg-chip">j</kbd> ·
          Zurück <kbd className="wg-chip">k</kbd>
        </span>
      </div>
      {aktion.isError && (
        <p role="alert" className="wg-fehler">
          {aktion.error.message}
        </p>
      )}
    </div>
  );
}
