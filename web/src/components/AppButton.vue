<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    variant?: "primary" | "secondary" | "danger" | "quiet";
    size?: "sm" | "md";
    loading?: boolean;
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
  }>(),
  { variant: "secondary", size: "md", loading: false, type: "button", disabled: false },
);

// A loading button is a disabled button that keeps its text: the browser then fires no click at all.
const blocked = computed(() => props.disabled || props.loading);
</script>

<template>
  <button
    :type="type"
    class="btn"
    :class="[`v-${variant}`, `s-${size}`, { loading }]"
    :disabled="blocked"
    :aria-busy="loading ? 'true' : undefined"
  >
    <span
      v-if="loading"
      class="spin"
      aria-hidden="true"
    />
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font: inherit;
  font-weight: 600;
  line-height: 1.2;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.s-md {
  height: 32px;
  padding: 0 16px;
}
.s-sm {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}
.v-primary {
  background: var(--accent);
  color: var(--on-accent);
  border-color: var(--accent);
}
.v-secondary {
  background: var(--panel-2);
  color: var(--ink);
  border-color: var(--line);
}
.v-danger {
  background: transparent;
  color: var(--crit);
  border-color: var(--crit);
}
.v-quiet {
  background: transparent;
  color: var(--accent);
}
.btn:hover:not(:disabled) {
  filter: brightness(1.12);
}
.v-secondary:hover:not(:disabled),
.v-quiet:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.btn.loading {
  cursor: progress;
}
.spin {
  width: 10px;
  height: 10px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
