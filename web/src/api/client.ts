import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./types";

// The API client does not import the stores (they import it): the auth store registers
// how to read the token and what to do on a 401.
interface AuthHooks {
  getToken: () => string | null;
  onUnauthorized: () => void;
}

let hooks: AuthHooks = { getToken: () => null, onUnauthorized: () => {} };

export function configureApi(next: AuthHooks): void {
  hooks = next;
}

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = hooks.getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ response }) {
    // A 401 without a token is a failed login, not an expired session.
    if (response.status === 401 && hooks.getToken()) hooks.onUnauthorized();
    return response;
  },
};

// Same origin everywhere (Vite proxy in dev, one ingress in production).
export const api = createClient<paths>({
  baseUrl: new URL("/api/v1", window.location.origin).toString(),
  // Resolve fetch per call, not at import time, so a test's network mock can take over.
  fetch: (request) => globalThis.fetch(request),
});
api.use(authMiddleware);
