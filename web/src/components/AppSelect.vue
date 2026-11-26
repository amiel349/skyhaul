<script setup lang="ts">
import { computed, useAttrs, useId } from "vue";

export interface SelectOption {
  value: string;
  label: string;
}

defineOptions({ inheritAttrs: false });

const props = defineProps<{
  modelValue: string;
  options: SelectOption[];
  label: string;
  id?: string;
  disabled?: boolean;
  /** Keep the label for screen readers only (a compact filter row). It stays tied to the control. */
  hideLabel?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const attrs = useAttrs();
const autoId = useId();
const selectId = computed(() => props.id ?? autoId);
// class and style belong to the wrapper; everything else (data-testid, aria-*) goes to the native select.
const rootAttrs = computed(() => ({ class: attrs.class, style: attrs.style }));
const selectAttrs = computed(() =>
  Object.fromEntries(Object.entries(attrs).filter(([name]) => name !== "class" && name !== "style")),
);

function onChange(event: Event): void {
  emit("update:modelValue", (event.target as HTMLSelectElement).value);
}
</script>

<template>
  <div
    class="field"
    v-bind="rootAttrs"
  >
    <label
      :for="selectId"
      :class="{ sr: hideLabel }"
    >{{ label }}</label>
    <select
      :id="selectId"
      class="select"
      :value="modelValue"
      :disabled="disabled"
      v-bind="selectAttrs"
      @change="onChange"
    >
      <option
        v-for="option in options"
        :key="option.value"
        :value="option.value"
      >
        {{ option.label }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
label {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.select {
  appearance: none;
  height: 32px;
  width: 100%;
  min-width: 0;
  padding: 0 28px 0 10px;
  font: inherit;
  color: var(--ink);
  background-color: var(--panel-2);
  /* The chevron is two triangles drawn in the muted token, so it follows the theme. */
  background-image:
    linear-gradient(45deg, transparent 50%, var(--muted) 50%),
    linear-gradient(135deg, var(--muted) 50%, transparent 50%);
  background-position:
    calc(100% - 15px) 50%,
    calc(100% - 10px) 50%;
  background-size: 5px 5px;
  background-repeat: no-repeat;
  border: 1px solid var(--line);
  border-radius: 6px;
  cursor: pointer;
  text-overflow: ellipsis;
  white-space: nowrap;
  overflow: hidden;
}
.select:hover:not(:disabled) {
  border-color: var(--muted);
}
.select:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
