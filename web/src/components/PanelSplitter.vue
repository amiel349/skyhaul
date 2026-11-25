<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{
  modelValue: number;
  min: number;
  max: number;
  label: string;
  // "vertical" is a vertical bar (drag left/right); "horizontal" is a horizontal bar (drag up/down).
  orientation: "vertical" | "horizontal";
  // Set for the right panel only: moving the bar left makes the controlled size bigger.
  invert?: boolean;
  testid: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: number]; reset: [] }>();

const STEP = 16;
const BIG_STEP = 64;
const dragging = ref(false);
let startPos = 0;
let startValue = 0;

const axis = (e: { clientX: number; clientY: number }): number =>
  props.orientation === "vertical" ? e.clientX : e.clientY;
const sign = (): number => (props.invert ? -1 : 1);

function onPointerDown(e: PointerEvent): void {
  if (e.button > 0) return;
  dragging.value = true;
  startPos = axis(e);
  startValue = props.modelValue;
  (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  document.body.classList.add("is-resizing");
}
function onPointerMove(e: PointerEvent): void {
  if (!dragging.value) return;
  emit("update:modelValue", startValue + sign() * (axis(e) - startPos));
}
function stop(): void {
  dragging.value = false;
  document.body.classList.remove("is-resizing");
}

function onKeydown(e: KeyboardEvent): void {
  const step = e.shiftKey ? BIG_STEP : STEP;
  const [less, more] = props.orientation === "vertical" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
  let next: number;
  if (e.key === more) next = props.modelValue + sign() * step;
  else if (e.key === less) next = props.modelValue - sign() * step;
  else if (e.key === "Home") next = props.min;
  else if (e.key === "End") next = props.max;
  else if (e.key === "Enter") {
    e.preventDefault();
    emit("reset");
    return;
  } else return;
  e.preventDefault();
  emit("update:modelValue", next);
}
</script>

<template>
  <div
    class="splitter"
    :class="[orientation, { dragging }]"
    role="separator"
    tabindex="0"
    :aria-orientation="orientation"
    :aria-label="label"
    :aria-valuenow="modelValue"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :data-testid="testid"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="stop"
    @pointercancel="stop"
    @keydown="onKeydown"
    @dblclick="emit('reset')"
  />
</template>

<style scoped>
.splitter {
  position: relative;
  flex: none;
  touch-action: none;
  user-select: none;
  border-radius: 4px;
}
.splitter.vertical {
  cursor: col-resize;
}
.splitter.horizontal {
  cursor: row-resize;
  height: 12px;
}
/* A thin grip line in the middle of the 12px gutter. */
.splitter::after {
  content: "";
  position: absolute;
  background: var(--line);
  border-radius: 2px;
  transition: background 0.12s;
}
.splitter.vertical::after {
  inset: 0 5px;
}
.splitter.horizontal::after {
  inset: 5px 0;
}
.splitter:hover::after,
.splitter.dragging::after,
.splitter:focus-visible::after {
  background: var(--accent);
}
.splitter:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -1px;
}
</style>
