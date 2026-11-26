<script setup lang="ts">
import { computed } from "vue";
import { useAlertsStore } from "../stores/alerts";

withDefaults(defineProps<{ showLabel?: boolean }>(), { showLabel: true });
const alerts = useAlertsStore();
// The count is the number of alerts in the table (it follows the filters); the unread count is the
// part of those that nobody has acknowledged yet.
const total = computed(() => alerts.visible.length);
const unread = computed(() => alerts.visibleUnread);
const label = computed(
  () => `${total.value} ${total.value === 1 ? "alert" : "alerts"}${unread.value > 0 ? `, ${unread.value} unread` : ""}`,
);
</script>

<template>
  <span
    class="badge"
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
    >{{ total }}</b>
    <span
      v-if="unread > 0"
      class="unread"
      aria-hidden="true"
      data-testid="alert-badge-unread"
    >{{ unread }} unread</span>
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
.unread {
  color: var(--crit);
  font-weight: 600;
}
</style>
