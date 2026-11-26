import type { DroneStateEvent } from "../../src/api/types";
import { liveStateFaketory } from "./live-state";

export const droneStateEventFaketory = {
  build(over: Partial<DroneStateEvent> = {}): DroneStateEvent {
    return { drone_id: "d1", state: liveStateFaketory.build(), ...over };
  },
};
