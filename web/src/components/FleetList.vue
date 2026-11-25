<script setup lang="ts">
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import BatteryBar from "./BatteryBar.vue";
import StatusChip from "./StatusChip.vue";
import { linkChip } from "./status";

const fleet = useFleetStore();
const selection = useSelectionStore();
</script>

<template>
  <section
    class="card"
    aria-labelledby="fleet-heading"
  >
    <h2 id="fleet-heading">
      Fleet <small data-testid="fleet-count">{{ fleet.list.length }}</small>
    </h2>
    <ul
      class="fleet"
      data-testid="fleet-list"
    >
      <li
        v-for="drone in fleet.list"
        :key="drone.id"
      >
        <button
          type="button"
          class="row"
          :aria-pressed="selection.selectedId === drone.id"
          :data-selected="selection.selectedId === drone.id"
          :data-testid="`fleet-row-${drone.id}`"
          @click="selection.select(drone.id)"
        >
          <span
            class="name"
            :data-testid="`fleet-row-name-${drone.id}`"
          >{{ drone.name }}</span>
          <StatusChip
            v-bind="linkChip(drone)"
            :testid="`fleet-row-chip-${drone.id}`"
          />
          <span class="sub">
            <span
              class="mono"
              :data-testid="`fleet-row-id-${drone.id}`"
            >{{ drone.id }}</span>
            <BatteryBar
              :pct="drone.state?.battery_pct"
              :testid="`fleet-row-battery-${drone.id}`"
            />
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  min-width: 0;
}
h2 {
  margin: 0;
  padding: 11px 14px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}
h2 small {
  font-weight: 400;
  letter-spacing: 0;
}
.fleet {
  list-style: none;
  margin: 0;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 100%;
  overflow-y: auto;
}
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 8px;
  width: 100%;
  text-align: left;
  background: none;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 9px 10px;
  cursor: pointer;
}
.row:hover {
  background: var(--panel-2);
}
.row[aria-pressed="true"] {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.name {
  font-weight: 500;
  overflow-wrap: anywhere;
}
.sub {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: var(--muted);
  font-size: 12px;
  min-width: 0;
}
.sub > .mono {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
