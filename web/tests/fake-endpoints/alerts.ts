// Fake endpoints for alerts: GET /alerts (honours `open` and `drone_id`) and POST /alerts/{id}/ack.
import { http, HttpResponse } from "msw";
import type { ApiError } from "../../src/api/types";
import { apiUrl, type Ok } from "../api";
import { fakeDb } from "../fake-db";

const errorCodes: Record<number, string> = { 403: "forbidden", 404: "not_found", 409: "already_acknowledged" };

export const alertEndpoints = {
  /** Registered once for every test by the browser setup. */
  happyPath: [
    http.get(apiUrl("/alerts"), ({ request }) => {
      const query = new URL(request.url).searchParams;
      fakeDb.alerts.listed.push(query);
      const open = query.get("open");
      const droneId = query.get("drone_id");
      const items = fakeDb.alerts.alerts.filter(
        (a) =>
          (droneId === null || a.drone_id === droneId) &&
          (open === null || (open === "true") === (a.cleared_at === null)),
      );
      return HttpResponse.json<Ok<"/alerts", "get">>({ items, next_cursor: null });
    }),
    http.post(apiUrl("/alerts/:id/ack"), ({ params }) => {
      const id = String(params.id);
      fakeDb.alerts.acked.push(id);
      const alert = fakeDb.alerts.acknowledge(id);
      if (!alert) return new HttpResponse(null, { status: 404 });
      return HttpResponse.json<Ok<"/alerts/{alert_id}/ack", "post">>(alert);
    }),
  ],
  /**
   * Override: POST ack answers `status` with an error body. For 409 the alert is first acknowledged by
   * someone else, as in the real race. Use with `worker.use(...)`.
   */
  respondWith(status: number, options: { acknowledgedBy?: string } = {}) {
    return http.post(apiUrl("/alerts/:id/ack"), ({ params }) => {
      const id = String(params.id);
      fakeDb.alerts.acked.push(id);
      if (status === 409) fakeDb.alerts.acknowledge(id, options.acknowledgedBy ?? "00000000-0000-4000-8000-000000000002");
      const error: ApiError = {
        error: errorCodes[status] ?? "error",
        detail: "Error",
        request_id: "00000000-0000-4000-8000-0000000000f1",
      };
      return HttpResponse.json(error, { status });
    });
  },
};
