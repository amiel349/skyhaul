<script setup lang="ts">
import { computed, watch } from "vue";
import type { Command, CommandStatus } from "../api/types";
import { formatTimestamp } from "./format";
import StatusChip from "./StatusChip.vue";
import type { Tone } from "./status";
import { useCommandsStore } from "../stores/commands";

const props = defineProps<{ droneId: string }>();

const commands = useCommandsStore();
const rows = computed(() => commands.forDrone(props.droneId));
const loadError = computed(() => commands.loadError.get(props.droneId) ?? null);

// The history loads when the drone's detail opens (and when another drone is opened).
watch(
  () => props.droneId,
  (id) => void commands.load(id),
  { immediate: true },
);

const TONE: Record<CommandStatus, Tone> = {
  PENDING: "neutral",
  SENT: "neutral",
  ACKED: "ok",
  NACKED: "crit",
  TIMED_OUT: "crit",
  FAILED: "crit",
};

// Names, details and reasons are untrusted drone text: always interpolated, never HTML.
// A list (the waypoints of START_MISSION) is summarised by its size, an object is shown as compact
// JSON; String() would print "[object Object]" for both.
const formatValue = (value: unknown): string => {
  if (Array.isArray(value)) return `${value.length} ${value.length === 1 ? "item" : "items"}`;
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
};
const payloadText = (command: Command): string =>
  Object.entries(command.payload)
    .map(([key, value]) => `${key} ${formatValue(value)}`)
    .join(", ");
</script>

<template>
  <p
    v-if="loadError"
    class="note err"
    role="alert"
    data-testid="command-log-error"
  >
    {{ loadError }}
  </p>
  <p
    v-if="rows.length === 0 && !loadError"
    class="note"
    data-testid="command-log-empty"
  >
    No commands yet
  </p>
  <ul
    v-if="rows.length > 0"
    class="list"
    aria-label="Commands of this drone"
    data-testid="command-list"
  >
    <li
      v-for="command in rows"
      :key="command.id"
      class="item"
      :data-testid="`command-row-${command.id}`"
    >
      <div class="line">
        <strong :data-testid="`command-type-${command.id}`">{{ command.type }}</strong>
        <StatusChip
          :text="command.status"
          :tone="TONE[command.status]"
          :testid="`command-status-${command.id}`"
        />
        <span
          v-if="command.late"
          class="late"
          :data-testid="`command-late-${command.id}`"
        >Late reply</span>
      </div>
      <div
        v-if="payloadText(command)"
        class="mono muted"
        :data-testid="`command-payload-${command.id}`"
      >
        {{ payloadText(command) }}
      </div>
      <div
        v-if="command.status === 'NACKED'"
        class="crit"
        :data-testid="`command-nack-${command.id}`"
      >
        <span :data-testid="`command-nack-reason-${command.id}`">{{ command.nack_reason }}</span>
        <span
          v-if="command.nack_detail"
          :data-testid="`command-nack-detail-${command.id}`"
        >: {{ command.nack_detail }}</span>
      </div>
      <div
        v-if="command.status === 'TIMED_OUT'"
        class="crit"
        :data-testid="`command-timeout-${command.id}`"
      >
        No reply from drone
      </div>
      <div
        v-if="command.status === 'FAILED'"
        class="crit"
        :data-testid="`command-failed-${command.id}`"
      >
        Not sent to the drone
      </div>
      <div
        class="mono muted"
        :data-testid="`command-time-${command.id}`"
      >
        {{ formatTimestamp(command.created_at) }}
      </div>
    </li>
  </ul>
</template>

<style scoped>
.note {
  margin: 0;
  color: var(--muted);
}
.err,
.crit {
  color: var(--crit);
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.item {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow-wrap: anywhere;
}
.line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.late {
  font-size: 11px;
  color: var(--warn);
  border: 1px solid var(--warn);
  border-radius: 4px;
  padding: 1px 6px;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.muted {
  color: var(--muted);
}
</style>
