/**
 * Ansicht 7 — Automatisierung (§17.2, U4): der Ort des Analysten.
 *
 * Was die CLI mit `wg cluster --scope … --dry-run` kann, kann jetzt die Oberfläche: je
 * Aufbaulauf ein geführtes Formular, die Vorbelegung aus der aufgelösten Konfiguration, und
 * **jeder Lauf startet zuerst als Probelauf** — das `--dry-run`-Prinzip aus §19, in die UI
 * übertragen. Erst wenn die Vorschau da ist, wird dieselbe Parametrierung scharf ausgeführt;
 * der scharfe Knopf existiert vorher gar nicht.
 *
 * Einzige Ausnahme: Embeddings. Sie sind Ableitungen und kein kuratierbarer Inhalt — ein
 * Probelauf, der nichts berechnet, wüsste nichts zu berichten (§16.2).
 *
 * Abweichungen von der Konfiguration sind angeschrieben: Wer den Regler verstellt hat, sieht
 * das — und sieht auch, wovon er abweicht.
 */

import { useState, type ReactNode } from "react";

import { useConfig, useStartRun, type RunKind } from "../api/hooks";
import type { Run } from "../api/types";
import { Fortschritt } from "../components/Fortschritt";
import { Auswahl, Fehler, Feld, Schaltflaeche } from "../components/basis";
import type { UiState } from "../state";

export interface AutomationProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

// `state`/`onChange` bleiben in der Schnittstelle, damit die Hülle alle Ansichten gleich
// behandelt — gebraucht wird hier nur die Konfiguration: Läufe hängen am Scope, nicht am Store.
export function Automation(_props: AutomationProps): JSX.Element {
  const konfiguration = useConfig();
  const [scope, setzeScope] = useState("");

  const scopes = (konfiguration.data?.scopes ?? []).map((eintrag) => eintrag.name);
  const gewaehlt = scope || scopes[0] || "";
  const vorgaben = (konfiguration.data?.orphans ?? {}) as Record<string, unknown>;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        <div className="wg-panel flex items-end gap-3">
          <div className="w-56">
            <Auswahl
              label="Scope"
              optionen={scopes}
              value={gewaehlt}
              onChange={(ereignis) => setzeScope(ereignis.target.value)}
            />
          </div>
          <p className="wg-hinweis pb-1">
            Jeder schreibende Lauf startet zuerst als <strong>Probelauf</strong> (§19): erst die
            Vorschau, dann — mit denselben Parametern — der Ernst.
          </p>
        </div>

        <LaufKarte
          titel="Embeddings"
          erklaerung="Vektoren je Konzept (§13.1). Ableitung, kein Inhalt — deshalb ohne Probelauf."
          kind="embed"
          ohneProbelauf
          koerper={(zusatz) => ({ scope: gewaehlt, ...zusatz })}
          zusammenfassung={(stats) => `${zahl(stats, "embedded")} Konzepte eingebettet.`}
        >
          {(zusatz, setzen) => (
            <label className="wg-check w-fit">
              <input
                type="checkbox"
                checked={zusatz.rebuild === true}
                onChange={(ereignis) => setzen({ rebuild: ereignis.target.checked || undefined })}
              />
              alles neu rechnen (rebuild)
            </label>
          )}
        </LaufKarte>

        <LaufKarte
          titel="Clustering"
          erklaerung="Themengruppen aus den Embeddings (§13.2). Der Probelauf gruppiert und zählt, schreibt aber nichts."
          kind="cluster"
          koerper={() => ({ scope: gewaehlt })}
          zusammenfassung={(stats) =>
            `${zahl(stats, "clusters_created")} Cluster entstünden neu, ` +
            `${zahl(stats, "clusters_matched")} würden wiedererkannt, ` +
            `${zahl(stats, "members_added")} Mitgliedschaften kämen dazu.`
          }
        />

        <LaufKarte
          titel="Relationen"
          erklaerung="Semantische Kanten zwischen Konzepten benachbarter Cluster (§14). Der Probelauf stellt die Modellfragen wirklich — sonst wüsste er nichts."
          kind="relations"
          koerper={() => ({ scope: gewaehlt })}
          zusammenfassung={(stats) => `${zahl(stats, "edges_written")} Kanten entstünden.`}
        />

        <LaufKarte
          titel="Waisen-Anbindung"
          erklaerung="Vernetzt lose Knoten (§15.4). Alle Parameter der CLI, vorbelegt aus der Konfiguration."
          kind="link-orphans"
          koerper={(zusatz) => ({ scope: gewaehlt, ...zusatz })}
          zusammenfassung={(stats) =>
            `${zahl(stats, "edges_written")} Kanten entstünden, ` +
            `${zahl(stats, "still_loose")} Knoten blieben lose.`
          }
        >
          {(zusatz, setzen) => (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
              {(
                [
                  ["loose_threshold", "Lose ab Grad ≤", 1],
                  ["proximity_top_n", "Kandidaten je Knoten", 1],
                  ["proximity_auto_commit", "Auto-Commit ab", 0.01],
                  ["proximity_candidate_band", "Kandidatenband ab", 0.01],
                  ["min_confidence", "Mindest-Confidence", 0.01],
                ] as const
              ).map(([name, label, schritt]) => (
                <ZahlMitVorgabe
                  key={name}
                  label={label}
                  schritt={schritt}
                  vorgabe={vorgaben[name]}
                  wert={zusatz[name]}
                  onWert={(wert) => setzen({ [name]: wert })}
                />
              ))}
              <label className="wg-check self-end">
                <input
                  type="checkbox"
                  checked={(zusatz.use_llm ?? vorgaben.use_llm) === true}
                  onChange={(ereignis) => setzen({ use_llm: ereignis.target.checked })}
                />
                Modell befragen (use_llm)
              </label>
            </div>
          )}
        </LaufKarte>
      </div>
    </div>
  );
}

