import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fake } from "../testing/fake-socket";
import { loginOk, server, url } from "../testing/node-server";
import { useAuthStore } from "./auth";
import { useFleetStore } from "./fleet";
import { useFleetFilterStore } from "./fleetFilter";
import { useSelectionStore } from "./selection";

vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

beforeEach(() => {
  setActivePinia(createPinia());
  fake.reset();
  localStorage.clear();
  sessionStorage.clear();
});

async function signedIn() {
  server.use(loginOk);
  const auth = useAuthStore();
  await auth.login("a@skyhaul.dev", "pw");
  return auth;
}

describe("auth store", () => {
  it("login keeps token and user in memory only", async () => {
    const auth = await signedIn();

    expect(auth.token).toBe("tok-1");
    expect(auth.user?.email).toBe("a@skyhaul.dev");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("reports wrong credentials and a 429 with Retry-After", async () => {
    const auth = useAuthStore();
    server.use(http.post(url("/auth/login"), () => new HttpResponse(null, { status: 401 })));
    expect(await auth.login("a@skyhaul.dev", "bad")).toEqual({ ok: false, reason: "invalid" });
    expect(auth.token).toBeNull();

    server.use(
      http.post(url("/auth/login"), () => new HttpResponse(null, { status: 429, headers: { "Retry-After": "30" } })),
    );
    expect(await auth.login("a@skyhaul.dev", "bad")).toEqual({
      ok: false,
      reason: "rate_limited",
      retryAfterS: 30,
    });
  });

  it("a 401 on any REST call clears the store and closes the socket", async () => {
    const auth = await signedIn();
    const fleet = useFleetStore();
    fleet.startRealtime();
    server.use(http.get(url("/drones"), () => new HttpResponse(null, { status: 401 })));

    await fleet.loadAll();

    expect(auth.token).toBeNull();
    expect(auth.notice).toBe("expired");
    expect(fake.open).toBe(false);
  });

  it("logout drops the token and closes the socket", async () => {
    const auth = await signedIn();
    useFleetStore().startRealtime();

    auth.logout();

    expect(auth.token).toBeNull();
    expect(auth.user).toBeNull();
    expect(auth.notice).toBeNull();
    expect(fake.open).toBe(false);
  });

  it("logout clears the selection, the not-found id and the list filter", async () => {
    const auth = await signedIn();
    const selection = useSelectionStore();
    const filter = useFleetFilterStore();
    selection.notFoundId = "nope";
    filter.query = "abc";
    filter.link = "LOST";

    auth.logout();

    expect(selection.notFoundId).toBeNull();
    expect(filter.query).toBe("");
    expect(filter.link).toBe("ALL");
  });
});
