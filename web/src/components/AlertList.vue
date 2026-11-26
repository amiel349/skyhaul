<script setup lang="ts">
import { computed, onMounted } from "vue";
import type { Alert, AlertSeverity } from "../api/types";
import { useAlertsStore, type SeverityFilter } from "../stores/alerts";
import { useAuthStore } from "../stores/auth";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { formatTimestamp } from "./format";
import StatusChip from "./StatusChip.vue";
import type { Tone } from "./status";

const alerts = useAlertsStore();
const auth = useAuthStore();
const fleet = useFleetStore();
const selection = useSelectionStore();

// The list owns the first load (the page always renders it); a socket reconnect refetches in the fleet store.
onMounted(() => void alerts.refetch());

const SEVERITIES: SeverityFilter[] = ["ALL", "CRITICAL", "WARNING", "INFO"];
const TONE: Record<AlertSeverity, Tone> = { CRITICAL: "crit", WARNING: "warn", INFO: "neutral" };

// Viewers see alerts but cannot acknowledge (the server refuses it too; full role work is issue #44).
const canAck = computed(() => auth.user?.role !== "viewer");

const droneName = (id: string): string => fleet.drones.get(id)?.name ?? id;
// Every drone that is known or has an alert, so an alert for an unknown drone can still be filtered.
const droneOptions = computed(() => {
  const ids = new Set<string>(fleet.drones.keys());
  for (const alert of alerts.byId.values()) ids.add(alert.drone_id);
  return [...ids].sort().map((id) => ({ id, name: droneName(id) }));
});

const needsAck = (alert: Alert): boolean => !alert.cleared_at && !alert.acknowledged_at;
const ackedBy = (alert: Alert): string =>
  alert.acknowledged_by && alert.acknowledged_by === auth.user?.id ? "you" : (alert.acknowledged_by ?? "unknown");
</script>

<template>
  <section
    class="alerts"
    aria-labelledby="alerts-heading"
    data-testid="alerts"
  >
    <div class="head">
      <h2 id="alerts-heading">
        Alerts
      </h2>
      <label for="alerts-severity">Severity</label>
      <select
        id="alerts-severity"
        :value="alerts.filter.severity"
        data-testid="alerts-filter-severity"
        @change="alerts.setFilter({ severity: ($event.target as HTMLSelectElement).value as SeverityFilter })"
      >
        <option
          v-for="option in SEVERITIES"
          :key="option"
          :value="option"
        >
          {{ option === "ALL" ? "All severities" : option }}
        </option>
      </select>
      <label for="alerts-drone">Drone</label>
      <select
        id="alerts-drone"
        :value="alerts.filter.droneId"
        data-testid="alerts-filter-drone"
        @change="alerts.setFilter({ droneId: ($event.target as HTMLSelectElement).value })"
      >
        <option value="">
          All drones
        </option>
        <option
          v-for="drone in droneOptions"
          :key="drone.id"
          :value="drone.id"
        >
          {{ drone.name }}
        </option>
      </select>
      <label for="alerts-open">Show</label>
      <select
        id="alerts-open"
        :value="alerts.filter.openOnly ? 'open' : 'all'"
        data-testid="alerts-filter-open"
        @change="alerts.setFilter({ openOnly: ($event.target as HTMLSelectElement).value === 'open' })"
      >
        <option value="open">
          Open only
        </option>
        <option value="all">
          All alerts
        </option>
      </select>
    </div>
    <p
      v-if="alerts.loadError.failed"
      class="note err"
      role="alert"
      data-testid="alerts-error"
    >
      Could not load the alerts
    </p>
    <p
      v-if="alerts.visible.length === 0 && !alerts.loadError.failed"
      class="note"
      data-testid="alerts-empty"
    >
      No alerts
    </p>
    <ul
      v-if="alerts.visible.length > 0"
      class="list"
      aria-label="Alerts"
      data-testid="alert-list"
    >
      <li
        v-for="alert in alerts.visible"
        :key="alert.id"
        class="item"
        :data-testid="`alert-row-${alert.id}`"
      >
        <div class="line">
          <StatusChip
            :text="alert.severity"
            :tone="TONE[alert.severity]"
            :testid="`alert-severity-${alert.id}`"
          />
          <strong
            class="mono"
            :data-testid="`alert-code-${alert.id}`"
          >{{ alert.code }}</strong>
          <button
            type="button"
            class="drone"
            :aria-label="`Open drone ${droneName(alert.drone_id)}`"
            :data-testid="`alert-drone-${alert.id}`"
            @click="void selection.open(alert.drone_id)"
          >
            {{ droneName(alert.drone_id) }}
          </button>
          <span
            class="mono muted"
            :data-testid="`alert-raised-${alert.id}`"
          >{{ formatTimestamp(alert.raised_at) }}</span>
        </div>
        <!-- Drone text is untrusted: always interpolated, never HTML. -->
        <div :data-testid="`alert-message-${alert.id}`">
          {{ alert.message }}
        </div>
        <div
          v-if="alert.acknowledged_at"
          class="muted"
          :data-testid="`alert-acked-${alert.id}`"
        >
          Acknowledged by {{ ackedBy(alert) }} at {{ formatTimestamp(alert.acknowledged_at) }}
        </div>
        <div
          v-if="alert.cleared_at"
          class="muted"
          :data-testid="`alert-cleared-${alert.id}`"
        >
          Cleared at {{ formatTimestamp(alert.cleared_at) }}
        </div>
        <button
          v-if="canAck && needsAck(alert)"
          type="button"
          class="ack"
          :disabled="alerts.acking.has(alert.id)"
          :aria-label="`Acknowledge ${alert.code} on ${droneName(alert.drone_id)}`"
          :data-testid="`alert-ack-${alert.id}`"
          @click="void alerts.ack(alert.id)"
        >
          Acknowledge
        </button>
        <p
          v-if="alerts.ackError.get(alert.id)"
          class="err"
          role="alert"
          :data-testid="`alert-ack-error-${alert.id}`"
        >
          {{ alerts.ackError.get(alert.id) }}
        </p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.alerts {
  flex: 1;
  min-height: 120px;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
}
.head h2 {
  margin: 0;
  margin-right: auto;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.head label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.note {
  margin: 0;
  padding: 14px;
  color: var(--muted);
}
.err {
  color: var(--crit);
  margin: 0;
}
.list {
  list-style: none;
  margin: 0;
  padding: 8px 14px;
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
  align-items: flex-start;
  gap: 3px;
  overflow-wrap: anywhere;
}
.line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.drone {
  background: none;
  border: none;
  padding: 0;
  color: var(--accent);
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.muted {
  color: var(--muted);
}
</style>
