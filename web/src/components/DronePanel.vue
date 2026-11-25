<script setup lang="ts">
import { computed } from "vue";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import BatteryBar from "./BatteryBar.vue";
import StatusChip from "./StatusChip.vue";
import { linkChip } from "./status";

const fleet = useFleetStore();
const selection = useSelectionStore();
const drone = computed(() => (selection.selectedId ? fleet.drones.get(selection.selectedId) : undefined));
const position = computed(() => drone.value?.state?.position ?? null);

const NO_DATA = "No data";
</script>

<template>
  <div class="col">
    <section
      class="card"
      aria-labelledby="drone-heading"
      data-testid="drone-panel"
    >
      <h2 id="drone-heading">
        Drone
      </h2>
      <p
        v-if="!drone"
        class="empty"
        data-testid="drone-panel-empty"
      >
        Select a drone
      </p>
      <div
        v-else
        class="detail"
      >
        <div class="head">
          <strong data-testid="drone-panel-name">{{ drone.name }}</strong>
          <span
            class="mono muted"
            data-testid="drone-panel-id"
          >{{ drone.id }}</span>
        </div>
        <dl class="facts">
          <div>
            <dt>Battery</dt>
            <dd data-testid="drone-panel-battery">
              <BatteryBar :pct="drone.state?.battery_pct" />
            </dd>
          </div>
          <div>
            <dt>Link</dt>
            <dd data-testid="drone-panel-link">
              <StatusChip v-bind="linkChip(drone)" />
            </dd>
          </div>
          <div>
            <dt>Altitude</dt>
            <dd
              class="mono"
              data-testid="drone-panel-altitude"
            >
              {{ position ? `${position.altitude_m} m` : NO_DATA }}
            </dd>
          </div>
          <div>
            <dt>Position</dt>
            <dd
              class="mono"
              data-testid="drone-panel-position"
            >
              {{ position ? `${position.lat.toFixed(5)}, ${position.lon.toFixed(5)}` : NO_DATA }}
            </dd>
          </div>
        </dl>
        <div
          class="btns"
          role="group"
          aria-label="Drone commands"
          data-testid="drone-panel-commands"
        >
          <button
            v-for="name in ['Take off', 'Return home', 'Land']"
            :key="name"
            type="button"
            class="btn"
            disabled
            :data-testid="`drone-panel-cmd-${name.toLowerCase().replace(' ', '-')}`"
          >
            {{ name }}
          </button>
        </div>
        <p
          class="muted"
          data-testid="drone-panel-commands-note"
        >
          Commands arrive in a later release
        </p>
      </div>
    </section>

    <section
      class="card"
      aria-labelledby="log-heading"
      data-testid="command-log"
    >
      <h2 id="log-heading">
        Command log
      </h2>
      <p
        class="empty"
        data-testid="command-log-empty"
      >
        No commands yet
      </p>
    </section>
  </div>
</template>

<style scoped>
.col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
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
.empty,
.muted {
  color: var(--muted);
  margin: 0;
}
.empty {
  padding: 14px;
}
.detail {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.head {
  display: flex;
  flex-direction: column;
  overflow-wrap: anywhere;
}
.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin: 0;
}
.facts div {
  min-width: 0;
}
dt {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
dd {
  margin: 0;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.btns {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.btn {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 12px;
  font-weight: 500;
}
.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