function zahl(stats: Record<string, unknown>, name: string): string {
  const wert = stats[name];
  return typeof wert === "number" ? String(wert) : "0";
}

/** Ein Zahlenfeld, das seine Konfigurationsvorgabe kennt und Abweichung anschreibt. */
function ZahlMitVorgabe({
  label,
  schritt,
  vorgabe,
  wert,
  onWert,
}: {
  label: string;
  schritt: number;
  vorgabe: unknown;
  wert: unknown;
  onWert: (wert: number | undefined) => void;
}): JSX.Element {
  // Ein eigener Textzustand, damit sich das Feld leeren lässt: Fiele ein leeres Feld sofort
  // auf die Vorgabe zurück, hängte jede Eingabe ihre Ziffern an die Vorgabe an.
  const [text, setzeText] = useState<string | null>(null);
  const stand = text ?? String((wert ?? vorgabe ?? "") as string | number);
  const weicht = wert !== undefined && wert !== vorgabe;
  return (
    <Feld
      label={label}
      type="number"
      step={schritt}
      value={stand}
      hinweis={weicht ? `abweichend — Konfiguration: ${String(vorgabe ?? "—")}` : undefined}
      onChange={(ereignis) => {
        const roh = ereignis.target.value;
        setzeText(roh);
        onWert(roh === "" ? undefined : Number(roh));
      }}
    />
  );
}

type Zusatz = Record<string, unknown>;

interface LaufKarteProps {
  titel: string;
  erklaerung: string;
  kind: RunKind;
  /** Embeddings: Ableitung, kein Inhalt — der eine Lauf ohne Probelauf (§16.2). */
  ohneProbelauf?: boolean;
  koerper: (zusatz: Zusatz) => Record<string, unknown>;
  zusammenfassung: (stats: Record<string, unknown>) => string;
  children?: (zusatz: Zusatz, setzen: (aenderung: Zusatz) => void) => ReactNode;
}

function LaufKarte({
  titel,
  erklaerung,
  kind,
  ohneProbelauf = false,
  koerper,
  zusammenfassung,
  children,
}: LaufKarteProps): JSX.Element {
  const starten = useStartRun();
  const [zusatz, setzeZusatz] = useState<Zusatz>({});
  const [laufend, setzeLaufend] = useState<{ id: string; probe: boolean } | null>(null);
  const [vorschau, setzeVorschau] = useState<Run | null>(null);

  const setzen = (aenderung: Zusatz): void => {
    setzeZusatz((vorher) => ({ ...vorher, ...aenderung }));
    // Neue Parameter, neue Frage: Eine Vorschau zu anderen Werten wäre eine falsche Aussage.
    setzeVorschau(null);
  };

  const anstossen = (probe: boolean): void => {
    starten.mutate(
      {
        kind,
        body: { ...koerper(zusatz), ...(ohneProbelauf ? {} : { dry_run: probe }) } as never,
      },
      { onSuccess: (lauf) => setzeLaufend({ id: lauf.id, probe }) },
    );
  };

  return (
    <section className="wg-panel space-y-2" aria-label={titel}>
      <h2 className="wg-panel-titel">{titel}</h2>
      <p className="wg-hinweis">{erklaerung}</p>
      {children?.(zusatz, setzen)}

      <div className="flex items-center gap-2 pt-1">
        {ohneProbelauf ? (
          <Schaltflaeche art="primaer" beschaeftigt={starten.isPending} onClick={() => anstossen(false)}>
            Starten
          </Schaltflaeche>
        ) : (
          <>
            <Schaltflaeche
              art={vorschau === null ? "primaer" : "normal"}
              beschaeftigt={starten.isPending}
              onClick={() => anstossen(true)}
            >
              Probelauf
            </Schaltflaeche>
            {vorschau !== null && vorschau.status === "succeeded" && (
              <Schaltflaeche art="primaer" beschaeftigt={starten.isPending} onClick={() => anstossen(false)}>
                Mit diesen Parametern scharf ausführen
              </Schaltflaeche>
            )}
          </>
        )}
      </div>

      {starten.isError && <Fehler>{starten.error.message}</Fehler>}
      {laufend !== null && (
        <Fortschritt
          runId={laufend.id}
          onFertig={(lauf) => {
            if (laufend.probe) {
              setzeVorschau(lauf);
            }
          }}
        />
      )}
      {vorschau !== null && vorschau.status === "succeeded" && (
        <p role="status" className="rounded border border-ton-200 bg-ton-50 px-2.5 py-1.5 text-xs text-ton-700">
          <strong>Vorschau:</strong> {zusammenfassung(vorschau.stats)} Geschrieben wurde nichts.
        </p>
      )}
    </section>
  );
}
