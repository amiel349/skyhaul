<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import ConnectionBadge from "../components/ConnectionBadge.vue";
import DronePanel from "../components/DronePanel.vue";
import FleetList from "../components/FleetList.vue";
import FleetMap from "../components/FleetMap.vue";
import ThemeToggle from "../components/ThemeToggle.vue";
import { useAuthStore } from "../stores/auth";
import { useFleetStore } from "../stores/fleet";

const auth = useAuthStore();
const fleet = useFleetStore();

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
      <div class="spacer" />
      <ThemeToggle />
      <span
        class="user"
        data-testid="fleet-user"
      >{{ auth.user?.email }}</span>
      <button
        type="button"
        class="logout"
        data-testid="fleet-logout"
        @click="auth.logout()"
      >
        Log out
      </button>
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
      data-testid="fleet-empty"
    >
      No drones yet
    </p>
    <main class="grid">
      <FleetList class="area-list" />
      <div class="area-map">
        <FleetMap />
        <section
          class="alerts"
          aria-labelledby="alerts-heading"
          data-testid="alerts"
        >
          <h2 id="alerts-heading">
            Alerts
          </h2>
          <p data-testid="alerts-empty">
            No alerts
          </p>
        </section>
      </div>
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
.user {
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.logout {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
}
.logout:hover {
  border-color: var(--accent);
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
  gap: 12px;
  min-width: 0;
}
.alerts {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.alerts h2 {
  margin: 0;
  padding: 11px 14px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}
.alerts p {
  margin: 0;
  padding: 14px;
  color: var(--muted);
}
/* Phone: one column, the list above the map (document order). Desktop: three columns. */
@media (min-width: 1001px) {
  .page {
    height: 100vh;
  }
  .grid {
    flex: 1;
    min-height: 0;
    grid-template-columns: 270px minmax(0, 1fr) 330px;
  }
  .area-list {
    overflow-y: auto;
    min-height: 0;
  }
  .area-map > :first-child {
    flex: 1;
  }
}
</style>
