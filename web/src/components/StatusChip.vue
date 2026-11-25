<script setup lang="ts">
import type { Tone } from "./status";

defineProps<{ text: string; tone: Tone; testid?: string }>();
</script>

<template>
  <span
    class="chip"
    :class="tone"
    :data-tone="tone"
    :data-testid="testid"
  >{{ text }}</span>
</template>

<style scoped>
.chip {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 4px;
  border: 1px solid var(--line);
  color: var(--muted);
  white-space: nowrap;
}
.ok {
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 45%, transparent);
}
/* Dashed border: the LOST state does not rely on colour alone. */
.crit {
  color: var(--crit);
  border-color: var(--crit);
  border-style: dashed;
}
</style>
