import { createPinia } from "pinia";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { worker } from "../testing/worker";
import { fakeDb, fakeEndpoints } from "./LoginView.fakes";
import LoginView from "./LoginView.vue";

// Renders only LoginView; the page it sends you to after login is a stub route.
async function renderLogin(path = "/login") {
  worker.use(...fakeEndpoints);
  const pinia = createPinia();
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/", name: "fleet", component: { template: "<p data-testid='fleet-stub'>Fleet</p>" } },
      { path: "/map", name: "map", component: { template: "<p data-testid='map-stub'>Map</p>" } },
    ],
  });
  await router.push(path);
  await router.isReady();
  await render(LoginView, { global: { plugins: [pinia, router] } });
  return { pinia, router };
}

async function signIn(email: string, password: string) {
  await userEvent.fill(page.getByTestId("login-email"), email);
  await userEvent.fill(page.getByTestId("login-password"), password);
  await userEvent.click(page.getByTestId("login-submit"));
}

describe("LoginView", () => {
  it("blocks empty fields", async () => {
    await renderLogin();

    await userEvent.click(page.getByTestId("login-submit"));

    await expect.element(page.getByTestId("login-missing")).toHaveTextContent("Enter email and password");
  });

  it("shows wrong credentials as one message", async () => {
    fakeDb.addUser("a@skyhaul.dev", "pw");
    await renderLogin();

    await signIn("a@skyhaul.dev", "bad");

    await expect.element(page.getByTestId("login-error")).toHaveTextContent("Wrong email or password");
  });

  it("shows the Retry-After hint on a 429", async () => {
    fakeDb.rateLimitedS = 30;
    await renderLogin();

    await signIn("a@skyhaul.dev", "pw");

    await expect.element(page.getByTestId("login-error")).toHaveTextContent("Try again later (in 30 s)");
  });

  it("signs in, keeps the token in the store and goes to the page it came from", async () => {
    fakeDb.addUser("a@skyhaul.dev", "pw");
    const { pinia, router } = await renderLogin("/login?next=/map");

    await signIn("a@skyhaul.dev", "pw");

    await expect.poll(() => router.currentRoute.value.name).toBe("map");
    expect(useAuthStore(pinia).token).toBe("tok-1");
  });

  it("says Session expired when the session ended", async () => {
    const { pinia } = await renderLogin();

    useAuthStore(pinia).logout("expired");

    await expect.element(page.getByTestId("login-expired")).toHaveTextContent("Session expired. Sign in again.");
  });
});
