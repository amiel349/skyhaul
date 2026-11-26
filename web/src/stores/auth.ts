import { defineStore } from "pinia";
import { ref } from "vue";
import { api, configureApi } from "../api/client";
import type { User } from "../api/types";
import { closeFleetSocket, reconnectFleetSocket } from "../realtime/socket";
import { useAlertsStore } from "./alerts";
import { useCommandsStore } from "./commands";
import { useConnectionStore } from "./connection";
import { useFleetStore } from "./fleet";
import { useFleetFilterStore } from "./fleetFilter";
import { useMapPickStore } from "./mapPick";
import { useSelectionStore } from "./selection";

export type LoginResult =
  | { ok: true }
  | { ok: false; reason: "invalid" | "rate_limited" | "unreachable"; retryAfterS?: number };

type RefreshOutcome = "ok" | "denied" | "unreachable";

const REFRESH_LEAD_S = 60;
const CSRF_COOKIE = "skyhaul_csrf";

/** The CSRF token the server also sets in a readable cookie, so a reload can still send it (ADR 0018). */
function readCsrfCookie(): string | null {
  const pair = document.cookie.split("; ").find((part) => part.startsWith(`${CSRF_COOKIE}=`));
  return pair ? decodeURIComponent(pair.slice(CSRF_COOKIE.length + 1)) : null;
}

// The access token lives here, in memory, and nowhere else (ADR 0015). The refresh token is an
// httpOnly cookie this code never sees; only the CSRF token is kept (also in memory).
export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(null);
  const user = ref<User | null>(null);
  const csrfToken = ref<string | null>(null);
  const notice = ref<"expired" | "unreachable" | null>(null);
  let refreshTimer: ReturnType<typeof setTimeout> | undefined;
  let inflight: Promise<RefreshOutcome> | null = null;
  let restoring: Promise<void> | null = null;
  // Bumped by logout: a refresh that finishes after it must not sign the user back in.
  let generation = 0;

  function startSession(data: { access_token: string; expires_in: number; csrf_token: string; user: User }): void {
    token.value = data.access_token;
    user.value = data.user;
    csrfToken.value = data.csrf_token;
    notice.value = null;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => void refreshBeforeExpiry(), Math.max(0, data.expires_in - REFRESH_LEAD_S) * 1000);
  }

  async function refreshBeforeExpiry(): Promise<void> {
    if ((await refresh()) !== "ok") logout("expired");
  }

  async function requestRefresh(): Promise<RefreshOutcome> {
    const started = generation;
    // Two tries at most: a 409 refresh_superseded means another request rotated the cookie first.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        // The cookie is the freshest copy: another tab may have rotated since this tab last refreshed.
        const csrf = readCsrfCookie() ?? csrfToken.value ?? "";
        const { data, response } = await api.POST("/auth/refresh", { params: { header: { "X-CSRF-Token": csrf } } });
        if (started !== generation) return "denied";
        if (data) {
          startSession(data);
          reconnectFleetSocket(); // the socket's session ends with the token it connected with
          return "ok";
        }
        if (response.status !== 409) return "denied";
      } catch {
        return "unreachable";
      }
    }
    return "denied";
  }

  /** Single-flight: callers at the same time share one request; other tabs wait on a Web Lock. */
  function refresh(): Promise<RefreshOutcome> {
    inflight ??= (async () => {
      try {
        return navigator.locks
          ? await navigator.locks.request("skyhaul-refresh", requestRefresh)
          : await requestRefresh();
      } finally {
        inflight = null;
      }
    })();
    return inflight;
  }

  /** Once, before the router guard decides: turn the refresh cookie of a reload into a session. */
  function restore(): Promise<void> {
    restoring ??= (async () => {
      if (token.value) return;
      if ((await refresh()) === "unreachable") notice.value = "unreachable";
    })();
    return restoring;
  }

  async function login(email: string, password: string): Promise<LoginResult> {
    try {
      const { data, response } = await api.POST("/auth/login", { body: { email, password } });
      if (data) {
        startSession(data);
        return { ok: true };
      }
      if (response.status === 429) {
        const retry = Number(response.headers.get("Retry-After"));
        return {
          ok: false,
          reason: "rate_limited",
          retryAfterS: Number.isFinite(retry) ? retry : undefined,
        };
      }
      return { ok: false, reason: "invalid" };
    } catch {
      return { ok: false, reason: "unreachable" };
    }
  }

  /** Clear every store. `signOut` is the user's logout; this alone is what a dead session needs. */
  function logout(reason?: "expired"): void {
    generation++;
    clearTimeout(refreshTimer);
    closeFleetSocket();
    token.value = null;
    user.value = null;
    csrfToken.value = null;
    notice.value = reason ?? null;
    useFleetStore().clear();
    useSelectionStore().reset();
    useMapPickStore().reset();
    useFleetFilterStore().reset();
    useCommandsStore().reset();
    useAlertsStore().reset();
    useConnectionStore().set("offline");
  }

  /** The user's logout: end the session on the server (revokes the refresh cookie), then clear the stores. */
  async function signOut(): Promise<void> {
    try {
      await api.POST("/auth/logout", { params: { header: { "X-CSRF-Token": readCsrfCookie() ?? csrfToken.value ?? "" } } });
    } catch {
      // The server cannot be reached: still leave. The refresh cookie dies on its own after 7 days.
    }
    logout();
  }

  configureApi({
    getToken: () => token.value,
    refresh: async () => (await refresh()) === "ok",
    onUnauthorized: () => logout("expired"),
  });

  return { token, user, notice, login, logout, signOut, refresh, restore };
});
