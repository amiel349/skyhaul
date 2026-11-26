<script setup lang="ts">
import { computed } from "vue";
import { useAuthStore } from "../stores/auth";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import BatteryBar from "./BatteryBar.vue";
import CommandForm from "./CommandForm.vue";
import CommandList from "./CommandList.vue";
import { formatDuration, formatPoint, formatTimestamp, NO_DATA } from "./format";
import StatusChip from "./StatusChip.vue";
import { linkChip } from "./status";

const auth = useAuthStore();
const fleet = useFleetStore();
const selection = useSelectionStore();
const drone = computed(() => (selection.selectedId ? fleet.drones.get(selection.selectedId) : undefined));
const state = computed(() => drone.value?.state ?? null);
const position = computed(() => state.value?.position ?? null);
// Viewers get no command UI at all (issue 44 does the full role-aware work).
const canCommand = computed(() => auth.user !== null && auth.user.role !== "viewer");
const stale = computed(() => state.value?.link === "LOST");
const armed = computed(() => (state.value?.armed == null ? NO_DATA : state.value.armed ? "Yes" : "No"));
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
        v-if="!drone && selection.notFoundId"
        class="empty"
        role="alert"
        data-testid="drone-not-found"
      >
        Drone not found
      </p>
      <p
        v-else-if="!drone && selection.notice"
        class="empty"
        role="alert"
        data-testid="drone-panel-notice"
      >
        {{ selection.notice }}
      </p>
      <p
        v-else-if="!drone"
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
        <label class="follow">
          <input
            type="checkbox"
            :checked="selection.follow"
            data-testid="drone-panel-follow"
            @change="selection.setFollow(($event.target as HTMLInputElement).checked)"
          >
          Follow on map
        </label>
        <p
          v-if="stale"
          class="stale"
          role="status"
          data-testid="drone-panel-stale"
        >
          Link lost. Last seen
          <span
            class="mono"
            data-testid="drone-panel-last-seen"
          >{{ formatTimestamp(state?.last_seen_at) }}</span>.
          Values below may be out of date.
        </p>
        <dl
          class="facts"
          :class="{ stale }"
          :data-stale="stale"
          data-testid="drone-panel-facts"
        >
          <div>
            <dt>Flight state</dt>
            <dd data-testid="drone-panel-flight-state">
              {{ state?.flight_state ?? NO_DATA }}
            </dd>
          </div>
          <div>
            <dt>Armed</dt>
            <dd data-testid="drone-panel-armed">
              {{ armed }}
            </dd>
          </div>
          <div>
            <dt>Battery</dt>
            <dd>
              <BatteryBar
                :pct="state?.battery_pct"
                testid="drone-panel-battery"
              />
            </dd>
          </div>
          <div>
            <dt>Remaining</dt>
            <dd
              class="mono"
              data-testid="drone-panel-remaining"
            >
              {{ formatDuration(state?.est_remaining_s) }}
            </dd>
          </div>
          <div>
            <dt>Link</dt>
            <dd data-testid="drone-panel-link">
              <StatusChip v-bind="linkChip(drone)" />
            </dd>
          </div>
          <div>
            <dt>Last heartbeat</dt>
            <dd
              class="mono"
              data-testid="drone-panel-heartbeat"
            >
              {{ formatTimestamp(state?.last_heartbeat_at) }}
            </dd>
          </div>
          <div>
            <dt>Position</dt>
            <dd
              class="mono"
              data-testid="drone-panel-position"
            >
              {{ formatPoint(position) }}
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
            <dt>Heading</dt>
            <dd
              class="mono"
              data-testid="drone-panel-heading"
            >
              {{ position ? `${position.heading_deg}°` : NO_DATA }}
            </dd>
          </div>
          <div>
            <dt>Speed</dt>
            <dd
              class="mono"
              data-testid="drone-panel-speed"
            >
              {{ position ? `${position.ground_speed_mps} m/s` : NO_DATA }}
            </dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd
              class="mono"
              data-testid="drone-panel-target"
            >
              {{ formatPoint(state?.target) }}
            </dd>
          </div>
          <div>
            <dt>Mission</dt>
            <dd
              class="mono"
              data-testid="drone-panel-mission"
            >
              {{ state?.mission_id ?? NO_DATA }}
            </dd>
          </div>
        </dl>
        <div
          v-if="canCommand"
          data-testid="drone-panel-commands"
        >
          <CommandForm
            :drone-id="drone.id"
            :link-lost="stale"
          />
        </div>
      </div>
    </section>

    <section
      v-if="canCommand"
      class="card"
      aria-labelledby="log-heading"
      data-testid="command-log"
    >
      <h2 id="log-heading">
        Command log
      </h2>
      <div class="log">
        <CommandList
          v-if="drone"
          :drone-id="drone.id"
        />
        <p
          v-else
          class="empty"
          data-testid="command-log-empty"
        >
          No commands yet
        </p>
      </div>
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
.follow {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.stale {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--crit);
  border-radius: 6px;
  color: var(--crit);
}
.facts.stale dd {
  opacity: 0.6;
  font-style: italic;
}
.log {
  padding: 14px;
}
.log .empty {
  padding: 0;
}
</style>
