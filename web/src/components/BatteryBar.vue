<script setup lang="ts">
import { computed } from "vue";
import { batteryView } from "./status";

const props = defineProps<{ pct: number | null | undefined; testid?: string }>();
const view = computed(() => batteryView(props.pct));
</script>

<template>
  <span
    class="battery"
    :class="{ low: view.low, critical: view.critical }"
    :data-testid="testid"
    :data-low="view.low"
    :data-critical="view.critical"
  >
    <span
      class="bar"
      aria-hidden="true"
    ><i :style="{ width: `${view.width}%` }" /></span>
    <span class="text mono">{{ view.text }}</span>
  </span>
</template>

<style scoped>
.battery {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.bar {
  flex: none;
  width: 70px;
  height: 6px;
  border-radius: 3px;
  background: var(--line);
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  background: var(--ok);
}
.low .bar i {
  background: repeating-linear-gradient(135deg, var(--warn) 0 4px, transparent 4px 6px);
}
.low .text {
  color: var(--warn);
  font-weight: 600;
}
.critical .bar i {
  background: repeating-linear-gradient(135deg, var(--crit) 0 4px, transparent 4px 6px);
}
.critical .text {
  color: var(--crit);
}
.text {
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
