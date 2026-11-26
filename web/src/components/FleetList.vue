<script setup lang="ts">
import { useFleetStore } from "../stores/fleet";
import { useFleetFilterStore, type LinkFilter } from "../stores/fleetFilter";
import { useSelectionStore } from "../stores/selection";
import BatteryBar from "./BatteryBar.vue";
import { NO_DATA } from "./format";
import StatusChip from "./StatusChip.vue";
import { linkChip } from "./status";

const fleet = useFleetStore();
const filter = useFleetFilterStore();
const selection = useSelectionStore();
const LINKS: LinkFilter[] = ["ALL", "UP", "LOST"];
</script>

<template>
  <section
    class="card"
    aria-labelledby="fleet-heading"
  >
    <h2 id="fleet-heading">
      Fleet <small data-testid="fleet-count">{{ filter.visible.length }}</small>
    </h2>
    <div class="filters">
      <input
        v-model="filter.query"
        type="search"
        class="search"
        placeholder="Search name or id"
        aria-label="Search drones by name or id"
        data-testid="fleet-search"
      >
      <select
        v-model="filter.link"
        class="select"
        aria-label="Filter by link"
        data-testid="fleet-filter-link"
      >
        <option
          v-for="option in LINKS"
          :key="option"
          :value="option"
        >
          {{ option === "ALL" ? "All links" : option }}
        </option>
      </select>
    </div>
    <p
      v-if="fleet.list.length > 0 && filter.visible.length === 0"
      class="none"
      data-testid="fleet-no-match"
    >
      No drones match
    </p>
    <ul
      class="fleet"
      data-testid="fleet-list"
    >
      <li
        v-for="drone in filter.visible"
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
            <span
              class="state"
              :data-testid="`fleet-row-state-${drone.id}`"
            >{{ drone.state?.flight_state ?? NO_DATA }}</span>
          </span>
          <span class="sub">
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
.filters {
  display: flex;
  gap: 6px;
  padding: 8px 6px 2px;
}
.search,
.select {
  background: var(--panel-2);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px 8px;
  font: inherit;
  min-width: 0;
}
.search {
  flex: 1;
}
.none {
  margin: 0;
  padding: 10px 14px;
  color: var(--muted);
}
.state {
  font-size: 11px;
  letter-spacing: 0.04em;
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
