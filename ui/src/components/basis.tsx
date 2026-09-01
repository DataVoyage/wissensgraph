/**
 * Der Komponentensatz der Oberfläche — einmal gebaut, überall benutzt.
 *
 * Bisher trugen die Ansichten rohe `wg-*`-Klassen und bauten sich Knöpfe und Felder ad hoc.
 * Dass zwei Knöpfe gleich *aussehen*, garantiert dann nicht, dass sie sich gleich *verhalten* —
 * genau dort lagen die "Buttons funktionieren nicht richtig"-Defekte. Diese Datei macht aus den
 * Klassen Komponenten mit festen Zuständen: hover, focus, disabled und busy sind hier einmal
 * entschieden und nicht in jeder Ansicht erneut.
 *
 * Die Palette bleibt die beschlossene (Grau, Weiß, knappes Rot); dieser Satz fügt keine Farbe
 * hinzu, er verteilt die vorhandenen verlässlich.
 */

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

/** Die Gewichte einer Schaltfläche. `primaer` gibt es höchstens einmal je Fläche. */
export type SchaltflaechenArt = "primaer" | "normal" | "still" | "gefahr";

const ARTEN: Record<SchaltflaechenArt, string> = {
  primaer: "wg-button wg-button-primaer",
  normal: "wg-button",
  still: "wg-button wg-button-still",
  gefahr: "wg-button wg-button-gefahr",
};

export interface SchaltflaecheProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  art?: SchaltflaechenArt;
  klein?: boolean;
  /**
   * Eine laufende Aktion sperrt die Schaltfläche und sagt das auch. Ein Knopf, der beim zweiten
   * Klick dieselbe Mutation noch einmal abschickt, ist der klassische Doppelklick-Defekt.
   */
  beschaeftigt?: boolean;
}

export function Schaltflaeche({
  art = "normal",
  klein = false,
  beschaeftigt = false,
  className = "",
  disabled,
  children,
  type,
  ...rest
}: SchaltflaecheProps): JSX.Element {
  return (
    <button
      // Vorgabe ist bewusst "button": Ein vergessenes `type` in einem Formular löst sonst
      // submit aus — der zweitklassischste Knopf-Defekt.
      type={type ?? "button"}
      className={`${ARTEN[art]} ${klein ? "wg-button-klein" : ""} ${className}`}
      disabled={disabled === true || beschaeftigt}
      aria-busy={beschaeftigt || undefined}
      {...rest}
    >
      {beschaeftigt && (
        <span aria-hidden="true" className="wg-skelett inline-block h-2 w-2 rounded-full" />
      )}
      {children}
    </button>
  );
}

export interface FeldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  /** Sichtbar gesperrt, nicht nur schreibgeschützt (§17.3). */
  gesperrt?: boolean;
  hinweis?: string;
}

export function Feld({ label, gesperrt = false, hinweis, className = "", ...rest }: FeldProps): JSX.Element {
  return (
    <label className="block">
      <span className="wg-label">{label}</span>
      <input
        className={`wg-input ${gesperrt ? "wg-locked" : ""} ${className}`}
        readOnly={gesperrt || rest.readOnly}
        aria-label={label}
        {...rest}
      />
      {hinweis !== undefined && <span className="wg-hinweis mt-0.5 block">{hinweis}</span>}
    </label>
  );
}

export interface AuswahlProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  /** Leerer Eintrag oben — "alle", "keine Angabe". `undefined` heißt: kein leerer Eintrag. */
  leer?: string;
  optionen: ReadonlyArray<string | { wert: string; text: string }>;
}

export function Auswahl({ label, leer, optionen, className = "", ...rest }: AuswahlProps): JSX.Element {
  return (
    <label className="block">
      <span className="wg-label">{label}</span>
      <select className={`wg-input ${className}`} aria-label={label} {...rest}>
        {leer !== undefined && <option value="">{leer}</option>}
        {optionen.map((eintrag) => {
          const { wert, text } = typeof eintrag === "string" ? { wert: eintrag, text: eintrag } : eintrag;
          return (
            <option key={wert} value={wert}>
              {text}
            </option>
          );
        })}
      </select>
    </label>
  );
}

/** Ein gestalteter Leerzustand — nie ein leeres Rechteck. */
export function Leer({ titel, children }: { titel: string; children?: ReactNode }): JSX.Element {
  return (
    <div className="wg-leer">
      <p className="text-sm font-medium text-ton-700">{titel}</p>
      {children !== undefined && <div className="wg-hinweis max-w-xs">{children}</div>}
    </div>
  );
}

/** Ein Ladezustand mit Ansage — für Leser von Screenreadern genauso wie für alle anderen. */
export function Laden({ was }: { was: string }): JSX.Element {
  return (
    <p role="status" className="text-sm text-ton-500">
      {was} …
    </p>
  );
}

/** Eine Fehlermeldung, wie die API sie liefert — rot, weil ein Fehler auf einen Menschen wartet. */
export function Fehler({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p role="alert" className="wg-fehler">
      {children}
    </p>
  );
}
