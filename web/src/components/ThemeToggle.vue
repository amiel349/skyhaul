<script setup lang="ts">
import { useThemeStore } from "../theme/theme";

const theme = useThemeStore();
</script>

<template>
  <button
    type="button"
    class="toggle"
    data-testid="theme-toggle"
    :data-theme-value="theme.resolved"
    :aria-label="`Switch to ${theme.resolved === 'dark' ? 'light' : 'dark'} theme`"
    @click="theme.toggle()"
  >
    {{ theme.resolved === "dark" ? "Light" : "Dark" }}
  </button>
</template>

<style scoped>
.toggle {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
}
.toggle:hover {
  border-color: var(--accent);
}
</style>
