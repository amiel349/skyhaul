import { createRouter, createWebHistory } from "vue-router";
import type { RouterHistory } from "vue-router";
import { useAuthStore } from "./stores/auth";
import FleetView from "./views/FleetView.vue";
import LoginView from "./views/LoginView.vue";

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({
    history,
    routes: [
      { path: "/", name: "fleet", component: FleetView },
      { path: "/login", name: "login", component: LoginView, meta: { public: true } },
      { path: "/:rest(.*)*", redirect: "/" },
    ],
  });

  // The one auth guard: every page but /login needs a token in memory.
  router.beforeEach((to) => {
    const auth = useAuthStore();
    if (!to.meta.public && !auth.token) {
      return { name: "login", query: to.fullPath === "/" ? {} : { next: to.fullPath } };
    }
    if (to.meta.public && auth.token) return { name: "fleet" };
    return true;
  });
  return router;
}
