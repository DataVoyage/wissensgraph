/**
 * Ansicht 2 — Dokumentenbrowser (§17.2).
 *
 * Eine Tabelle mit Facettenfiltern und einer Detailspalte. Die Facetten kommen aus der
 * Konfiguration (§17.1): Scopes, Typen und Status stehen dort und nicht hier, damit eine neue
 * Taxonomie keine Codeänderung in der Oberfläche verlangt (§7.2).
 */

import { useState } from "react";

import { useConcept, useConcepts } from "../api/hooks";
import type { EffectiveConfig } from "../api/types";
import { ConceptPanel } from "../components/ConceptPanel";
import type { UiState } from "../state";

export interface DocumentBrowserProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  config: EffectiveConfig;
}

export function DocumentBrowser({
  state,
  onChange,
  config,
}: DocumentBrowserProps): JSX.Element {
  const [typ, setzeTyp] = useState<string>("");
  const [status, setzeStatus] = useState<string>("");
  const [nurLose, setzeNurLose] = useState(false);
  const [nurKuratiert, setzeNurKuratiert] = useState(false);
  const [cursor, setzeCursor] = useState<string | undefined>(undefined);

  const seite = useConcepts({
    store: state.store,
    scope: state.scope,
    type: typ || undefined,
    status: status || undefined,
    q: state.q,
    orphan: nurLose ? true : undefined,
    curated: nurKuratiert ? true : undefined,
    cursor,
    limit: 50,
  });
  const detail = useConcept(state.id ?? null, state.store);

  const typen = config.concept_types
    .filter((eintrag) => eintrag.stores.includes(state.store))
    .map((eintrag) => eintrag.name);

  return (
    <div className="grid h-full grid-cols-[1fr_340px] gap-3">
      <div className="wg-panel flex h-full flex-col gap-2 overflow-hidden">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            Suche
            <input
              className="wg-input"
              aria-label="Suche"
              defaultValue={state.q}
              onBlur={(ereignis) => {
                setzeCursor(undefined);
                onChange({ q: ereignis.target.value });
              }}
            />
          </label>
          <label className="text-sm">
            Scope
            <select
              className="wg-input"
              aria-label="Scope"
              value={state.scope ?? ""}
              onChange={(ereignis) => {
                setzeCursor(undefined);
                onChange({ scope: ereignis.target.value || undefined });
              }}
            >
              <option value="">alle</option>
              {config.scopes
                .filter((eintrag) => eintrag.store === state.store)
                .map((eintrag) => (
                  <option key={eintrag.name} value={eintrag.name}>
                    {eintrag.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="text-sm">
            Typ
            <select
              className="wg-input"
              aria-label="Typ"
              value={typ}
              onChange={(ereignis) => {
                setzeCursor(undefined);
                setzeTyp(ereignis.target.value);
              }}
            >
              <option value="">alle</option>
              {typen.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Status
            <input
              className="wg-input"
              aria-label="Status-Filter"
              value={status}
              onChange={(ereignis) => {
                setzeCursor(undefined);
                setzeStatus(ereignis.target.value);
              }}
            />
          </label>
          <label className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={nurLose}
              onChange={(ereignis) => {
                setzeCursor(undefined);
                setzeNurLose(ereignis.target.checked);
              }}
            />
            nur lose
          </label>
          <label className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={nurKuratiert}
              onChange={(ereignis) => {
                setzeCursor(undefined);
                setzeNurKuratiert(ereignis.target.checked);
              }}
            />
            nur kuratiert
          </label>
        </div>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="p-1">Titel</th>
                <th className="p-1">Typ</th>
                <th className="p-1">Scope</th>
                <th className="p-1">Status</th>
                <th className="p-1">Quelle</th>
              </tr>
            </thead>
            <tbody>
              {(seite.data?.items ?? []).map((eintrag) => (
                <tr
                  key={eintrag.id}
                  className={`cursor-pointer border-t hover:bg-slate-50 ${
                    state.id === eintrag.id ? "bg-slate-100" : ""
                  }`}
                  onClick={() => onChange({ id: eintrag.id })}
                >
                  <td className="p-1">{eintrag.title ?? eintrag.id}</td>
                  <td className="p-1">{eintrag.type}</td>
                  <td className="p-1">{eintrag.scope}</td>
                  <td className="p-1">{eintrag.status}</td>
                  <td className="p-1">{eintrag.source_name ?? "lokal"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {seite.data?.items.length === 0 && (
            <p className="p-2 text-sm text-slate-500">Kein Treffer.</p>
          )}
        </div>

        <div className="flex items-center gap-2 text-sm">
          <button
            type="button"
            className="wg-button"
            disabled={cursor === undefined}
            onClick={() => setzeCursor(undefined)}
          >
            Anfang
          </button>
          <button
            type="button"
            className="wg-button"
            disabled={!seite.data?.next_cursor}
            onClick={() => setzeCursor(seite.data?.next_cursor ?? undefined)}
          >
            Weiter
          </button>
        </div>
      </div>

      {detail.data ? (
        <ConceptPanel detail={detail.data} onOpen={(id, store) => onChange({ id, store })} />
      ) : (
        <div className="wg-panel text-sm text-slate-500">Zeile auswählen.</div>
      )}
    </div>
  );
}
