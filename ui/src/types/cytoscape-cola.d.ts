/**
 * `cytoscape-cola` liefert keine Typen mit.
 *
 * Beschrieben wird deshalb nur, was diese Oberfläche benutzt: die Registrierung als
 * Cytoscape-Erweiterung. Die Optionen der Simulation bleiben absichtlich außen vor — sie werden
 * in `GraphCanvas.tsx` an genau einer Stelle zusammengesetzt und dort einmal in `LayoutOptions`
 * überführt. Eine hier nachgebaute Optionsliste wäre eine zweite Wahrheit, die beim nächsten
 * Versionssprung des Pakets still falsch würde, ohne dass ein Test es merkt.
 */
declare module "cytoscape-cola" {
  import type cytoscape from "cytoscape";

  const erweiterung: cytoscape.Ext;
  export default erweiterung;
}
