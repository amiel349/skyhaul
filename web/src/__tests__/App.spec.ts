import { render, screen, waitFor } from "@testing-library/vue";
import userEvent from "@testing-library/user-event";
import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { safeNext } from "../auth/safeNext";
import App from "../App.vue";
import { createMemoryHistory } from "vue-router";
import { createAppRouter } from "../router";
import { useAuthStore } from "../stores/auth";
import { useFleetStore } from "../stores/fleet";
import ConnectionBadge from "../components/ConnectionBadge.vue";
import FleetMap from "../components/FleetMap.vue";
import { useConnectionStore } from "../stores/connection";
import { fake } from "./fakeSocket";
import { drone, event, listDrones, liveState, loginOk, server, url } from "./server";

vi.mock("../realtime/socket", async () => await import("./fakeSocket"));

let router: ReturnType<typeof createAppRouter>;

async function mountApp(path = "/") {
  const pinia = createPinia();
  setActivePinia(pinia);
  router = createAppRouter(createMemoryHistory());
  await router.push(path);
  await router.isReady();
  const view = render(App, { global: { plugins: [pinia, router] } });
  return { ...view, pinia };
}

describe("dispatcher UI", () => {
  it("shows the login page when not signed in", async () => {
    await mountApp("/");

    expect(await screen.findByTestId("login-form")).toBeTruthy();
  });

  it("blocks empty fields, shows wrong credentials and the 429 hint", async () => {
    await mountApp("/login");
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("login-submit"));
    expect(screen.getByTestId("login-missing")).toBeTruthy();

    await user.type(screen.getByTestId("login-email"), "a@skyhaul.dev");
    await user.type(screen.getByTestId("login-password"), "bad");
    server.use(http.post(url("/auth/login"), () => new HttpResponse(null, { status: 401 })));
    await user.click(screen.getByTestId("login-submit"));
    expect((await screen.findByTestId("login-error")).textContent).toBe("Wrong email or password");

    await user.type(screen.getByTestId("login-password"), "bad");
    server.use(
      http.post(url("/auth/login"), () => new HttpResponse(null, { status: 429, headers: { "Retry-After": "30" } })),
    );
    await user.click(screen.getByTestId("login-submit"));
    await waitFor(() => expect(screen.getByTestId("login-error").textContent).toContain("Try again later"));
  });

  it("logs in, shows the map with a marker per positioned drone, and logout returns to login", async () => {
    server.use(loginOk, listDrones([drone("d1"), drone("d2", null)]));
    await mountApp("/login");
    const user = userEvent.setup();

    await user.type(await screen.findByTestId("login-email"), "a@skyhaul.dev");
    await user.type(screen.getByTestId("login-password"), "pw");
    await user.click(screen.getByTestId("login-submit"));

    const map = await screen.findByTestId("fleet-map");
    await waitFor(() => expect(map.getAttribute("data-marker-count")).toBe("1"));

    await user.click(screen.getByTestId("user-avatar"));
    await user.click(screen.getByTestId("user-menu-logout"));
    expect(await screen.findByTestId("login-form")).toBeTruthy();
    await router.back();
    expect(await screen.findByTestId("login-form")).toBeTruthy();
    expect(useAuthStore().token).toBeNull();
  });

  it("shows Session expired on the login page when the socket refuses the token", async () => {
    server.use(loginOk, listDrones([drone("d1")]));
    await mountApp("/login");
    const user = userEvent.setup();
    await user.type(await screen.findByTestId("login-email"), "a@skyhaul.dev");
    await user.type(screen.getByTestId("login-password"), "pw");
    await user.click(screen.getByTestId("login-submit"));
    await screen.findByTestId("fleet-map");

    fake.handlers!.onAuthError("token_expired");

    expect((await screen.findByTestId("login-expired")).textContent).toContain("Session expired");
    expect(useAuthStore().token).toBeNull();
  });
});

describe("map", () => {
  it("moves a marker on an event and marks a LOST drone with a text label", async () => {
    server.use(loginOk, listDrones([drone("d1")]));
    const pinia = createPinia();
    setActivePinia(pinia);
    await useAuthStore().login("a@skyhaul.dev", "pw");
    const fleet = useFleetStore();
    await fleet.loadAll();
    render(FleetMap, { global: { plugins: [pinia] } });
    await waitFor(() => expect(screen.getByTestId("fleet-marker-label-d1").dataset.link).toBe("UP"));

    fleet.applyState(event("d1", liveState({ link: "LOST", ts: "2026-01-01T00:01:00.000Z" })));

    await waitFor(() => {
      const label = screen.getByTestId("fleet-marker-label-d1");
      expect(label.dataset.link).toBe("LOST");
      expect(label.textContent).toContain("link lost");
    });
  });

  it("renders an untrusted drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    server.use(loginOk, listDrones([{ ...drone("d1"), name: hostile }]));
    const pinia = createPinia();
    setActivePinia(pinia);
    await useAuthStore().login("a@skyhaul.dev", "pw");
    await useFleetStore().loadAll();
    const { container } = render(FleetMap, { global: { plugins: [pinia] } });

    const label = await screen.findByTestId("fleet-marker-label-d1");

    expect(label.textContent).toBe(hostile);
    expect(container.querySelector("img")?.outerHTML).toBeUndefined();
  });

  it("shows a Reconnecting banner while the socket is down", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    render(ConnectionBadge, { global: { plugins: [pinia] } });

    useConnectionStore().set("reconnecting");

    expect((await screen.findByTestId("connection-reconnecting")).textContent).toContain("Reconnecting");
  });
});

describe("safeNext", () => {
  it("accepts only local paths", () => {
    expect(safeNext("/map?x=1")).toBe("/map?x=1");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext("/\t/evil.example")).toBe("/");
    expect(safeNext("/\n/evil.example")).toBe("/");
    expect(safeNext("/\r/evil.example")).toBe("/");
  });
});
