// Fake db and endpoints for CommandList only: GET /drones/{id}/commands.
import { http, HttpResponse } from "msw";
import type { Command } from "../api/types";
import { apiUrl, type Ok } from "../testing/api";
import { registerReset } from "../testing/reset";

export const fakeDb = {
  commands: [] as Command[],
  /** Set to make the GET answer 500. */
  failing: false,
  requested: [] as string[],
  add(...commands: Command[]): void {
    this.commands.push(...commands);
  },
  reset(): void {
    this.commands = [];
    this.failing = false;
    this.requested = [];
  },
};
registerReset(() => fakeDb.reset());

export const fakeEndpoints = [
  http.get(apiUrl("/drones/:id/commands"), ({ params }) => {
    const droneId = String(params.id);
    fakeDb.requested.push(droneId);
    if (fakeDb.failing) return new HttpResponse(null, { status: 500 });
    return HttpResponse.json<Ok<"/drones/{drone_id}/commands", "get">>({
      items: fakeDb.commands.filter((c) => c.drone_id === droneId),
      next_cursor: null,
    });
  }),
];
