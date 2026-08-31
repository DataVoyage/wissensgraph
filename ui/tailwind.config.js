/**
 * Tailwind mit einem zentralen Token-Set für Farben und Abstände (§17.1).
 *
 * Die Tokens stehen hier und nicht verstreut in den Komponenten, weil §17.2 eine visuelle
 * Kodierung festlegt, die durchgehend gelten muss: Store über die Knotenform, Typ über die Farbe,
 * Provenienz über die Linienfarbe. Eine Ansicht, die sich ihre Farbe selbst aussucht, bricht die
 * Aussage.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Die Provenienz einer Kante (§17.2): manuell, Code, Modell.
        manuell: "#0f766e",
        code: "#475569",
        modell: "#a16207",
        // Der persönliche Bereich ist durchgehend als solcher gekennzeichnet (§17.2 Ansicht 5).
        personal: "#7c3aed",
        shared: "#0369a1",
      },
    },
  },
  plugins: [],
};
