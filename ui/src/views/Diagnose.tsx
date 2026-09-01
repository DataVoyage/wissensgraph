/**
 * Verwalten → Konfiguration & Diagnose (§17.2 Ansicht 6, U5).
 *
 * Die Diagnose ist dieselbe wie `wg doctor` — derselbe Endpunkt, dieselben Prüfungen, mit
 * Ampel (Leitprinzip 14). Sie läuft auf Knopfdruck und nicht im Intervall: Die Prüfungen
 * verbinden sich wirklich mit den Stores.
 *
 * Die Schemamigration (`wg migrate`) bleibt bewusst außerhalb der UI (§17.2): Sie gehört an
 * die Konsole, nicht hinter einen Knopf.
 */

import { useConfig, useDoctor } from "../api/hooks";
import { Fehler, Schaltflaeche } from "../components/basis";
import type { UiState } from "../state";

export interface DiagnoseProps {
  state: UiState;
  onChange: (aenderung: Partial<UiState>) => void;
}

const AMPEL: Record<string, { farbe: string; text: string }> = {
  ok: { farbe: "bg-emerald-500", text: "ok" },
  warn: { farbe: "bg-signal-300", text: "warn" },
  fail: { farbe: "bg-signal-600", text: "fail" },
};

export function Diagnose(_props: DiagnoseProps): JSX.Element {
  const diagnose = useDoctor();
  const konfiguration = useConfig();
  // Kein Rückfallwert: Solange die Konfiguration nicht da ist, wird der Modus nicht behauptet
  // (§17.1). Ein geratenes "token" stünde sonst kurz auf dem Schirm und wäre womöglich falsch.
  const modus = konfiguration.data?.api.auth_mode;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-3">
        {/* Die Arbeitsbereiche ordnen die Navigation, sie sind keine Rechte (§17.2). Solange
            das nicht durch `oidc` gedeckt ist, muss es dort stehen, wo jemand sonst die
            Sichtbarkeit für Absicherung halten könnte — im Verwalten-Bereich. */}
        {modus !== undefined && modus !== "oidc" && (
          <p className="wg-panel border-l-4 border-ton-700 text-xs leading-relaxed text-ton-600">
            <strong className="text-ton-800">Die Bereiche ordnen, sie sperren nicht.</strong>{" "}
            Diese Installation läuft mit <code>auth_mode: {modus}</code>; wer die Oberfläche
            öffnet, darf alles, was §17.4 der UI erlaubt — auch das, was hier unter „Verwalten"
            steht. Erst mit <code>oidc</code> liefert das Token eine Rolle, und aus den
            Bereichen werden Berechtigungsgrenzen (§20.3).
          </p>
        )}
        <section className="wg-panel space-y-2" aria-label="Diagnose">
          <h2 className="wg-panel-titel">Diagnose</h2>
          <div className="flex items-center gap-3">
            <Schaltflaeche
              art="primaer"
              beschaeftigt={diagnose.isFetching}
              onClick={() => void diagnose.refetch()}
            >
              Diagnose ausführen
            </Schaltflaeche>
            <p className="wg-hinweis">
              Dieselben Prüfungen wie <code>wg doctor</code>: Verbindungen, Provider, Adapter,
              Policies. Die Schemamigration bleibt an der Konsole (<code>wg migrate</code>).
            </p>
          </div>

          {diagnose.isError && <Fehler>{diagnose.error.message}</Fehler>}
          {diagnose.data !== undefined && (
            <>
              <p
                role="status"
                className={`rounded px-2.5 py-1.5 text-sm font-medium ${
                  diagnose.data.healthy
                    ? "bg-ton-100 text-ton-800"
                    : "bg-signal-50 text-signal-700"
                }`}
              >
                {diagnose.data.healthy
                  ? "Alles in Ordnung — keine Prüfung fehlgeschlagen."
                  : "Mindestens eine Prüfung ist fehlgeschlagen."}
              </p>
              <ul className="space-y-0.5 text-sm">
                {diagnose.data.checks.map((pruefung) => {
                  const ampel = AMPEL[pruefung.status] ?? AMPEL.fail;
                  return (
                    <li
                      key={pruefung.name}
                      className="flex items-baseline gap-2 rounded px-1 py-1 hover:bg-ton-50"
                    >
                      <span
                        aria-label={ampel?.text}
                        className={`mt-1 h-2.5 w-2.5 shrink-0 self-start rounded-full ${ampel?.farbe}`}
                      />
                      <span className="w-44 shrink-0 font-mono text-xs text-ton-800">
                        {pruefung.name}
                      </span>
                      <span className="min-w-0 text-xs leading-relaxed text-ton-600">
                        {pruefung.detail}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        <section className="wg-panel space-y-2" aria-label="Aufgelöste Konfiguration">
          <h2 className="wg-panel-titel">Aufgelöste Konfiguration</h2>
          <p className="wg-hinweis">Secrets sind maskiert (§20.2).</p>
          <pre className="max-h-96 overflow-auto rounded bg-ton-900 p-3 font-mono text-2xs leading-relaxed text-ton-100">
            {JSON.stringify(konfiguration.data ?? {}, null, 2)}
          </pre>
        </section>
      </div>
    </div>
  );
}
