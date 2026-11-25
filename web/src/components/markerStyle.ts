import type { PathOptions } from "leaflet";
import type { FleetDrone } from "../stores/fleet";
import { token } from "../theme/theme";

export interface MarkerLook {
  path: PathOptions;
  radius: number;
  /** Text next to the marker; the LOST state is in words, not only in colour. */
  label: string;
  lost: boolean;
}

/** Colours are read from the CSS tokens each time, so a theme switch shows on the next call. */
export function markerLook(drone: FleetDrone, selected = false): MarkerLook {
  const lost = drone.state?.link === "LOST";
  const base: PathOptions = lost
    ? { color: token("--muted"), fillColor: token("--muted"), fillOpacity: 0.4, dashArray: "3 3", weight: 2 }
    : { color: token("--bg"), fillColor: token("--accent"), fillOpacity: 0.95, weight: 2 };
  return {
    lost,
    label: lost ? `${drone.name} (link lost)` : drone.name,
    radius: selected ? 11 : 8,
    // Selected: a thick ink ring and a bigger dot, visible in both themes.
    path: selected ? { ...base, color: token("--ink"), weight: 4, dashArray: undefined } : base,
  };
}

export type BaseKind = "land" | "coast" | "road";

/** Style of a base-layer feature, from the tokens. Land is a fill; coast and roads are lines. */
export function baseStyle(kind: string | undefined): PathOptions {
  switch (kind) {
    case "land":
      return { stroke: false, fill: true, fillColor: token("--land"), fillOpacity: 1 };
    case "coast":
      return { color: token("--coast"), weight: 2, fill: false };
    default:
      return { color: token("--road"), weight: 2, fill: false };
  }
}
