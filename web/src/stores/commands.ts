import { defineStore } from "pinia";
import { shallowReactive } from "vue";
import { api } from "../api/client";
import type { ApiError, Command, CommandStatus, DirectCommandType } from "../api/types";
import type { Payload } from "../commands/payload";

export type SendResult =
  | { ok: true; command: Command }
  | {
      ok: false;
      kind: "validation" | "rate_limited" | "not_sent" | "rejected" | "unreachable";
      message: string;
      fields?: Record<string, string[]>;
      retryAfterS?: number;
    };

/**
 * How far a command got. A later reply (ACKED/NACKED) outranks TIMED_OUT; a status never goes
 * back to a lower rank, so a late or repeated event cannot undo a newer one.
 */
const RANK: Record<CommandStatus, number> = {
  PENDING: 0,
  SENT: 1,
  TIMED_OUT: 2,
  ACKED: 3,
  NACKED: 3,
  FAILED: 3,
};

function newestFirst(a: Command, b: Command): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

export const useCommandsStore = defineStore("commands", () => {
  const byDrone = shallowReactive(new Map<string, Command[]>());
  const loadError = shallowReactive(new Map<string, string>());
  /** Bumped by reset(): an answer that was in flight at sign-out is dropped. */
  let epoch = 0;
  /** The send attempt that has not been answered for good yet; a retry of it reuses the key. */
  let attempt: { fingerprint: string; key: string } | null = null;

  const forDrone = (droneId: string): Command[] => byDrone.get(droneId) ?? [];

  /** Insert or merge by id. An older status, or a duplicate, changes nothing. */
  function applyUpdate(incoming: Command): void {
    const rows = forDrone(incoming.drone_id);
    const known = rows.find((row) => row.id === incoming.id);
    if (known) {
      if (RANK[incoming.status] < RANK[known.status]) return;
      if (known.status === incoming.status && known.late === incoming.late) return;
      byDrone.set(
        incoming.drone_id,
        rows.map((row) => (row.id === incoming.id ? incoming : row)),
      );
      return;
    }
    byDrone.set(incoming.drone_id, [...rows, incoming].sort(newestFirst));
  }

  async function load(droneId: string): Promise<void> {
    const started = epoch;
    try {
      const { data, response } = await api.GET("/drones/{drone_id}/commands", {
        params: { path: { drone_id: droneId } },
      });
      if (started !== epoch) return;
      if (!data) {
        loadError.set(droneId, response.status === 404 ? "Drone not found" : "Could not load the commands");
        return;
      }
      loadError.delete(droneId);
      for (const item of data.items) applyUpdate(item);
    } catch {
      if (started === epoch) loadError.set(droneId, "Could not load the commands");
    }
  }

  async function send(droneId: string, type: DirectCommandType, payload: Payload): Promise<SendResult> {
    const started = epoch;
    const fingerprint = JSON.stringify([droneId, type, payload]);
    if (attempt?.fingerprint !== fingerprint) attempt = { fingerprint, key: crypto.randomUUID() };
    const key = attempt.key;
    try {
      const { data, error, response } = await api.POST("/drones/{drone_id}/commands", {
        params: { path: { drone_id: droneId }, header: { "Idempotency-Key": key } },
        // The generated type says Record<string, never>; the real shape is the per-type payload.
        body: { type, payload: payload as Record<string, never> },
      });
      if (data) {
        attempt = null;
        if (started === epoch) applyUpdate(data);
        return { ok: true, command: data };
      }
      const body = error as ApiError | undefined;
      const detail = body?.detail ?? "Could not send the command";
      if (response.status === 422) {
        attempt = null;
        return { ok: false, kind: "validation", message: detail, fields: body?.fields };
      }
      if (response.status === 429) {
        const retry = Number(response.headers.get("Retry-After"));
        return {
          ok: false,
          kind: "rate_limited",
          message: "Too many commands, wait",
          retryAfterS: Number.isFinite(retry) && retry > 0 ? retry : undefined,
        };
      }
      if (response.status === 503) return { ok: false, kind: "not_sent", message: detail };
      attempt = null; // 404, 409 and the like: the next send is a new attempt
      return { ok: false, kind: "rejected", message: detail };
    } catch {
      return { ok: false, kind: "unreachable", message: "Could not reach the server" };
    }
  }

  function reset(): void {
    epoch += 1;
    byDrone.clear();
    loadError.clear();
    attempt = null;
  }

  return { byDrone, loadError, forDrone, applyUpdate, load, send, reset };
});
