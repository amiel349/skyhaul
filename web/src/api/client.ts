import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./types";

// The API client does not import the stores (they import it): the auth store registers
// how to read the token, how to refresh it and what to do when the session is over.
interface AuthHooks {
  getToken: () => string | null;
  /** One single-flight refresh of the session; true when a new access token is in place. */
  refresh: () => Promise<boolean>;
  onUnauthorized: () => void;
}

let hooks: AuthHooks = { getToken: () => null, refresh: async () => false, onUnauthorized: () => {} };

export function configureApi(next: AuthHooks): void {
  hooks = next;
}

// The calls that may need a retry, by request id: a copy of the request (its body is spent once
// sent) and the token it carried.
const inflight = new Map<string, { request: Request; token: string }>();

// /auth/* authenticates itself with the cookie and the CSRF header: never refreshed, never retried.
const isAuthCall = (request: Request): boolean => new URL(request.url).pathname.startsWith("/api/v1/auth/");

const authMiddleware: Middleware = {
  onRequest({ request, id }) {
    const token = hooks.getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    if (token && !isAuthCall(request)) inflight.set(id, { request: request.clone(), token });
    return request;
  },
  async onResponse({ id, response }) {
    const sent = inflight.get(id);
    inflight.delete(id);
    // No entry: a /auth/* call, or no token (a 401 then is a failed login, not an expired session).
    if (response.status !== 401 || !sent) return response;

    // A call that carried an older token than the store holds now has a newer one to use; otherwise refresh.
    if (hooks.getToken() === sent.token && !(await hooks.refresh())) {
      hooks.onUnauthorized();
      return response;
    }
    const fresh = hooks.getToken();
    if (!fresh) return response; // signed out while we waited
    sent.request.headers.set("Authorization", `Bearer ${fresh}`);
    const retried = await globalThis.fetch(sent.request);
    if (retried.status === 401) hooks.onUnauthorized();
    return retried;
  },
  onError({ id }) {
    inflight.delete(id);
  },
};

// Same origin everywhere (Vite proxy in dev, one ingress in production).
export const api = createClient<paths>({
  baseUrl: new URL("/api/v1", window.location.origin).toString(),
  // Resolve fetch per call, not at import time, so a test's network mock can take over.
  fetch: (request) => globalThis.fetch(request),
});
api.use(authMiddleware);
