import { createPinia } from "pinia";
import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import App from "./App.vue";
import { useAuthStore } from "./stores/auth";

// Renders only App; both pages are stub routes, so the test is about the shell and its token watcher.
async function renderApp(path: string, token: string | null = null) {
  const pinia = createPinia();
  useAuthStore(pinia).token = token;
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "fleet", component: { template: "<p data-testid='fleet-stub'>Fleet page</p>" } },
      {
        path: "/login",
        name: "login",
        meta: { public: true },
        component: { template: "<p data-testid='login-stub'>Login page</p>" },
      },
    ],
  });
  await router.push(path);
  await router.isReady();
  await render(App, { global: { plugins: [pinia, router] } });
  return { pinia, router };
}

describe("App", () => {
  it("renders the page of the current route inside the app shell", async () => {
    await renderApp("/login");

    await expect.element(page.getByTestId("app-shell")).toBeInTheDocument();
    await expect.element(page.getByTestId("login-stub")).toHaveTextContent("Login page");
  });

  it("leaves a protected page for the login page when the token is gone", async () => {
    const { pinia, router } = await renderApp("/", "tok-1");
    await expect.element(page.getByTestId("fleet-stub")).toBeInTheDocument();

    useAuthStore(pinia).token = null;

    await expect.poll(() => router.currentRoute.value.name).toBe("login");
    await expect.element(page.getByTestId("login-stub")).toBeInTheDocument();
  });
});
