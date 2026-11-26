// The one mount helper for component tests. It does plumbing only: a fresh Pinia, an optional router
// at an optional route, an optional signed-in user, and global plugins. It stubs nothing and seeds no
// data (the test builds data with faketories and puts it in the fake db). Anything special is an option.
import { createPinia, setActivePinia, type Pinia } from "pinia";
import type { Component, Plugin } from "vue";
import { render, type RenderResult } from "vitest-browser-vue";
import { createMemoryHistory, type Router } from "vue-router";
import type { User } from "../src/api/types";
import { createAppRouter } from "../src/router";
import { useAuthStore } from "../src/stores/auth";
import { userFaketory } from "./faketories";

export interface RenderOptions {
  props?: Record<string, unknown>;
  /** Slot content by slot name, as plain text. */
  slots?: Record<string, string>;
  /** Non-prop attributes on the root element (class, style, ...). */
  attrs?: Record<string, unknown>;
  /** Start the app's router (memory history) at this route and install it. Omit it for a component with no router. */
  route?: string;
  /** Sign in as this user. `role` is required; any other field overrides the faketory default. */
  user?: Partial<User> & { role: User["role"] };
  /** Extra global plugins, installed after Pinia and the router. */
  plugins?: Plugin[];
}

export interface Rendered extends RenderResult<Record<string, unknown>> {
  pinia: Pinia;
}

/** The access token a signed-in test user holds. */
const TEST_TOKEN = "tok-1";

export async function renderComponent(
  component: Component,
  options: RenderOptions & { route: string },
): Promise<Rendered & { router: Router }>;
export async function renderComponent(component: Component, options?: RenderOptions): Promise<Rendered>;
export async function renderComponent(
  component: Component,
  options: RenderOptions = {},
): Promise<Rendered & { router?: Router }> {
  const pinia = createPinia();
  // The router's auth guard and the stores read the active Pinia, also before the app is mounted.
  setActivePinia(pinia);

  if (options.user) {
    // Signing in is setting the auth store's state, the same two refs a successful login sets. The
    // login request itself is covered by the LoginView and auth store tests.
    const auth = useAuthStore(pinia);
    auth.token = TEST_TOKEN;
    auth.user = userFaketory.build(options.user);
  }

  let router: Router | undefined;
  if (options.route !== undefined) {
    router = createAppRouter(createMemoryHistory());
    await router.push(options.route);
    await router.isReady();
  }

  const plugins: Plugin[] = [pinia, ...(router ? [router] : []), ...(options.plugins ?? [])];
  const view = await render(component, { props: options.props, slots: options.slots, attrs: options.attrs, global: { plugins } });
  return Object.assign(view, { pinia, router });
}
