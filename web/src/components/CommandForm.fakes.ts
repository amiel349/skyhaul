// Fake db and endpoints for CommandForm only: POST /drones/{id}/commands, with switches for the answer.
import { http, HttpResponse } from "msw";
import type { ApiError, Command } from "../api/types";
import { apiUrl, type Body } from "../testing/api";
import { makeCommand } from "../testing/factories/command";
import { registerReset } from "../testing/reset";

type Mode = "202" | "422" | "429" | "503" | "409";

export interface SentRequest {
  droneId: string;
  idempotencyKey: string | null;
  body: Body<"/drones/{drone_id}/commands", "post">;
}

export const fakeDb = {
  /** What the next POST answers with. */
  mode: "202" as Mode,
  /** The status of the command a 202 returns. */
  status: "SENT" as Command["status"],
  /** While set, the POST waits for it: lets a test look at the form while a request is in flight. */
  gate: null as Promise<void> | null,
  requests: [] as SentRequest[],
  reset(): void {
    this.mode = "202";
    this.status = "SENT";
    this.gate = null;
    this.requests = [];
  },
};
registerReset(() => fakeDb.reset());

const error = (code: string, detail: string, fields?: ApiError["fields"]): ApiError => ({
  error: code,
  detail,
  fields,
  request_id: "00000000-0000-4000-8000-0000000000f1",
});

export const fakeEndpoints = [
  http.post(apiUrl("/drones/:id/commands"), async ({ params, request }) => {
    const body = (await request.json()) as SentRequest["body"];
    const droneId = String(params.id);
    fakeDb.requests.push({ droneId, idempotencyKey: request.headers.get("Idempotency-Key"), body });
    if (fakeDb.gate) await fakeDb.gate;
    switch (fakeDb.mode) {
      case "422":
        return HttpResponse.json(
          error("validation_failed", "Invalid payload", { "payload.altitude_m": ["Too high for this drone"] }),
          { status: 422 },
        );
      case "429":
        return HttpResponse.json(error("rate_limited", "Slow down"), { status: 429, headers: { "Retry-After": "12" } });
      case "503":
        return HttpResponse.json(error("command_not_sent", "The broker did not confirm"), { status: 503 });
      case "409":
        return HttpResponse.json(error("drone_busy", "Drone is on a mission"), { status: 409 });
      default:
        return HttpResponse.json(
          makeCommand({ id: `cmd-${fakeDb.requests.length}`, drone_id: droneId, type: body.type, status: fakeDb.status }),
          { status: 202 },
        );
    }
  }),
];
