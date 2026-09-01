/**
 * `cytoscape-fcose` liefert keine Typen mit.
 *
 * Wie bei `cytoscape-cola` wird nur die Registrierung beschrieben. Die Optionen werden in
 * `GraphCanvas.tsx` an einer Stelle zusammengesetzt und dort einmal in `LayoutOptions` überführt;
 * eine hier nachgebaute Optionsliste wäre eine zweite Wahrheit, die beim nächsten Versionssprung
 * still falsch würde.
 */
declare module "cytoscape-fcose" {
  import type cytoscape from "cytoscape";

  const erweiterung: cytoscape.Ext;
  export default erweiterung;
}
