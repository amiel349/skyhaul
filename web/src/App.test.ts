import { page } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import App from "./App.vue";
import { useAuthStore } from "./stores/auth";
import { renderComponent } from "../tests/render";

// The live socket is the one thing the real fleet page reaches outside the page.
vi.mock("./realtime/socket", async () => await import("../tests/fake-socket"));

// Renders App on the app's router, so the pages are the real login and fleet pages.
async function renderApp(route: string, signedIn = false) {
  const { pinia, router } = await renderComponent(App, {
    route,
    user: signedIn ? { role: "dispatcher" } : undefined,
  });
  return { pinia, router };
}

describe("App", () => {
  it("renders the page of the current route inside the app shell", async () => {
    await renderApp("/login");

    // The shell is a plain wrapper with no role or text, so it is found by its test id.
    await expect.element(page.getByTestId("app-shell")).toBeInTheDocument();
    await expect.element(page.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("leaves a protected page for the login page when the token is gone", async () => {
    const { pinia, router } = await renderApp("/", true);
    await expect.element(page.getByRole("list")).toBeInTheDocument();

    useAuthStore(pinia).token = null;

    await expect.poll(() => router.currentRoute.value.name).toBe("login");
    await expect.element(page.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});
