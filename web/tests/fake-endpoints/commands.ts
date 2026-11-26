// Fake endpoints for commands: GET and POST /drones/{id}/commands, served from the fake command table.
import { http, HttpResponse } from "msw";
import type { ApiError } from "../../src/api/types";
import { apiUrl, type Body, type Ok } from "../api";
import { fakeDb } from "../fake-db";

export interface RespondWithOptions {
  /** Retry-After header, in seconds (429). */
  retryAfter?: number;
  /** The error `detail`. */
  detail?: string;
  /** Per-field validation errors (422). */
  fields?: ApiError["fields"];
  /** While set, the POST waits for it before answering: lets a test look at the UI while a request is in flight. */
  gate?: Promise<void>;
}

const errorCodes: Record<number, string> = {
  409: "drone_busy",
  422: "validation_failed",
  429: "rate_limited",
  503: "command_not_sent",
};

function post(status: number, options: RespondWithOptions) {
  return http.post(apiUrl("/drones/:id/commands"), async ({ params, request }) => {
    const body = (await request.json()) as Body<"/drones/{drone_id}/commands", "post">;
    const droneId = String(params.id);
    fakeDb.commands.requests.push({ droneId, idempotencyKey: request.headers.get("Idempotency-Key"), body });
    if (options.gate) await options.gate;
    if (status === 202) {
      return HttpResponse.json<Ok<"/drones/{drone_id}/commands", "post", 202>>(
        fakeDb.commands.create(droneId, body),
        { status: 202 },
      );
    }
    const error: ApiError = {
      error: errorCodes[status] ?? "error",
      detail: options.detail ?? "Error",
      fields: options.fields,
      request_id: "00000000-0000-4000-8000-0000000000f1",
    };
    const headers = options.retryAfter === undefined ? undefined : { "Retry-After": String(options.retryAfter) };
    return HttpResponse.json(error, { status, headers });
  });
}

export const commandEndpoints = {
  /** Registered once for every test by the browser setup. */
  happyPath: [
    http.get(apiUrl("/drones/:id/commands"), ({ params }) => {
      const droneId = String(params.id);
      fakeDb.commands.listed.push(droneId);
      return HttpResponse.json<Ok<"/drones/{drone_id}/commands", "get">>({
        items: fakeDb.commands.forDrone(droneId),
        next_cursor: null,
      });
    }),
    post(202, {}),
  ],
  /** Override: POST answers `status` (202 stores a SENT command). Use with `worker.use(...)`. */
  respondWith(status: number, options: RespondWithOptions = {}) {
    return post(status, options);
  },
  /** Override: GET of a command history answers `status` with no body. */
  listRespondWith(status: number) {
    return http.get(apiUrl("/drones/:id/commands"), () => new HttpResponse(null, { status }));
  },
};
