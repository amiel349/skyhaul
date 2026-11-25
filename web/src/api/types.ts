// No hand-written API types: everything comes from the generated OpenAPI types.
import type { components, paths } from "@contracts/api/fleet-api.v1";

export type { components, paths };
export type Drone = components["schemas"]["Drone"];
export type DroneLiveState = components["schemas"]["DroneLiveState"];
export type DroneStateEvent = components["schemas"]["DroneStateEvent"];
export type User = components["schemas"]["User"];
export type SocketConnectError = components["schemas"]["SocketConnectError"];
