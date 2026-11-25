import type { DroneStateEvent } from "../../api/types";
import { makeLiveState } from "./live-state";

export function makeDroneStateEvent(over: Partial<DroneStateEvent> = {}): DroneStateEvent {
  return { drone_id: "d1", state: makeLiveState(), ...over };
}
