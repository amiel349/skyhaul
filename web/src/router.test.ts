import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory } from "vue-router";
import { createAppRouter } from "./router";
import { useAuthStore } from "./stores/auth";

vi.mock("./realtime/socket", async () => await import("./testing/fake-socket"));

beforeEach(() => setActivePinia(createPinia()));

describe("router guard", () => {
  it("sends a visitor without a token to the login page, and a signed-in user away from it", async () => {
    const router = createAppRouter(createMemoryHistory());

    await router.push("/");
    expect(router.currentRoute.value.name).toBe("login");

    useAuthStore().token = "tok-1";
    await router.push("/some-page");
    await router.push("/login");
    expect(router.currentRoute.value.name).toBe("fleet");
  });

  it("keeps a drone link as ?next= for the login page", async () => {
    const router = createAppRouter(createMemoryHistory());

    await router.push("/drones/d1");

    expect(router.currentRoute.value.name).toBe("login");
    expect(router.currentRoute.value.query.next).toBe("/drones/d1");
  });
});
