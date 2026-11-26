<script setup lang="ts">
import { computed } from "vue";
import { useAlertsStore } from "../stores/alerts";

withDefaults(defineProps<{ showLabel?: boolean }>(), { showLabel: true });
const alerts = useAlertsStore();
const label = computed(() => `${alerts.unread} unread ${alerts.unread === 1 ? "alert" : "alerts"}`);
</script>

<template>
  <span
    class="badge"
    :class="{ some: alerts.unread > 0 }"
    role="status"
    :aria-label="label"
    data-testid="alert-badge"
  >
    <span
      v-if="showLabel"
      aria-hidden="true"
    >Alerts</span>
    <b
      aria-hidden="true"
      data-testid="alert-badge-count"
    >{{ alerts.unread }}</b>
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--muted);
}
.badge b {
  min-width: 20px;
  padding: 0 6px;
  text-align: center;
  font-family: var(--font-mono);
  border: 1px solid var(--line);
  border-radius: 10px;
}
.some b {
  color: var(--crit);
  border-color: var(--crit);
}
</style>
