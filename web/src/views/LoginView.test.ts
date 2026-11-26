import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useAuthStore } from "../stores/auth";
import { renderComponent } from "../../tests/render";
import { worker } from "../../tests/worker";
import { fakeDb } from "../../tests/fake-db";
import { authEndpoints } from "../../tests/fake-endpoints";
import { userFaketory } from "../../tests/faketories";
import LoginView from "./LoginView.vue";

// Renders LoginView on the app's router (nobody is signed in, so /login is reachable).
async function renderLogin(route = "/login") {
  const { pinia, router } = await renderComponent(LoginView, { route });
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
    fakeDb.users.add(userFaketory.build({ email: "a@skyhaul.dev" }), "pw");
    await renderLogin();

    await signIn("a@skyhaul.dev", "bad");

    await expect.element(page.getByTestId("login-error")).toHaveTextContent("Wrong email or password");
  });

  it("shows the Retry-After hint on a 429", async () => {
    worker.use(authEndpoints.respondWith(429, { retryAfter: 30 }));
    await renderLogin();

    await signIn("a@skyhaul.dev", "pw");

    await expect.element(page.getByTestId("login-error")).toHaveTextContent("Try again later (in 30 s)");
  });

  it("signs in, keeps the token in the store and goes to the page it came from", async () => {
    fakeDb.users.add(userFaketory.build({ email: "a@skyhaul.dev" }), "pw");
    const { pinia, router } = await renderLogin("/login?next=/drones/d1");

    await signIn("a@skyhaul.dev", "pw");

    await expect.poll(() => router.currentRoute.value.name).toBe("drone");
    expect(useAuthStore(pinia).token).toBe("tok-1");
  });

  it("says Session expired when the session ended", async () => {
    const { pinia } = await renderLogin();

    useAuthStore(pinia).logout("expired");

    await expect.element(page.getByTestId("login-expired")).toHaveTextContent("Session expired. Sign in again.");
  });
});
