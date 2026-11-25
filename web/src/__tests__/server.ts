import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { Drone, DroneLiveState, DroneStateEvent } from "../api/types";

export const server = setupServer();
export const url = (path: string): string => `http://localhost:3000/api/v1${path}`;

export function liveState(over: Partial<DroneLiveState> = {}): DroneLiveState {
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
}

export function drone(id: string, state: DroneLiveState | null = liveState()): Drone {
  return { id, name: `Name ${id}`, home: { lat: 32, lon: 34.8 }, created_at: "2026-01-01T00:00:00Z", state };
}

export function event(id: string, state: DroneLiveState): DroneStateEvent {
  return { drone_id: id, state };
}

export function listDrones(items: Drone[]): ReturnType<typeof http.get> {
  return http.get(url("/drones"), () => HttpResponse.json({ items, next_cursor: null }));
}

export const loginOk = http.post(url("/auth/login"), () =>
  HttpResponse.json({
    access_token: "tok-1",
    token_type: "Bearer",
    expires_in: 900,
    user: {
      id: "00000000-0000-4000-8000-000000000001",
      email: "a@skyhaul.dev",
      role: "admin",
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
    },
  }),
);
