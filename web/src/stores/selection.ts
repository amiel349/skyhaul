import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { useFleetStore } from "./fleet";

/** Which drone the dispatcher is looking at; the list, the map and the panel all read it. */
export const useSelectionStore = defineStore("selection", () => {
  const fleet = useFleetStore();
  const selectedId = ref<string | null>(null);
  /** The map keeps the selected drone centred. Off whenever the selection changes. */
  const follow = ref(false);
  /** Why the panel is closed, when it did not close by the dispatcher's choice. */
  const notice = ref<string | null>(null);
  /** The id from the URL that the API does not know (404). */
  const notFoundId = ref<string | null>(null);
  let hadPosition = false;

  function select(id: string | null): void {
    if (id !== selectedId.value) follow.value = false;
    selectedId.value = id;
    notice.value = null;
    notFoundId.value = null;
    hadPosition = !!(id && fleet.drones.get(id)?.state?.position);
  }

  function setFollow(on: boolean): void {
    follow.value = on && selectedId.value !== null;
  }

  /** Select a drone by id from the URL; load it first if the list does not have it yet. */
  async function open(id: string): Promise<void> {
    if (selectedId.value === id) return;
    if (!fleet.drones.has(id)) {
      const result = await fleet.loadOne(id);
      if (result !== "ok") {
        select(null);
        if (result === "not_found") notFoundId.value = id;
        else notice.value = "Could not load the drone";
        return;
      }
    }
    select(id);
  }

  // A selected drone that is removed, or loses its position, is deselected.
  watch(
    () => fleet.list,
    () => {
      if (!selectedId.value) return;
      const id = selectedId.value;
      const drone = fleet.drones.get(id);
      const has = !!drone?.state?.position;
      if (!drone) {
        select(null);
        notice.value = `Drone ${id} was removed`;
      } else if (hadPosition && !has) {
        select(null);
      } else hadPosition = has;
    },
  );

  return { selectedId, follow, notice, notFoundId, select, setFollow, open };
});
