/**
 * Ein Knotenprogramm für Rauten — der `personal`-Store in seiner eigenen Form (§17.2).
 *
 * sigma bringt von Haus aus nur Kreise mit. Statt eines kompletten eigenen WebGL-Programms
 * wird hier das Kreisprogramm abgeleitet und **eine** Zeile seines Fragment-Shaders getauscht:
 * die Abstandsfunktion. `length(v)` zeichnet einen Kreis (euklidischer Abstand); die
 * Betragssumme `|x| + |y|` zeichnet eine auf der Spitze stehende Raute (Manhattan-Abstand).
 * Der Faktor √2 gleicht die Diagonale aus, damit die Raute denselben Umriss füllt wie der
 * Kreis gleicher Größe.
 *
 * Ein Test wacht darüber, dass der Tausch greift — bricht eine sigma-Version die Shader-Zeile,
 * fällt der Test und nicht die Kodierung.
 */

import { NodeCircleProgram } from "sigma/rendering";

const KREIS_ABSTAND = "float dist = length(v_diffVector) - v_radius + border;";
const RAUTEN_ABSTAND =
  "float dist = (abs(v_diffVector.x) + abs(v_diffVector.y)) * 0.7071 - v_radius + border;";

/** Ob die erwartete Shader-Zeile existiert — für den Test, der die sigma-Version bewacht. */
export function shaderTauschMoeglich(): boolean {
  const definition = NodeCircleProgram.prototype.getDefinition.call(
    Object.create(NodeCircleProgram.prototype) as NodeCircleProgram,
  );
  return definition.FRAGMENT_SHADER_SOURCE.includes(KREIS_ABSTAND);
}

export class NodeRautenProgram extends NodeCircleProgram {
  getDefinition(): ReturnType<NodeCircleProgram["getDefinition"]> {
    const definition = super.getDefinition();
    return {
      ...definition,
      FRAGMENT_SHADER_SOURCE: definition.FRAGMENT_SHADER_SOURCE.replace(
        KREIS_ABSTAND,
        RAUTEN_ABSTAND,
      ),
    };
  }
}
