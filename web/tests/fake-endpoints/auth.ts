// Fake endpoint for auth: POST /auth/login, checked against the fake user table.
import { http, HttpResponse } from "msw";
import { apiUrl, type Body, type Ok } from "../api";
import { fakeDb } from "../fake-db";

export const authEndpoints = {
  /** Registered once for every test by the browser setup. */
  happyPath: [
    http.post(apiUrl("/auth/login"), async ({ request }) => {
      const body = (await request.json()) as Body<"/auth/login", "post">;
      const user = fakeDb.users.find(body.email, body.password);
      if (!user) return new HttpResponse(null, { status: 401 });
      return HttpResponse.json<Ok<"/auth/login", "post">>({
        access_token: "tok-1",
        token_type: "Bearer",
        expires_in: 900,
        user,
        csrf_token: "c".repeat(43),
      });
    }),
  ],
  /** Override: every login answers `status` (429 takes a Retry-After in seconds). Use with `worker.use(...)`. */
  respondWith(status: number, options: { retryAfter?: number } = {}) {
    const headers = options.retryAfter === undefined ? undefined : { "Retry-After": String(options.retryAfter) };
    return http.post(apiUrl("/auth/login"), () => new HttpResponse(null, { status, headers }));
  },
};
