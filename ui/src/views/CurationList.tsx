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
      <div className="wg-panel text-sm text-slate-500">
        Nichts offen. Jede generierte Kante ist bestätigt oder verworfen.
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-[260px_1fr] gap-3">
      <ul className="wg-panel space-y-1 overflow-y-auto text-sm">
        {offen.map((aufgabe, index) => (
          <li key={aufgabe.edge?.id}>
            <button
              type="button"
              className={`w-full rounded px-1 text-left ${
                index === position ? "bg-slate-100 font-medium" : ""
              }`}
              onClick={() => setzePosition(index)}
            >
              <span className="rounded bg-slate-200 px-1 text-xs">{aufgabe.edge?.kind}</span>{" "}
              {aufgabe.confidence !== null && aufgabe.confidence.toFixed(2)}
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
    <div className="wg-panel space-y-3">
      <header>
        <h2 className="text-base font-semibold">
          {kante.from_id} <span className="text-modell">— {kante.kind} →</span> {kante.to_id}
        </h2>
        <p className="text-xs text-slate-500">
          Modell: {kante.generated_by ?? "—"} · Confidence:{" "}
          {kante.confidence?.toFixed(2) ?? "—"}
        </p>
        {aufgabe.kind === "supersedes" && (
          <p className="text-xs text-modell">
            Eine Ablösung wirkt nicht von selbst — sie ist ein Vorschlag (§14.4).
          </p>
        )}
      </header>

      <div className="grid grid-cols-2 gap-3">
        {[links, rechts].map((konzept, index) => (
          <section key={index} className="rounded border border-slate-200 p-2">
            <h3 className="text-sm font-medium">{konzept?.title ?? "—"}</h3>
            <p className="text-xs text-slate-500">{konzept?.id}</p>
            <p className="mt-1 text-sm">{konzept?.description ?? "—"}</p>
          </section>
        ))}
      </div>

      {kante.reasoning !== null && (
        <section>
          <h3 className="text-sm font-medium">Begründung des Modells</h3>
          <p className="text-sm">{kante.reasoning}</p>
        </section>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          className="wg-button"
          onClick={() =>
            aktion.mutate({ id: kante.id, action: "verify", store: kante.from_store })
          }
        >
          Bestätigen (Enter)
        </button>
        <button
          type="button"
          className="wg-button"
          onClick={() =>
            aktion.mutate({ id: kante.id, action: "reject", store: kante.from_store })
          }
        >
          Verwerfen (x)
        </button>
        <span className="self-center text-xs text-slate-500">
          Später: s · Weiter: j · Zurück: k
        </span>
      </div>
      {aktion.isError && (
        <p role="alert" className="text-xs text-red-700">
          {aktion.error.message}
        </p>
      )}
    </div>
  );
}
