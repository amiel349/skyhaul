// No hand-written API types: everything comes from the generated OpenAPI types.
import type { components, paths } from "@contracts/api/fleet-api.v1";

export type { components, paths };
export type Drone = components["schemas"]["Drone"];
export type DroneLiveState = components["schemas"]["DroneLiveState"];
export type DroneStateEvent = components["schemas"]["DroneStateEvent"];
export type User = components["schemas"]["User"];
export type SocketConnectError = components["schemas"]["SocketConnectError"];
export type Command = components["schemas"]["Command"];
export type CommandStatus = components["schemas"]["CommandStatus"];
export type DirectCommandType = components["schemas"]["DirectCommandType"];
export type CommandUpdatedEvent = components["schemas"]["CommandUpdatedEvent"];
export type ApiError = components["schemas"]["Error"];
export type Alert = components["schemas"]["Alert"];
export type AlertSeverity = components["schemas"]["AlertSeverity"];
export type AlertRaisedEvent = components["schemas"]["AlertRaisedEvent"];
export type AlertUpdatedEvent = components["schemas"]["AlertUpdatedEvent"];
