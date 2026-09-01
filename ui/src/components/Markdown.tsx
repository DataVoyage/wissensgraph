/**
 * Gerendertes Markdown für die Detailansicht (§17.2 Ansicht 2) — als eigener, kleiner Renderer.
 *
 * Warum keine Bibliothek: Ein Markdown-Paket samt Sanitizer wäre die größte neue Abhängigkeit
 * der Oberfläche, für einen Funktionsumfang, von dem die Bestände hier ein Zehntel benutzen.
 * Und die Sicherheitsfrage stellt sich bei diesem Renderer gar nicht erst: Er baut
 * **React-Knoten** statt HTML-Zeichenketten — es gibt kein `dangerouslySetInnerHTML`, also auch
 * nichts zu sanitisieren. Gespiegelte Inhalte kommen aus fremden Systemen; ein `<script>` im
 * Fließtext einer Confluence-Seite ist hier schlicht Text.
 *
 * Getragen wird, was in Notizen und gespiegelten Seiten tatsächlich vorkommt: Überschriften,
 * Absätze, Listen, Zitate, Codeblöcke, `Code`, **fett**, *kursiv* und Links (nur http/https —
 * ein `javascript:`-Link wird zu Text).
 */

import type { ReactNode } from "react";

function inline(text: string, schluessel = 0): ReactNode[] {
  const knoten: ReactNode[] = [];
  let rest = text;
  let n = schluessel;
  const muster = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)\s]+\))/;
  while (rest.length > 0) {
    const treffer = muster.exec(rest);
    if (treffer === null || treffer.index === undefined) {
      knoten.push(rest);
      break;
    }
    if (treffer.index > 0) {
      knoten.push(rest.slice(0, treffer.index));
    }
    const stueck = treffer[0];
    n += 1;
    if (stueck.startsWith("`")) {
      knoten.push(
        <code key={n} className="rounded bg-ton-100 px-1 font-mono text-[0.85em]">
          {stueck.slice(1, -1)}
        </code>,
      );
    } else if (stueck.startsWith("**")) {
      knoten.push(<strong key={n}>{stueck.slice(2, -2)}</strong>);
    } else if (stueck.startsWith("*")) {
      knoten.push(<em key={n}>{stueck.slice(1, -1)}</em>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(stueck);
      const ziel = link?.[2] ?? "";
      if (link && /^https?:\/\//i.test(ziel)) {
        knoten.push(
          <a
            key={n}
            href={ziel}
            target="_blank"
            rel="noreferrer noopener"
            className="text-signal-700 underline decoration-signal-200 hover:decoration-signal-500"
          >
            {link[1]}
          </a>,
        );
      } else {
        // Ein Link, der kein http(s) ist, wird zu Text — was er behauptet, bleibt sichtbar,
        // was er täte, passiert nicht.
        knoten.push(stueck);
      }
    }
    rest = rest.slice(treffer.index + stueck.length);
  }
  return knoten;
}

const UEBERSCHRIFT: Record<number, string> = {
  1: "mt-3 text-base font-semibold text-ton-900",
  2: "mt-3 text-sm font-semibold text-ton-900",
  3: "mt-2 text-sm font-semibold text-ton-700",
};

export function Markdown({ text }: { text: string }): JSX.Element {
  const zeilen = text.replace(/\r\n/g, "\n").split("\n");
  const bloecke: ReactNode[] = [];
  let absatz: string[] = [];
  let liste: { geordnet: boolean; punkte: string[] } | null = null;
  let code: string[] | null = null;
  let zitat: string[] = [];
  let n = 0;

  const absatzSchliessen = (): void => {
    if (absatz.length > 0) {
      n += 1;
      bloecke.push(
        <p key={n} className="my-1.5 leading-relaxed">
          {inline(absatz.join(" "), n * 1000)}
        </p>,
      );
      absatz = [];
    }
  };
  const listeSchliessen = (): void => {
    if (liste !== null) {
      n += 1;
      const punkte = liste.punkte.map((punkt, platz) => (
        <li key={platz}>{inline(punkt, n * 1000 + platz * 20)}</li>
      ));
      bloecke.push(
        liste.geordnet ? (
          <ol key={n} className="my-1.5 list-decimal space-y-0.5 pl-5">
            {punkte}
          </ol>
        ) : (
          <ul key={n} className="my-1.5 list-disc space-y-0.5 pl-5">
            {punkte}
          </ul>
        ),
      );
      liste = null;
    }
  };
  const zitatSchliessen = (): void => {
    if (zitat.length > 0) {
      n += 1;
      bloecke.push(
        <blockquote key={n} className="my-1.5 border-l-2 border-ton-300 pl-3 text-ton-500">
          {inline(zitat.join(" "), n * 1000)}
        </blockquote>,
      );
      zitat = [];
    }
  };
  const allesSchliessen = (): void => {
    absatzSchliessen();
    listeSchliessen();
    zitatSchliessen();
  };

  for (const zeile of zeilen) {
    if (code !== null) {
      if (zeile.startsWith("```")) {
        n += 1;
        bloecke.push(
          <pre key={n} className="my-1.5 overflow-x-auto rounded bg-ton-100 p-2 font-mono text-xs">
            {code.join("\n")}
          </pre>,
        );
        code = null;
      } else {
        code.push(zeile);
      }
      continue;
    }
    if (zeile.startsWith("```")) {
      allesSchliessen();
      code = [];
      continue;
    }
    const kopf = /^(#{1,6})\s+(.*)$/.exec(zeile);
    if (kopf) {
      allesSchliessen();
      n += 1;
      const stufe = Math.min(3, (kopf[1] as string).length);
      const Tag = `h${stufe + 3}` as "h4" | "h5" | "h6";
      bloecke.push(
        <Tag key={n} className={UEBERSCHRIFT[stufe]}>
          {inline(kopf[2] as string, n * 1000)}
        </Tag>,
      );
      continue;
    }
    const punkt = /^\s*[-*]\s+(.*)$/.exec(zeile);
    const nummer = /^\s*\d+\.\s+(.*)$/.exec(zeile);
    if (punkt || nummer) {
      absatzSchliessen();
      zitatSchliessen();
      const geordnet = Boolean(nummer);
      if (liste === null || liste.geordnet !== geordnet) {
        listeSchliessen();
        liste = { geordnet, punkte: [] };
      }
      liste.punkte.push((punkt?.[1] ?? nummer?.[1]) as string);
      continue;
    }
    if (zeile.startsWith(">")) {
      absatzSchliessen();
      listeSchliessen();
      zitat.push(zeile.replace(/^>\s?/, ""));
      continue;
    }
    if (zeile.trim() === "") {
      allesSchliessen();
      continue;
    }
    listeSchliessen();
    zitatSchliessen();
    absatz.push(zeile.trim());
  }
  if (code !== null) {
    n += 1;
    bloecke.push(
      <pre key={n} className="my-1.5 overflow-x-auto rounded bg-ton-100 p-2 font-mono text-xs">
        {code.join("\n")}
      </pre>,
    );
  }
  allesSchliessen();

  return <div className="text-sm text-ton-700">{bloecke}</div>;
}
