<script setup lang="ts">
import { useConnectionStore } from "../stores/connection";

const connection = useConnectionStore();
</script>

<template>
  <div>
    <span
      data-testid="connection-badge"
      :data-status="connection.status"
    >
      {{ connection.status }}
    </span>
    <p
      v-if="connection.status === 'reconnecting'"
      role="status"
      class="banner"
      data-testid="connection-reconnecting"
    >
      Reconnecting
    </p>
  </div>
</template>

<style scoped>
.banner {
  margin: 0;
  padding: 0.25rem 0.75rem;
  background: #fef3c7;
}
</style>
