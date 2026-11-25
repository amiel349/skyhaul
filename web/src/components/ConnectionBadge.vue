<script setup lang="ts">
import { useConnectionStore } from "../stores/connection";

const connection = useConnectionStore();
</script>

<template>
  <div class="conn">
    <span
      class="badge"
      data-testid="connection-badge"
      :data-status="connection.status"
    ><i aria-hidden="true" />{{ connection.status }}</span>
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
.conn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--muted);
}
.badge i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}
[data-status="live"] i {
  background: var(--ok);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent);
}
[data-status="reconnecting"] i {
  background: var(--warn);
}
.banner {
  margin: 0;
  padding: 1px 8px;
  border: 1px dashed var(--warn);
  border-radius: 4px;
  color: var(--warn);
  font-size: 12px;
}
</style>
