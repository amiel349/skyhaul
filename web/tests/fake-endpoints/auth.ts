// Fake endpoints for auth: POST /auth/login, /auth/refresh and /auth/logout, over the fake user and session tables.
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
      fakeDb.sessions.start(user);
      return HttpResponse.json<Ok<"/auth/login", "post">>({
        access_token: "tok-1",
        token_type: "Bearer",
        expires_in: 900,
        user,
        csrf_token: fakeDb.sessions.csrf,
      });
    }),
    // A browser with no valid cookie gets 401 session_invalid; with one, a rotated cookie and a new token.
    http.post(apiUrl("/auth/refresh"), ({ request }) => {
      const csrf = request.headers.get("X-CSRF-Token");
      fakeDb.sessions.refreshRequests.push({ csrf });
      const { user } = fakeDb.sessions;
      if (!user) return HttpResponse.json({ code: "session_invalid", message: "Session is not valid" }, { status: 401 });
      if (csrf !== fakeDb.sessions.csrf) return HttpResponse.json({ code: "csrf_failed", message: "CSRF check failed" }, { status: 403 });
      const next = fakeDb.sessions.rotate();
      return HttpResponse.json<Ok<"/auth/refresh", "post">>({
        access_token: next.accessToken,
        token_type: "Bearer",
        expires_in: 900,
        user,
        csrf_token: next.csrf,
      });
    }),
    http.post(apiUrl("/auth/logout"), ({ request }) => {
      fakeDb.sessions.logoutRequests.push({ csrf: request.headers.get("X-CSRF-Token") });
      fakeDb.sessions.end();
      return new HttpResponse(null, { status: 204 });
    }),
  ],
  /** Override: every login answers `status` (429 takes a Retry-After in seconds). Use with `worker.use(...)`. */
  respondWith(status: number, options: { retryAfter?: number } = {}) {
    const headers = options.retryAfter === undefined ? undefined : { "Retry-After": String(options.retryAfter) };
    return http.post(apiUrl("/auth/login"), () => new HttpResponse(null, { status, headers }));
  },
  /** Override: every refresh answers `status` (409 is refresh_superseded). Records the request like the happy path. */
  refreshRespondWith(status: number) {
    return http.post(apiUrl("/auth/refresh"), ({ request }) => {
      fakeDb.sessions.refreshRequests.push({ csrf: request.headers.get("X-CSRF-Token") });
      return HttpResponse.json({ code: "refresh_failed", message: "Refresh refused" }, { status });
    });
  },
  /** Override: the first refresh answers 409 refresh_superseded, then the happy path takes over. */
  refreshSupersededOnce() {
    let first = true;
    return http.post(apiUrl("/auth/refresh"), ({ request }) => {
      if (!first) return; // falls through to the happy path
      first = false;
      fakeDb.sessions.refreshRequests.push({ csrf: request.headers.get("X-CSRF-Token") });
      return HttpResponse.json({ code: "refresh_superseded", message: "Retry" }, { status: 409 });
    });
  },
  /**
   * Override: `GET path` answers 401 for a request that carries `Bearer <token>` (an expired token),
   * or for every request when no token is given. Other requests fall through to the happy path.
   */
  rejectToken(path: string, token?: string) {
    return http.get(apiUrl(path), ({ request }) => {
      if (token === undefined || request.headers.get("Authorization") === `Bearer ${token}`) {
        return new HttpResponse(null, { status: 401 });
      }
    });
  },
};
