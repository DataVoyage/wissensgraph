/**
 * Der Inspektor — das rechte Panel des Gerüsts (§17.5).
 *
 * Er zeigt immer das Selektierte (Knoten, Dokument, Lauf) und ist ein *echtes* Panel:
 * einklappbar und in der Breite ziehbar, mit Grenzen, damit weder ein 200-px-Streifen noch eine
 * halbe Bildschirmbreite entsteht. Breite und Zustand gehören der Werkbank (`localStorage`),
 * nicht der URL — wer einen Link teilt, teilt seine Frage, nicht seine Fensteraufteilung.
 *
 * Das Ziehen läuft über Pointer-Events mit `setPointerCapture`: Damit bleibt der Griff auch dann
 * zuständig, wenn der Zeiger beim schnellen Ziehen das schmale Griff-Element verlässt — ohne
 * Capture reißt genau dort die Bewegung ab, und das Panel "klemmt".
 */

import { useCallback, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { INSPEKTOR_MAX, INSPEKTOR_MIN } from "../werkbank";

export interface InspektorProps {
  titel: string;
  breite: number;
  zu: boolean;
  onBreite: (breite: number) => void;
  onZu: (zu: boolean) => void;
  children: ReactNode;
}

export function Inspektor({
  titel,
  breite,
  zu,
  onBreite,
  onZu,
  children,
}: InspektorProps): JSX.Element {
  const start = useRef<{ x: number; breite: number } | null>(null);

  const anfassen = useCallback(
    (ereignis: ReactPointerEvent<HTMLDivElement>) => {
      start.current = { x: ereignis.clientX, breite };
      try {
        ereignis.currentTarget.setPointerCapture(ereignis.pointerId);
      } catch {
        // Synthetische Zeigerereignisse (Tests) haben keine aktive Pointer-ID; das Ziehen
        // funktioniert dann trotzdem, nur ohne Capture.
      }
    },
    [breite],
  );

  const ziehen = useCallback(
    (ereignis: ReactPointerEvent<HTMLDivElement>) => {
      if (start.current === null) {
        return;
      }
      // Das Panel sitzt rechts: Ziehen nach links macht es breiter.
      onBreite(start.current.breite + (start.current.x - ereignis.clientX));
    },
    [onBreite],
  );

  const loslassen = useCallback(() => {
    start.current = null;
  }, []);

  if (zu) {
    return (
      <div className="flex shrink-0 flex-col items-center border-l border-ton-200 bg-ton-0 py-2">
        <button
          type="button"
          className="wg-button wg-button-still wg-button-klein"
          aria-label="Inspektor öffnen"
          title="Inspektor öffnen"
          onClick={() => onZu(false)}
        >
          <span aria-hidden="true">‹</span>
        </button>
        <span
          className="mt-2 text-2xs font-semibold uppercase tracking-wider text-ton-400"
          style={{ writingMode: "vertical-rl" }}
        >
          {titel}
        </span>
      </div>
    );
  }

  return (
    <aside
      aria-label={titel}
      className="relative flex shrink-0 flex-col border-l border-ton-200 bg-ton-0"
      style={{ width: breite }}
    >
      <div
        role="separator"
        aria-label="Inspektorbreite"
        aria-orientation="vertical"
        aria-valuenow={breite}
        aria-valuemin={INSPEKTOR_MIN}
        aria-valuemax={INSPEKTOR_MAX}
        tabIndex={0}
        className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize transition-colors duration-ruhig hover:bg-signal-200/60"
        onPointerDown={anfassen}
        onPointerMove={ziehen}
        onPointerUp={loslassen}
        onPointerCancel={loslassen}
        onKeyDown={(ereignis) => {
          // Ein Griff, den nur eine Maus bedienen kann, wäre der halbe Griff (§17.2 Ansicht 4).
          if (ereignis.key === "ArrowLeft") {
            onBreite(breite + 16);
          } else if (ereignis.key === "ArrowRight") {
            onBreite(breite - 16);
          }
        }}
      />
      <header className="flex items-center justify-between border-b border-ton-200 px-3 py-2">
        <h2 className="text-2xs font-semibold uppercase tracking-wider text-ton-500">{titel}</h2>
        <button
          type="button"
          className="wg-button wg-button-still wg-button-klein"
          aria-label="Inspektor einklappen"
          title="Inspektor einklappen"
          onClick={() => onZu(true)}
        >
          <span aria-hidden="true">›</span>
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </aside>
  );
}
