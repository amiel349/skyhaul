// Fake API for the jsdom unit tests (stores): MSW in Node, typed from the generated API types.
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { Drone } from "../api/types";
import type { Ok } from "./api";
import { makeUser } from "./factories/user";

export const server = setupServer();
export const url = (path: string): string => `http://localhost:3000/api/v1${path}`;

export function listDrones(items: Drone[]): ReturnType<typeof http.get> {
  return http.get(url("/drones"), () => HttpResponse.json<Ok<"/drones", "get">>({ items, next_cursor: null }));
}

export const loginOk = http.post(url("/auth/login"), () =>
  HttpResponse.json<Ok<"/auth/login", "post">>({
    access_token: "tok-1",
    token_type: "Bearer",
    expires_in: 900,
    user: makeUser(),
  }),
);
