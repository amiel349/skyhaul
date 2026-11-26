// Fake db and endpoints for this component only: the drones it reads through the fleet store.
import { http, HttpResponse } from "msw";
import type { Drone } from "../api/types";
import { apiUrl, type Ok } from "../../tests/api";
import { registerReset } from "../../tests/reset";

export const fakeDb = {
  drones: new Map<string, Drone>(),
  add(...drones: Drone[]): void {
    for (const drone of drones) this.drones.set(drone.id, drone);
  },
  reset(): void {
    this.drones.clear();
  },
};
registerReset(() => fakeDb.reset());

export const fakeEndpoints = [
  http.get(apiUrl("/drones"), () =>
    HttpResponse.json<Ok<"/drones", "get">>({ items: [...fakeDb.drones.values()], next_cursor: null }),
  ),
  http.get(apiUrl("/drones/:id"), ({ params }) => {
    const drone = fakeDb.drones.get(String(params.id));
    if (!drone) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json<Ok<"/drones/{drone_id}", "get">>(drone);
  }),
];
