/**
 * Tailwind mit einem zentralen Token-Set für Farben und Abstände (§17.1).
 *
 * Die Tokens stehen hier und nicht verstreut in den Komponenten, weil §17.2 eine visuelle
 * Kodierung festlegt, die durchgehend gelten muss: Store über die Knotenform, Typ über die Farbe,
 * Provenienz über die Linienfarbe. Eine Ansicht, die sich ihre Farbe selbst aussucht, bricht die
 * Aussage.
 *
 * **Die Palette ist Grau, Weiß und Rot.** Rot ist dabei knapp bemessen und deshalb aussagekräftig:
 * Es markiert die Marke, die eine ausgeführte Hauptaktion je Fläche — und alles, was auf einen
 * Menschen wartet. Ein zweites Rot für "Typ 7 von 12" nähme dem ersten seine Bedeutung. Aus
 * demselben Grund tragen die Konzepttypen eine eigene, bewusst gedämpfte Reihe (siehe
 * `src/theme.ts`), in der kein Rot vorkommt.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Die Marke. `signal` ist das Rot der Bedienoberfläche, `signal-ink` die Variante, die
        // auf Weiß noch den Kontrastwert AA erreicht — reines Markenrot tut das als Textfarbe
        // knapp nicht, und ein Warnhinweis, den man nicht liest, ist keiner.
        signal: {
          50: "#fdecee",
          100: "#fbd5d8",
          200: "#f7a8ae",
          300: "#f2757f",
          400: "#ea404e",
          500: "#e10915",
          600: "#c00812",
          700: "#9c060f",
          800: "#75050b",
          900: "#4d0307",
        },
        // Die Grauskala trägt alles andere. Sie ist minimal ins Neutrale gezogen und nicht blau,
        // damit das Rot daneben warm bleibt statt zu kippen.
        ton: {
          0: "#ffffff",
          50: "#f8f8f9",
          100: "#f1f1f3",
          200: "#e4e4e8",
          300: "#cfcfd6",
          400: "#a1a1ab",
          500: "#75757f",
          600: "#565660",
          700: "#3e3e46",
          800: "#2a2a30",
          900: "#18181b",
        },
        // Die Provenienz einer Kante (§17.2): manuell, Code, Modell. Ihre Werte stehen in
        // `src/theme.ts`, weil Cytoscape sie als Zeichenkette braucht und nicht als Klasse; hier
        // stehen sie zusätzlich, damit Legende und Panel dieselbe Farbe über Tailwind erreichen.
        manuell: "#2a2a30",
        code: "#a1a1ab",
        modell: "#e10915",
        // Der persönliche Bereich ist durchgehend als solcher gekennzeichnet (§17.2 Ansicht 5).
        // Er darf nicht rot sein — Rot heißt in dieser Oberfläche "sieh her", und ein Store ist
        // kein Hinweis, sondern ein Ort.
        personal: "#3e3e46",
        shared: "#a1a1ab",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        // Eine engere Leiter als die Vorgabe: Diese Oberfläche zeigt viele kleine Datenfelder,
        // und die Standardsprünge von Tailwind erzeugen dabei mehr Unruhe als Hierarchie.
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: {
        DEFAULT: "0.375rem",
      },
      boxShadow: {
        karte: "0 1px 2px 0 rgb(24 24 27 / 0.05), 0 1px 3px 0 rgb(24 24 27 / 0.06)",
        schwebend: "0 4px 6px -1px rgb(24 24 27 / 0.08), 0 10px 24px -4px rgb(24 24 27 / 0.12)",
      },
      transitionDuration: {
        ruhig: "160ms",
      },
      keyframes: {
        einblenden: {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "none" },
        },
        pulsen: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        einblenden: "einblenden 160ms ease-out",
        pulsen: "pulsen 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
