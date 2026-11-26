import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { useFleetStore, type FleetDrone } from "./fleet";

export type LinkFilter = "ALL" | "UP" | "LOST";

/** Pure selector: name or id contains the text (case-insensitive), and the link matches. */
export function filterDrones(drones: FleetDrone[], query: string, link: LinkFilter): FleetDrone[] {
  const needle = query.trim().toLowerCase();
  return drones.filter(
    (drone) =>
      (link === "ALL" || drone.state?.link === link) &&
      (!needle || drone.name.toLowerCase().includes(needle) || drone.id.toLowerCase().includes(needle)),
  );
}

/** What the list header asks for. The fleet store stays the single source of drones. */
export const useFleetFilterStore = defineStore("fleetFilter", () => {
  const fleet = useFleetStore();
  const query = ref("");
  const link = ref<LinkFilter>("ALL");
  const visible = computed(() => filterDrones(fleet.list, query.value, link.value));
  return { query, link, visible };
});
