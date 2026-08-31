/**
 * Ansicht 3 — Cluster-Arbeitsplatz (§17.2).
 *
 * Die zentrale Reorganisationsfläche: Cluster links, Mitglieder rechts, Verschieben per
 * Drag-and-Drop. Was dabei passiert, ist zweierlei und nicht eines — die alte `member`-Kante wird
 * entfernt **und** als Ausschluss vermerkt (§13.4), die neue mit `curated = true` geschrieben.
 * Genau daran hängt das Abnahmekriterium aus §24: Die Handbewegung überlebt den nächsten
 * Clustering-Lauf.
 *
 * Drag-and-Drop mit den HTML5-Ereignissen und ohne Bibliothek: Die Geste ist eine Zeile
 * `dataTransfer`, und eine Abhängigkeit dafür wäre mehr Fläche als Nutzen.
 */

import { useState } from "react";

import {
  useAddMembers,
  useCluster,
  useClusters,
  useMergeClusters,
  usePatchCluster,
  useRemoveMember,
  useSplitCluster,
} from "../api/hooks";
import type { UiState } from "../state";

export interface ClusterWorkbenchProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

export function ClusterWorkbench({ state, onChange }: ClusterWorkbenchProps): JSX.Element {
  const liste = useClusters(state.store, state.scope);
  const detail = useCluster(state.cluster ?? null, state.store);
  const entfernen = useRemoveMember();
  const hinzufuegen = useAddMembers();
  const umbenennen = usePatchCluster();
  const ausgliedern = useSplitCluster();
  const verschmelzen = useMergeClusters();

  const [auswahl, setzeAuswahl] = useState<Set<string>>(new Set());
  const [titel, setzeTitel] = useState("");
  const [fehler, setzeFehler] = useState<string | null>(null);

  /**
   * Verschiebt ein Mitglied in ein anderes Cluster.
   *
   * Erst entfernen, dann hinzufügen — in dieser Reihenfolge: Andersherum hinge das Konzept kurz
   * in beiden Clustern, und ein Clustering-Lauf, der dazwischen startet, sähe einen Zustand, den
   * niemand gewollt hat.
   */
  async function verschieben(conceptId: string, vonCluster: string, nachCluster: string) {
    if (vonCluster === nachCluster) {
      return;
    }
    try {
      await entfernen.mutateAsync({
        clusterId: vonCluster,
        conceptId,
        store: state.store,
      });
      await hinzufuegen.mutateAsync({
        id: nachCluster,
        store: state.store,
        body: { concept_ids: [conceptId] },
      });
      setzeFehler(null);
    } catch (ausnahme) {
      setzeFehler(ausnahme instanceof Error ? ausnahme.message : String(ausnahme));
    }
  }

  return (
    <div className="grid h-full grid-cols-[280px_1fr] gap-3">
      <div className="wg-panel overflow-y-auto">
        <h2 className="text-sm font-semibold">Cluster</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {(liste.data?.items ?? []).map((eintrag) => (
            <li
              key={eintrag.id}
              data-testid={`cluster-${eintrag.id}`}
              onDragOver={(ereignis) => ereignis.preventDefault()}
              onDrop={(ereignis) => {
                ereignis.preventDefault();
                const nutzlast = ereignis.dataTransfer.getData("text/plain");
                const [conceptId, vonCluster] = nutzlast.split("|");
                if (conceptId && vonCluster) {
                  void verschieben(conceptId, vonCluster, eintrag.id);
                }
              }}
              className={`rounded px-1 ${
                state.cluster === eintrag.id ? "bg-slate-100 font-medium" : ""
              }`}
            >
              <button
                type="button"
                className="w-full text-left"
                onClick={() => {
                  setzeAuswahl(new Set());
                  onChange({ cluster: eintrag.id });
                }}
              >
                {eintrag.title ?? eintrag.id}
                <span className="ml-1 text-xs text-slate-500">({eintrag.member_count})</span>
                {eintrag.curated && (
                  <span className="ml-1 text-xs text-manuell" title="Titel von Hand gesetzt">
                    ✎
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="wg-panel flex flex-col gap-3 overflow-y-auto">
        {detail.data === undefined ? (
          <p className="text-sm text-slate-500">Cluster links auswählen.</p>
        ) : (
          <>
            <header className="space-y-1">
              <h2 className="text-base font-semibold">{detail.data.title ?? detail.data.id}</h2>
              <p className="text-xs text-slate-500">
                {detail.data.members.length} Mitglieder ·{" "}
                {detail.data.centroid_age_seconds === null
                  ? "kein Zentroid"
                  : `Zentroid ${Math.round(detail.data.centroid_age_seconds / 60)} min alt`}
                {detail.data.manual_title && " · von Hand benannt, keine automatische Neubetitelung"}
              </p>
              <div className="flex gap-2">
                <input
                  className="wg-input"
                  aria-label="Neuer Titel"
                  value={titel}
                  placeholder={detail.data.title ?? ""}
                  onChange={(ereignis) => setzeTitel(ereignis.target.value)}
                />
                <button
                  type="button"
                  className="wg-button"
                  disabled={titel.trim() === ""}
                  onClick={() =>
                    umbenennen.mutate({
                      id: detail.data.id,
                      store: state.store,
                      patch: { title: titel },
                    })
                  }
                >
                  Umbenennen
                </button>
              </div>
            </header>

            <section>
              <h3 className="text-sm font-medium">Mitglieder</h3>
              <ul className="mt-1 space-y-1 text-sm">
                {detail.data.members.map((mitglied) => (
                  <li
                    key={mitglied.id}
                    draggable
                    data-testid={`mitglied-${mitglied.id}`}
                    onDragStart={(ereignis) =>
                      ereignis.dataTransfer.setData(
                        "text/plain",
                        `${mitglied.id}|${detail.data.id}`,
                      )
                    }
                    className="flex cursor-grab items-center gap-2 rounded border border-slate-200 px-2 py-1"
                  >
                    <input
                      type="checkbox"
                      aria-label={`Auswahl ${mitglied.id}`}
                      checked={auswahl.has(mitglied.id)}
                      onChange={(ereignis) =>
                        setzeAuswahl((vorher) => {
                          const neu = new Set(vorher);
                          if (ereignis.target.checked) {
                            neu.add(mitglied.id);
                          } else {
                            neu.delete(mitglied.id);
                          }
                          return neu;
                        })
                      }
                    />
                    <span className="flex-1 truncate">{mitglied.title ?? mitglied.id}</span>
                    <button
                      type="button"
                      className="wg-button"
                      onClick={() =>
                        entfernen.mutate({
                          clusterId: detail.data.id,
                          conceptId: mitglied.id,
                          store: state.store,
                        })
                      }
                    >
                      entfernen
                    </button>
                  </li>
                ))}
              </ul>
            </section>

            <section className="flex flex-wrap items-end gap-2">
              <button
                type="button"
                className="wg-button"
                disabled={auswahl.size === 0 || titel.trim() === ""}
                onClick={() =>
                  ausgliedern.mutate(
                    {
                      id: detail.data.id,
                      store: state.store,
                      body: { concept_ids: [...auswahl], title: titel },
                    },
                    { onSuccess: () => setzeAuswahl(new Set()) },
                  )
                }
              >
                Als neues Cluster ausgliedern
              </button>
              <label className="text-sm">
                Verschmelzen mit
                <select
                  className="wg-input"
                  aria-label="Verschmelzen mit"
                  defaultValue=""
                  onChange={(ereignis) => {
                    const ziel = ereignis.target.value;
                    if (ziel !== "") {
                      verschmelzen.mutate(
                        {
                          store: state.store,
                          source_id: detail.data.id,
                          target_id: ziel,
                        },
                        { onSuccess: () => onChange({ cluster: ziel }) },
                      );
                    }
                  }}
                >
                  <option value="">—</option>
                  {(liste.data?.items ?? [])
                    .filter((eintrag) => eintrag.id !== detail.data.id)
                    .map((eintrag) => (
                      <option key={eintrag.id} value={eintrag.id}>
                        {eintrag.title ?? eintrag.id}
                      </option>
                    ))}
                </select>
              </label>
            </section>

            {detail.data.related.length > 0 && (
              <section>
                <h3 className="text-sm font-medium">Verwandte Cluster</h3>
                <ul className="text-sm">
                  {detail.data.related.map((eintrag) => (
                    <li key={eintrag.id}>
                      <button
                        type="button"
                        className="underline"
                        onClick={() => onChange({ cluster: eintrag.id })}
                      >
                        {eintrag.title ?? eintrag.id}
                      </button>{" "}
                      <span className="text-xs text-slate-500">
                        {eintrag.similarity.toFixed(2)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
        {fehler !== null && (
          <p role="alert" className="text-xs text-red-700">
            {fehler}
          </p>
        )}
      </div>
    </div>
  );
}
