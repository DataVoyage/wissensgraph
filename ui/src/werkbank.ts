/**
 * Der Zustand der Werkbank — in `localStorage`, nicht in der URL.
 *
 * Die Grenze ist grundsätzlich: Geteilt wird, was eine Ansicht *bezeichnet* (Bereich, Store,
 * Filter, Selektion) — das steht in der URL (`state.ts`), damit ein Link dieselbe Ansicht zeigt.
 * Persönlich ist, was die Werkbank *einstellt*: Panelbreiten, eingeklappte Zonen. Wer einen Link
 * teilt, teilt seine Frage, nicht seine Fensteraufteilung.
 */

import { useCallback, useState } from "react";

export interface WerkbankZustand {
  /** Die Navigationsleiste: schmal (nur Kürzel) oder mit Text. */
  railBreit: boolean;
  /** Der Inspektor rechts: eingeklappt oder offen. */
  inspektorZu: boolean;
  /** Die Breite des offenen Inspektors in Pixeln, innerhalb der Grenzen. */
  inspektorBreite: number;
}

export const INSPEKTOR_MIN = 280;
export const INSPEKTOR_MAX = 560;

const VORGABE: WerkbankZustand = {
  railBreit: true,
  inspektorZu: false,
  inspektorBreite: 340,
};

const SCHLUESSEL = "wg.werkbank";

/** Hält einen Wert in den Grenzen — eine gemerkte Breite von einem 4K-Schirm auch. */
export function begrenzteBreite(breite: number): number {
  if (Number.isNaN(breite)) {
    return VORGABE.inspektorBreite;
  }
  return Math.min(INSPEKTOR_MAX, Math.max(INSPEKTOR_MIN, Math.round(breite)));
}

function lesen(): WerkbankZustand {
  try {
    const roh = window.localStorage.getItem(SCHLUESSEL);
    if (roh === null) {
      return VORGABE;
    }
    const geparst = JSON.parse(roh) as Partial<WerkbankZustand>;
    return {
      railBreit: typeof geparst.railBreit === "boolean" ? geparst.railBreit : VORGABE.railBreit,
      inspektorZu:
        typeof geparst.inspektorZu === "boolean" ? geparst.inspektorZu : VORGABE.inspektorZu,
      inspektorBreite: begrenzteBreite(Number(geparst.inspektorBreite)),
    };
  } catch {
    // Ein kaputter Eintrag (Handbearbeitung, alte Fassung) darf die Oberfläche nicht anhalten.
    return VORGABE;
  }
}

export function useWerkbank(): [WerkbankZustand, (aenderung: Partial<WerkbankZustand>) => void] {
  const [zustand, setzeZustand] = useState<WerkbankZustand>(lesen);

  const aendern = useCallback((aenderung: Partial<WerkbankZustand>) => {
    setzeZustand((vorher) => {
      const neu = { ...vorher, ...aenderung };
      if (aenderung.inspektorBreite !== undefined) {
        neu.inspektorBreite = begrenzteBreite(aenderung.inspektorBreite);
      }
      try {
        window.localStorage.setItem(SCHLUESSEL, JSON.stringify(neu));
      } catch {
        // Voller oder gesperrter Speicher: Die Einstellung gilt dann eben nur für diese Sitzung.
      }
      return neu;
    });
  }, []);

  return [zustand, aendern];
}
