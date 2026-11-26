import type { Drone } from "../../src/api/types";
import { makeLiveState } from "./live-state";

export function makeDrone(over: Partial<Drone> = {}): Drone {
  return {
    id: "d1",
    name: "Drone d1",
    home: { lat: 32, lon: 34.8 },
    created_at: "2026-01-01T00:00:00Z",
    state: makeLiveState(),
    ...over,
  };
}
