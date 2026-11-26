import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeDb } from "../../tests/fake-db";
import { authEndpoints } from "../../tests/fake-endpoints";
import { userFaketory } from "../../tests/faketories";
import { fake } from "../../tests/fake-socket";
import { listDrones, loginOk, server, url } from "../../tests/node-server";
import { useAuthStore } from "./auth";
import { useFleetStore } from "./fleet";
import { useFleetFilterStore } from "./fleetFilter";
import { useMapPickStore } from "./mapPick";
import { useSelectionStore } from "./selection";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => {
  setActivePinia(createPinia());
  fake.reset();
  localStorage.clear();
  sessionStorage.clear();
  // Refresh and logout are answered by the fake session table; a test overrides what it needs.
  server.use(authEndpoints.happyPath[1]!, authEndpoints.happyPath[2]!);
});

afterEach(() => vi.useRealTimers());

/** A real login through the fake login endpoint, so the fake session (cookie and CSRF token) exists. */
async function loggedIn() {
  fakeDb.users.add(userFaketory.build(), "pw");
  server.use(authEndpoints.happyPath[0]!);
  const auth = useAuthStore();
  await auth.login("a@skyhaul.dev", "pw");
  return auth;
}

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

  it("logout ends pick mode and drops the picked target", async () => {
    const auth = await signedIn();
    const pick = useMapPickStore();
    pick.pick(32.1, 34.8);
    pick.start();

    auth.logout();

    expect(pick.active).toBe(false);
    expect(pick.picked).toBeNull();
    expect(pick.target).toBeNull();
  });

  it("restores the session on a reload from the refresh cookie, asking once", async () => {
    fakeDb.sessions.start(userFaketory.build());
    const auth = useAuthStore();

    await auth.restore();
    await auth.restore();

    expect(auth.token).toBe("tok-2");
    expect(auth.user?.email).toBe("a@skyhaul.dev");
    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });

  it("stays signed out when there is no valid cookie", async () => {
    const auth = useAuthStore();

    await auth.restore();

    expect(auth.token).toBeNull();
    expect(auth.notice).toBeNull();
    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);
  });

  it("sends one request for two refreshes at once", async () => {
    fakeDb.sessions.start(userFaketory.build());
    const auth = useAuthStore();

    await Promise.all([auth.refresh(), auth.refresh()]);

    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);
    expect(auth.token).toBe("tok-2");
  });

  it("retries a 401 once with a refreshed token", async () => {
    const auth = await loggedIn();
    server.use(listDrones([]));
    server.use(authEndpoints.rejectToken("/drones", "tok-1"));
    const fleet = useFleetStore();

    await fleet.loadAll();

    expect(fleet.loaded.done).toBe(true);
    expect(auth.token).toBe("tok-2");
    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);
  });

  it("logs out with the expired notice on a second 401, and never retries /auth/refresh", async () => {
    const auth = await loggedIn();
    server.use(listDrones([]));
    server.use(authEndpoints.rejectToken("/drones"));

    await useFleetStore().loadAll();

    expect(auth.token).toBeNull();
    expect(auth.notice).toBe("expired");
    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);

    // A refresh that is refused is not refreshed again either: one request, then login.
    await loggedIn();
    server.use(authEndpoints.refreshRespondWith(401), authEndpoints.rejectToken("/drones", "tok-1"));
    fakeDb.sessions.refreshRequests = [];
    await useFleetStore().loadAll();
    expect(fakeDb.sessions.refreshRequests).toHaveLength(1);
    expect(auth.notice).toBe("expired");
  });

  it("retries the refresh once on 409 refresh_superseded", async () => {
    fakeDb.sessions.start(userFaketory.build());
    server.use(authEndpoints.refreshSupersededOnce());
    const auth = useAuthStore();

    await auth.restore();

    expect(auth.token).toBe("tok-2");
    expect(fakeDb.sessions.refreshRequests).toHaveLength(2);
  });

  it("refreshes 60 s before expiry and reconnects the socket with the new token", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const auth = await loggedIn(); // expires_in is 900 s: the timer is set for 840 s
    useFleetStore().startRealtime();

    await vi.advanceTimersByTimeAsync(839_000);
    expect(fake.reconnects).toBe(0);
    await vi.advanceTimersByTimeAsync(1_000);

    await vi.waitFor(() => expect(fake.reconnects).toBe(1));
    expect(auth.token).toBe("tok-2");
  });

  it("signOut calls POST /auth/logout with the CSRF token, then clears the store", async () => {
    const auth = await loggedIn();
    const csrf = fakeDb.sessions.csrf;

    await auth.signOut();

    expect(fakeDb.sessions.logoutRequests).toEqual([{ csrf }]);
    expect(auth.token).toBeNull();
    expect(auth.user).toBeNull();
  });
});
