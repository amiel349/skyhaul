<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { safeNext } from "../auth/safeNext";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

const email = ref("");
const password = ref("");
const busy = ref(false);
const error = ref<string | null>(null);
const missing = ref(false);

const expired = computed(() => auth.notice === "expired");

async function submit(): Promise<void> {
  missing.value = !email.value.trim() || !password.value;
  if (missing.value) return;
  busy.value = true;
  error.value = null;
  const result = await auth.login(email.value.trim(), password.value);
  busy.value = false;
  password.value = "";
  if (result.ok) {
    await router.replace(safeNext(route.query.next));
  } else if (result.reason === "invalid") {
    error.value = "Wrong email or password";
  } else if (result.reason === "rate_limited") {
    error.value =
      result.retryAfterS === undefined
        ? "Try again later"
        : `Try again later (in ${result.retryAfterS} s)`;
  } else {
    error.value = "Cannot reach the server";
  }
}
</script>

<template>
  <main class="login">
    <h1>Skyhaul</h1>
    <p
      v-if="expired"
      role="status"
      data-testid="login-expired"
    >
      Session expired. Sign in again.
    </p>
    <form
      novalidate
      data-testid="login-form"
      @submit.prevent="submit"
    >
      <label for="login-email">Email</label>
      <input
        id="login-email"
        v-model="email"
        type="email"
        autocomplete="username"
        data-testid="login-email"
      >
      <label for="login-password">Password</label>
      <input
        id="login-password"
        v-model="password"
        type="password"
        autocomplete="current-password"
        data-testid="login-password"
      >
      <p
        v-if="missing"
        role="alert"
        data-testid="login-missing"
      >
        Enter email and password
      </p>
      <p
        v-if="error"
        role="alert"
        data-testid="login-error"
      >
        {{ error }}
      </p>
      <button
        type="submit"
        :disabled="busy"
        data-testid="login-submit"
      >
        Sign in
      </button>
    </form>
  </main>
</template>

<style scoped>
.login {
  max-width: 22rem;
  margin: 4rem auto;
  padding: 0 1rem;
}
form {
  display: grid;
  gap: 0.5rem;
}
input,
button {
  padding: 0.5rem;
  font: inherit;
  color: var(--ink);
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
}
button[type="submit"] {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
  cursor: pointer;
}
button:disabled {
  opacity: 0.55;
}
</style>
