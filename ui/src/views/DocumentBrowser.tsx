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
import { Inspektor } from "../components/Inspektor";
import type { UiState } from "../state";
import type { WerkbankZustand } from "../werkbank";

export interface DocumentBrowserProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
  config: EffectiveConfig;
  werkbank: WerkbankZustand;
  onWerkbank: (aenderung: Partial<WerkbankZustand>) => void;
}

export function DocumentBrowser({
  state,
  onChange,
  config,
  werkbank,
  onWerkbank,
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
    <div className="flex h-full gap-3">
      <div className="wg-panel-blank flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex flex-wrap items-end gap-3 border-b border-ton-200 bg-ton-50 p-3">
          <label className="min-w-[12rem] flex-1">
            <span className="wg-label">Suche</span>
            <input
              className="wg-input"
              aria-label="Suche"
              placeholder="Titel oder Inhalt …"
              defaultValue={state.q}
              onBlur={(ereignis) => {
                setzeCursor(undefined);
                onChange({ q: ereignis.target.value });
              }}
            />
          </label>
          <label className="w-40">
            <span className="wg-label">Scope</span>
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
          <label className="w-44">
            <span className="wg-label">Typ</span>
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
          <label className="w-32">
            <span className="wg-label">Status</span>
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
          <div className="flex gap-1 pb-0.5">
            <label className="wg-check">
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
            <label className="wg-check">
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
        </div>

        <div className="flex-1 overflow-auto">
          <table className="wg-tabelle">
            <thead>
              <tr>
                <th>Titel</th>
                <th>Typ</th>
                <th>Scope</th>
                <th>Status</th>
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>
              {(seite.data?.items ?? []).map((eintrag) => (
                <tr
                  key={eintrag.id}
                  className="cursor-pointer"
                  aria-selected={state.id === eintrag.id}
                  onClick={() => onChange({ id: eintrag.id })}
                >
                  <td className="font-medium text-ton-800">{eintrag.title ?? eintrag.id}</td>
                  <td className="text-ton-600">{eintrag.type}</td>
                  <td className="text-ton-600">{eintrag.scope}</td>
                  <td>
                    <span className="wg-chip">{eintrag.status}</span>
                  </td>
                  <td className="text-ton-500">{eintrag.source_name ?? "lokal"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {seite.data?.items.length === 0 && (
            <p className="p-4 text-sm text-ton-500">Kein Treffer.</p>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-ton-200 bg-ton-50 p-2">
          <button
            type="button"
            className="wg-button wg-button-klein"
            disabled={cursor === undefined}
            onClick={() => setzeCursor(undefined)}
          >
            Anfang
          </button>
          <button
            type="button"
            className="wg-button wg-button-klein"
            disabled={!seite.data?.next_cursor}
            onClick={() => setzeCursor(seite.data?.next_cursor ?? undefined)}
          >
            Weiter
          </button>
          <span className="ml-auto text-2xs tabular-nums text-ton-500">
            {seite.data?.items.length ?? 0} Zeilen
          </span>
        </div>
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
          <div className="wg-leer">
            <p className="text-sm font-medium text-ton-700">Zeile auswählen.</p>
            <p className="wg-hinweis max-w-[15rem]">
              Der Inspektor zeigt Felder, Provenienz, Kanten und das Änderungsjournal.
            </p>
          </div>
        )}
      </Inspektor>
    </div>
  );
}
