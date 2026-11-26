import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import area from "../../../contracts/geo/demo-area.v1.json";
import { useSelectionStore } from "./selection";

export interface Point {
  lat: number;
  lon: number;
}

/**
 * Picking the GoTo target on the map. The map and the GoTo form share it: the form starts and
 * cancels pick mode and reads the picked point, the map draws the marker and reports the click.
 */
export const useMapPickStore = defineStore("mapPick", () => {
  const selection = useSelectionStore();
  /** Pick mode: the next click on the map is the target. */
  const active = ref(false);
  /** The last clicked point. A new object per click, so the form reacts to each one. */
  const picked = ref<Point | null>(null);
  /** The marker to draw: the picked point, then whatever valid point the form holds. */
  const target = ref<Point | null>(null);

  /** A target outside the demo area is accepted; the map only warns. The server decides. */
  const outsideArea = computed(() => {
    const t = target.value;
    if (!t) return false;
    const b = area.bounds;
    return t.lat < b.lat_min || t.lat > b.lat_max || t.lon < b.lon_min || t.lon > b.lon_max;
  });

  function start(): void {
    active.value = true;
  }

  function cancel(): void {
    active.value = false;
  }

  function toggle(): void {
    active.value = !active.value;
  }

  /** A click on the map. Not wrapped or clamped: a longitude past 180 fails the form's range check. */
  function pick(lat: number, lon: number): void {
    picked.value = { lat, lon };
    target.value = { lat, lon };
    active.value = false;
  }

  /** The marker follows what the form holds; null removes it. */
  function setTarget(point: Point | null): void {
    target.value = point;
  }

  function reset(): void {
    active.value = false;
    picked.value = null;
    target.value = null;
  }

  // Another drone is another target.
  watch(() => selection.selectedId, reset);

  return { active, picked, target, outsideArea, start, cancel, toggle, pick, setTarget, reset };
});
