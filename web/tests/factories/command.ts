import type { Command } from "../../src/api/types";

export function makeCommand(over: Partial<Command> = {}): Command {
  return {
    id: "00000000-0000-4000-8000-0000000000c1",
    drone_id: "d1",
    mission_id: null,
    type: "ARM",
    payload: {},
    correlation_id: "00000000-0000-4000-8000-0000000000e1",
    status: "SENT",
    nack_reason: null,
    nack_detail: null,
    late: false,
    requested_by: "00000000-0000-4000-8000-000000000001",
    created_at: "2026-01-01T00:00:00Z",
    sent_at: "2026-01-01T00:00:00Z",
    replied_at: null,
    ...over,
  };
}
