import { defineStore } from "pinia";
import { ref } from "vue";
import { api, configureApi } from "../api/client";
import type { User } from "../api/types";
import { closeFleetSocket } from "../realtime/socket";
import { useCommandsStore } from "./commands";
import { useConnectionStore } from "./connection";
import { useFleetStore } from "./fleet";
import { useFleetFilterStore } from "./fleetFilter";
import { useSelectionStore } from "./selection";

export type LoginResult =
  | { ok: true }
  | { ok: false; reason: "invalid" | "rate_limited" | "unreachable"; retryAfterS?: number };

// The access token lives here, in memory, and nowhere else (ADR 0015).
export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(null);
  const user = ref<User | null>(null);
  const notice = ref<"expired" | null>(null);

  async function login(email: string, password: string): Promise<LoginResult> {
    try {
      const { data, response } = await api.POST("/auth/login", { body: { email, password } });
      if (data) {
        token.value = data.access_token;
        user.value = data.user;
        notice.value = null;
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

  function logout(reason?: "expired"): void {
    closeFleetSocket();
    token.value = null;
    user.value = null;
    notice.value = reason ?? null;
    useFleetStore().clear();
    useSelectionStore().reset();
    useFleetFilterStore().reset();
    useCommandsStore().reset();
    useConnectionStore().set("offline");
  }

  configureApi({ getToken: () => token.value, onUnauthorized: () => logout("expired") });

  return { token, user, notice, login, logout };
});
