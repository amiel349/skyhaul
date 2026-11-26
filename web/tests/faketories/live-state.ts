import type { DroneLiveState } from "../../src/api/types";

export const liveStateFaketory = {
  build(over: Partial<DroneLiveState> = {}): DroneLiveState {
    return {
      flight_state: "FLYING",
      armed: true,
      battery_pct: 80,
      est_remaining_s: 600,
      position: { lat: 32.0, lon: 34.8, altitude_m: 20, heading_deg: 0, ground_speed_mps: 5 },
      target: null,
      mission_id: null,
      link: "UP",
      last_heartbeat_at: null,
      last_seen_at: null,
      ts: "2026-01-01T00:00:10.000Z",
      ...over,
    };
  },
};
