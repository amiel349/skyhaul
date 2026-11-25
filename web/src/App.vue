<script setup lang="ts">
import { watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";
import { useThemeStore } from "./theme/theme";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
useThemeStore().init();

// Token gone (logout, 401, socket auth error): leave any protected page for the login page.
watch(
  () => auth.token,
  (token) => {
    if (!token && !route.meta.public) void router.replace({ name: "login" });
  },
);
</script>

<template>
  <div data-testid="app-shell">
    <RouterView />
  </div>
</template>
