// Fake db and endpoint for this view only: the drones it loads on mount through GET /drones.
import { http, HttpResponse } from "msw";
import type { Drone } from "../api/types";
import { apiUrl, type Ok } from "../testing/api";
import { registerReset } from "../testing/reset";

export const fakeDb = {
  drones: new Map<string, Drone>(),
  /** Set to make GET /drones answer 500. */
  failing: false,
  add(...drones: Drone[]): void {
    for (const drone of drones) this.drones.set(drone.id, drone);
  },
  reset(): void {
    this.drones.clear();
    this.failing = false;
  },
};
registerReset(() => fakeDb.reset());

export const fakeEndpoints = [
  http.get(apiUrl("/drones"), () => {
    if (fakeDb.failing) return new HttpResponse(null, { status: 500 });
    return HttpResponse.json<Ok<"/drones", "get">>({ items: [...fakeDb.drones.values()], next_cursor: null });
  }),
];
