// Fake endpoints for drones: GET /drones and GET /drones/{id}, served from the fake drone table.
import { http, HttpResponse } from "msw";
import { apiUrl, type Ok } from "../api";
import { fakeDb } from "../fake-db";

export const droneEndpoints = {
  /** Registered once for every test by the browser setup. */
  happyPath: [
    http.get(apiUrl("/drones"), () =>
      HttpResponse.json<Ok<"/drones", "get">>({ items: fakeDb.drones.list(), next_cursor: null }),
    ),
    http.get(apiUrl("/drones/:id"), ({ params }) => {
      const drone = fakeDb.drones.get(String(params.id));
      if (!drone) return new HttpResponse(null, { status: 404 });
      return HttpResponse.json<Ok<"/drones/{drone_id}", "get">>(drone);
    }),
  ],
  /** Override: GET /drones answers `status` with no body. Use with `worker.use(...)`. */
  respondWith(status: number) {
    return http.get(apiUrl("/drones"), () => new HttpResponse(null, { status }));
  },
};
