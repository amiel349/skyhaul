import type { PathOptions } from "leaflet";
import type { FleetDrone } from "../stores/fleet";

export interface MarkerLook {
  path: PathOptions;
  /** Text next to the marker; the LOST state is in words, not only in colour. */
  label: string;
  lost: boolean;
}

export function markerLook(drone: FleetDrone): MarkerLook {
  const lost = drone.state?.link === "LOST";
  return {
    lost,
    label: lost ? `${drone.name} (link lost)` : drone.name,
    path: lost
      ? { color: "#6b7280", fillColor: "#d1d5db", fillOpacity: 0.5, dashArray: "3 3", weight: 2 }
      : { color: "#1d4ed8", fillColor: "#3b82f6", fillOpacity: 0.9, weight: 2 },
  };
}
