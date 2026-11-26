<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AlertBadge from "../components/AlertBadge.vue";
import AlertList from "../components/AlertList.vue";
import ConnectionBadge from "../components/ConnectionBadge.vue";
import DronePanel from "../components/DronePanel.vue";
import FleetList from "../components/FleetList.vue";
import FleetMap from "../components/FleetMap.vue";
import PanelSplitter from "../components/PanelSplitter.vue";
import UserMenu from "../components/UserMenu.vue";
import { useResizable } from "../layout/useResizable";
import { useAuthStore } from "../stores/auth";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";

const auth = useAuthStore();
const fleet = useFleetStore();
const selection = useSelectionStore();
const route = useRoute();
const router = useRouter();
const { sizes, limits, set, reset } = useResizable();

// The URL and the selection follow each other: /drones/:id selects, selecting updates the URL.
const routeId = () => (typeof route.params.id === "string" ? route.params.id : null);
watch(
  routeId,
  (id) => {
    if (id) void selection.open(id);
    else if (selection.selectedId) selection.select(null);
  },
  { immediate: true },
);
watch(
  () => selection.selectedId,
  (id) => {
    if (id && id !== routeId()) void router.push({ name: "drone", params: { id } });
    // Closed (deselected or removed) while the URL names a drone; an unknown id keeps its URL.
    else if (!id && routeId() && !selection.notFoundId) void router.replace({ name: "fleet" });
  },
);

onMounted(() => {
  void fleet.loadAll();
  fleet.startRealtime();
});

// Leaving the page (logout already did it too) must not keep a socket open.
onBeforeUnmount(() => auth.token && fleet.stopRealtime());
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div class="brand">
        Sky<span>haul</span>
      </div>
      <ConnectionBadge />
      <AlertBadge />
      <div class="spacer" />
      <UserMenu />
    </header>
    <p
      v-if="fleet.loaded.failed"
      role="alert"
      class="msg"
      data-testid="fleet-error"
    >
      Could not load the drones
    </p>
    <p
      v-else-if="fleet.loaded.done && fleet.list.length === 0"
      class="msg"
      role="status"
      data-testid="fleet-empty"
    >
      No drones yet
    </p>
    <main
      class="grid"
      :style="{ '--fleet-w': sizes.fleet + 'px', '--panel-w': sizes.panel + 'px', '--map-h': sizes.map + 'px' }"
    >
      <FleetList class="area-list" />
      <PanelSplitter
        class="split-v"
        :model-value="sizes.fleet"
        :min="limits.fleet.min"
        :max="limits.fleet.max"
        orientation="vertical"
        label="Resize fleet list"
        testid="splitter-fleet-map"
        @update:model-value="set('fleet', $event)"
        @reset="reset('fleet')"
      />
      <div class="area-map">
        <FleetMap />
        <PanelSplitter
          :model-value="sizes.map"
          :min="limits.map.min"
          :max="limits.map.max"
          orientation="horizontal"
          label="Resize map and alerts"
          testid="splitter-map-alerts"
          @update:model-value="set('map', $event)"
          @reset="reset('map')"
        />
        <AlertList />
      </div>
      <PanelSplitter
        class="split-v"
        :model-value="sizes.panel"
        :min="limits.panel.min"
        :max="limits.panel.max"
        orientation="vertical"
        invert
        label="Resize drone panel"
        testid="splitter-map-panel"
        @update:model-value="set('panel', $event)"
        @reset="reset('panel')"
      />
      <DronePanel class="area-panel" />
    </main>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  min-height: 100vh;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.brand {
  font-weight: 600;
  font-size: 16px;
}
.brand span {
  color: var(--accent);
}
.spacer {
  flex: 1;
}
.msg {
  margin: 0;
  color: var(--muted);
}
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
}
.area-map {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.area-map > :first-child {
  flex: none;
  height: var(--map-h);
}
.split-v {
  display: none;
}
/* Phone: one column, the list above the map (document order). Desktop: three columns. */
@media (min-width: 1001px) {
  .page {
    height: 100vh;
  }
  .grid {
    flex: 1;
    min-height: 0;
    gap: 0;
    grid-template-columns: var(--fleet-w) 12px minmax(0, 1fr) 12px var(--panel-w);
  }
  .split-v {
    display: block;
  }
  .area-list {
    overflow-y: auto;
    min-height: 0;
  }
}
</style>
