import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../stores/auth";
import { useConnectionStore } from "../stores/connection";
import { useFleetStore } from "../stores/fleet";
import { fake } from "./fakeSocket";
import { drone, event, listDrones, liveState, loginOk, server, url } from "./server";

vi.mock("../realtime/socket", async () => await import("./fakeSocket"));

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
});

describe("fleet store", () => {
  it("loads drones, moves a marker on drone.state, adds unknown ones, drops stale events", async () => {
    await signedIn();
    server.use(
      listDrones([drone("d1"), drone("d2", null)]),
      http.get(url("/drones/d9"), () => HttpResponse.json(drone("d9"))),
    );
    const fleet = useFleetStore();
    await fleet.loadAll();
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.0);

    const moved = liveState({ ts: "2026-01-01T00:00:20.000Z" });
    moved.position = { ...moved.position!, lat: 32.5 };
    fleet.applyState(event("d1", moved));
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(event("d1", liveState({ ts: "2026-01-01T00:00:05.000Z" })));
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(event("d9", liveState()));
    expect(fleet.drones.has("d9")).toBe(true);
  });

  it("refetches and replaces its state after a reconnect, showing Reconnecting meanwhile", async () => {
    await signedIn();
    server.use(listDrones([drone("d1")]));
    const fleet = useFleetStore();
    const connection = useConnectionStore();
    fleet.startRealtime();
    fake.handlers!.onConnect();
    expect(connection.status).toBe("live");

    fake.handlers!.onDisconnect();
    expect(connection.status).toBe("reconnecting");

    server.use(listDrones([drone("d2")]));
    fake.handlers!.onConnect();
    await vi.waitFor(() => expect([...fleet.drones.keys()]).toEqual(["d2"]));
    expect(connection.status).toBe("live");
  });

  it("logs out with Session expired when the socket answers token_expired", async () => {
    const auth = await signedIn();
    useFleetStore().startRealtime();

    fake.handlers!.onAuthError("token_expired");

    expect(auth.token).toBeNull();
    expect(auth.notice).toBe("expired");
    expect(fake.open).toBe(false);
  });
});
