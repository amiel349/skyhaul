<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import ConnectionBadge from "../components/ConnectionBadge.vue";
import FleetMap from "../components/FleetMap.vue";
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
    <header>
      <h1>Fleet</h1>
      <ConnectionBadge />
      <span data-testid="fleet-user">{{ auth.user?.email }}</span>
      <button
        type="button"
        data-testid="fleet-logout"
        @click="auth.logout()"
      >
        Log out
      </button>
    </header>
    <p
      v-if="fleet.loaded.failed"
      role="alert"
      data-testid="fleet-error"
    >
      Could not load the drones
    </p>
    <p
      v-else-if="fleet.loaded.done && fleet.list.length === 0"
      data-testid="fleet-empty"
    >
      No drones yet
    </p>
    <FleetMap />
  </div>
</template>

<style scoped>
.page {
  display: grid;
  grid-template-rows: auto auto 1fr;
  height: 100vh;
}
header {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.5rem 1rem;
}
h1 {
  margin: 0;
  font-size: 1.25rem;
}
header button {
  margin-left: auto;
  padding: 0.4rem 0.8rem;
  font: inherit;
}
</style>
