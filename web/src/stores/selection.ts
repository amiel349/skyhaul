import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { useFleetStore } from "./fleet";

/** Which drone the dispatcher is looking at; the list, the map and the panel all read it. */
export const useSelectionStore = defineStore("selection", () => {
  const fleet = useFleetStore();
  const selectedId = ref<string | null>(null);
  let hadPosition = false;

  function select(id: string | null): void {
    selectedId.value = id;
    hadPosition = !!(id && fleet.drones.get(id)?.state?.position);
  }

  // A selected drone that is removed, or loses its position, is deselected.
  watch(
    () => fleet.list,
    () => {
      if (!selectedId.value) return;
      const drone = fleet.drones.get(selectedId.value);
      const has = !!drone?.state?.position;
      if (!drone || (hadPosition && !has)) selectedId.value = null;
      else hadPosition = has;
    },
  );

  return { selectedId, select };
});
