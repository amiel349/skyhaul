import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Alert } from "../api/types";
import { fake } from "../../tests/fake-socket";
import { loginOk, server, url } from "../../tests/node-server";
import { alertFaketory } from "../../tests/faketories";
import { useAlertsStore } from "./alerts";
import { useAuthStore } from "./auth";
import { useFleetStore } from "./fleet";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => {
  setActivePinia(createPinia());
  fake.reset();
});

const alert = (id: string, over: Partial<Alert> = {}) => alertFaketory.build({ id, ...over });

function listAlerts(items: Alert[], seen: URLSearchParams[] = []) {
  return http.get(url("/alerts"), ({ request }) => {
    seen.push(new URL(request.url).searchParams);
    return HttpResponse.json({ items, next_cursor: null });
  });
}

describe("alerts store", () => {
  it("merges alert.raised and alert.updated by id: a duplicate adds no row, an older state never wins", () => {
    const store = useAlertsStore();
    store.applyUpdate(alert("a1"));
    store.applyUpdate(alert("a1"));
    store.applyUpdate(alert("a1", { acknowledged_by: "u1", acknowledged_at: "2026-01-01T00:05:00Z" }));
    store.applyUpdate(alert("a1")); // older: still unacknowledged

    expect(store.visible).toHaveLength(1);
    expect(store.visible[0]).toMatchObject({ acknowledged_by: "u1", acknowledged_at: "2026-01-01T00:05:00Z" });
  });

  it("counts open alerts nobody has acknowledged as unread", () => {
    const store = useAlertsStore();
    store.applyUpdate(alert("a1"));
    store.applyUpdate(alert("a2"));
    store.applyUpdate(alert("a3"));
    expect(store.unread).toBe(3);

    store.applyUpdate(alert("a2", { acknowledged_by: "u1", acknowledged_at: "2026-01-01T00:05:00Z" }));
    store.applyUpdate(alert("a3", { cleared_at: "2026-01-01T00:06:00Z" }));

    expect(store.unread).toBe(1);
  });

  it("filters by severity, drone and open only, and puts CRITICAL first within its group", () => {
    const store = useAlertsStore();
    store.applyUpdate(alert("a1", { severity: "WARNING", raised_at: "2026-01-01T00:02:00Z" }));
    store.applyUpdate(alert("a2", { severity: "CRITICAL", raised_at: "2026-01-01T00:01:00Z", drone_id: "d2" }));
    store.applyUpdate(alert("a3", { severity: "INFO", cleared_at: "2026-01-01T00:03:00Z" }));
    expect(store.visible.map((a) => a.id)).toEqual(["a2", "a1"]); // open only by default

    store.filter.openOnly = false;
    expect(store.visible.map((a) => a.id)).toEqual(["a2", "a1", "a3"]);
    store.filter.severity = "WARNING";
    expect(store.visible.map((a) => a.id)).toEqual(["a1"]);
    store.filter.severity = "ALL";
    store.filter.droneId = "d2";
    expect(store.visible.map((a) => a.id)).toEqual(["a2"]);
  });

  it("asks the API for ?open= and ?drone_id= when those filters change", async () => {
    const seen: URLSearchParams[] = [];
    server.use(listAlerts([], seen));
    const store = useAlertsStore();

    store.setFilter({ droneId: "d2" });
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    store.setFilter({ openOnly: false });
    await vi.waitFor(() => expect(seen).toHaveLength(2));

    expect(seen[0]?.get("open")).toBe("true");
    expect(seen[0]?.get("drone_id")).toBe("d2");
    expect(seen[1]?.has("open")).toBe(false);
  });

  it("refetches after a socket reconnect and drops an alert that was cleared meanwhile", async () => {
    server.use(loginOk, listAlerts([alert("a1"), alert("a2")]));
    const auth = useAuthStore();
    await auth.login("a@skyhaul.dev", "pw");
    useFleetStore().startRealtime();
    fake.handlers!.onConnect();
    const store = useAlertsStore();
    store.applyUpdate(alert("a1"));
    store.applyUpdate(alert("a2"));

    server.use(listAlerts([alert("a2"), alert("a3")]));
    fake.handlers!.onConnect(); // a reconnect

    await vi.waitFor(() => expect(store.visible.map((a) => a.id).sort()).toEqual(["a2", "a3"]));
  });

  it("takes alert.raised and alert.updated from the socket", async () => {
    server.use(loginOk);
    await useAuthStore().login("a@skyhaul.dev", "pw");
    useFleetStore().startRealtime();
    const store = useAlertsStore();

    fake.handlers!.onAlertRaised(alert("a1"));
    fake.handlers!.onAlertUpdated(alert("a1", { cleared_at: "2026-01-01T00:06:00Z" }));

    expect(store.byId.get("a1")?.cleared_at).toBe("2026-01-01T00:06:00Z");
  });

  it("logout clears the alerts and the filters", async () => {
    server.use(loginOk);
    const auth = useAuthStore();
    await auth.login("a@skyhaul.dev", "pw");
    const store = useAlertsStore();
    store.applyUpdate(alert("a1"));
    store.filter.severity = "INFO";

    auth.logout();

    expect(store.visible).toHaveLength(0);
    expect(store.unread).toBe(0);
    expect(store.filter.severity).toBe("ALL");
  });

  it("treats a 409 on acknowledge as quiet: no error, the first acknowledger shows", async () => {
    server.use(
      http.post(url("/alerts/a1/ack"), () => HttpResponse.json({ error: "conflict" }, { status: 409 })),
      listAlerts([alert("a1", { acknowledged_by: "u2", acknowledged_at: "2026-01-01T00:05:00Z" })]),
    );
    const store = useAlertsStore();
    store.applyUpdate(alert("a1"));

    const result = await store.ack("a1");

    expect(result).toEqual({ ok: true, already: true });
    expect(store.ackError.size).toBe(0);
    expect(store.byId.get("a1")?.acknowledged_by).toBe("u2");
  });
});
