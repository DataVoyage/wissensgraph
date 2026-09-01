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
        <h2 className="wg-panel-titel">Cluster</h2>
        <p className="wg-hinweis mb-2">Ein Mitglied lässt sich auf ein Cluster ziehen.</p>
        <ul className="-mx-1 space-y-0.5 text-sm">
          {(liste.data?.items ?? []).map((eintrag) => (
            <li
              key={eintrag.id}
              data-testid={`cluster-${eintrag.id}`}
              onDragOver={(ereignis) => ereignis.preventDefault()}
              onDrop={(ereignis) => {
                ereignis.preventDefault();
                ereignis.currentTarget.classList.remove("wg-ziel");
                const nutzlast = ereignis.dataTransfer.getData("text/plain");
                const [conceptId, vonCluster] = nutzlast.split("|");
                if (conceptId && vonCluster) {
                  void verschieben(conceptId, vonCluster, eintrag.id);
                }
              }}
              className="rounded transition-shadow duration-ruhig
                [&.wg-ziel]:ring-2 [&.wg-ziel]:ring-signal-500"
              onDragEnter={(ereignis) => ereignis.currentTarget.classList.add("wg-ziel")}
              onDragLeave={(ereignis) => ereignis.currentTarget.classList.remove("wg-ziel")}
            >
              <button
                type="button"
                className={`wg-eintrag flex items-baseline gap-1.5 ${
                  state.cluster === eintrag.id ? "wg-eintrag-aktiv" : ""
                }`}
                onClick={() => {
                  setzeAuswahl(new Set());
                  onChange({ cluster: eintrag.id });
                }}
              >
                <span className="truncate">{eintrag.title ?? eintrag.id}</span>
                {eintrag.curated && (
                  <span title="Titel von Hand gesetzt" className="shrink-0 text-2xs opacity-70">
                    ✎
                  </span>
                )}
                <span className="ml-auto shrink-0 text-2xs tabular-nums opacity-60">
                  {eintrag.member_count}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="wg-panel flex flex-col gap-3 overflow-y-auto">
        {detail.data === undefined ? (
          <div className="wg-leer">
            <p className="text-sm font-medium text-ton-700">Cluster links auswählen.</p>
            <p className="wg-hinweis max-w-xs">
              Mitglieder lassen sich per Zug in ein anderes Cluster verschieben, mehrere zugleich
              ausgliedern oder zwei Cluster verschmelzen.
            </p>
          </div>
        ) : (
          <>
            <header className="-m-3 mb-0 space-y-2 border-b border-ton-200 bg-ton-50 p-3">
              <h2 className="text-base font-semibold text-ton-900">
                {detail.data.title ?? detail.data.id}
              </h2>
              <p className="wg-hinweis">
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
                  className="wg-button shrink-0"
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
              <h3 className="wg-panel-titel">
                Mitglieder
                <span className="ml-auto font-normal tabular-nums">
                  {detail.data.members.length}
                </span>
              </h3>
              <ul className="space-y-1 text-sm">
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
                    className="flex cursor-grab items-center gap-2 rounded border border-ton-200
                      bg-ton-0 px-2 py-1.5 transition-colors duration-ruhig hover:border-ton-300
                      hover:bg-ton-50 active:cursor-grabbing"
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
                    <span className="flex-1 truncate text-ton-700">
                      {mitglied.title ?? mitglied.id}
                    </span>
                    <button
                      type="button"
                      className="wg-button wg-button-klein wg-button-still"
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

            <section className="flex flex-wrap items-end gap-3 rounded-lg bg-ton-50 p-3">
              <button
                type="button"
                className="wg-button wg-button-primaer"
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
              <label className="w-56">
                <span className="wg-label">Verschmelzen mit</span>
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
                <h3 className="wg-panel-titel">Verwandte Cluster</h3>
                <ul className="-mx-1 space-y-0.5 text-sm">
                  {detail.data.related.map((eintrag) => (
                    <li key={eintrag.id}>
                      <button
                        type="button"
                        className="wg-eintrag flex items-baseline gap-2"
                        onClick={() => onChange({ cluster: eintrag.id })}
                      >
                        <span className="truncate">{eintrag.title ?? eintrag.id}</span>
                        <span className="ml-auto shrink-0 text-2xs tabular-nums text-ton-400">
                          {eintrag.similarity.toFixed(2)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
        {fehler !== null && (
          <p role="alert" className="wg-fehler">
            {fehler}
          </p>
        )}
      </div>
    </div>
  );
}
