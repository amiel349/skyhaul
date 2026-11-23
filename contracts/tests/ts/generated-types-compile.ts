// Issue #6: "Given the generated TS types, when the web typecheck runs, then they compile."
// web/tsconfig.json includes this file, so `pnpm typecheck` (vue-tsc) compiles it together
// with contracts/ts through the @skyhaul/contracts alias. The @ts-expect-error lines fail the
// typecheck if the types stop rejecting wrong shapes.

import type {
  AlertCode,
  CommandType,
  FlightState,
  GotoPayload,
  Message,
  MessageType,
  NackReason,
  PayloadByType,
} from "@skyhaul/contracts";

const goto: Message<"GOTO"> = {
  msg_id: "7a1c3e5f-2b4d-4c6e-8f10-000000000001",
  correlation_id: null,
  drone_id: "gw0-d001",
  type: "GOTO",
  schema_version: 1,
  ts: "2026-11-23T10:00:00.123Z",
  payload: { lat: 52.2297, lon: 21.0122, altitude_m: 40 },
};

// The Message union narrows on `type`.
export function altitudeOf(msg: Message): number | null {
  switch (msg.type) {
    case "POSITION":
      return msg.payload.altitude_m;
    case "TAKEOFF":
      return msg.payload.altitude_m;
    default:
      return null;
  }
}

const speed: GotoPayload["speed_mps"] = undefined;
const state: FlightState = "HOVERING";
const reason: NackReason = "INJECTED";
const code: AlertCode = "LINK_RESTORED";
const command: CommandType = "START_MISSION";
const everyType: Record<MessageType, true> = {
  ARM: true, TAKEOFF: true, GOTO: true, RETURN_HOME: true, LAND: true, SET_SPEED: true,
  START_MISSION: true, ACK: true, NACK: true, HEARTBEAT: true, BATTERY: true, POSITION: true,
  MISSION_STATUS: true, ALERT: true,
};
// PayloadByType has exactly one entry per MessageType (both directions are checked).
const payloadKeys: Record<keyof PayloadByType, true> = everyType;

// @ts-expect-error unknown enum value
const badState: FlightState = "hovering";
// @ts-expect-error a GOTO payload needs lat, lon and altitude_m
const badGoto: Message<"GOTO"> = { ...goto, payload: { lat: 1 } };
// @ts-expect-error the payload type follows the message type
const mismatched: Message<"ACK"> = { ...goto, type: "ACK" };
// @ts-expect-error schema_version is 1 in v1
const badVersion: Message<"GOTO"> = { ...goto, schema_version: 2 };

export const samples = [
  goto, speed, state, reason, code, command, payloadKeys, badState, badGoto, mismatched, badVersion,
];
