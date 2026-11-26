import { defineStore } from "pinia";
import { computed, shallowReactive } from "vue";
import { api } from "../api/client";
import type { Drone, DroneLiveState, DroneStateEvent } from "../api/types";
import { closeFleetSocket, openFleetSocket } from "../realtime/socket";
import { useAuthStore } from "./auth";
import { useCommandsStore } from "./commands";
import { useConnectionStore } from "./connection";

/** A drone as the map needs it; `name` falls back to the id until the registry answers. */
export interface FleetDrone {
  id: string;
  name: string;
  state: DroneLiveState | null;
}

function stamp(state: DroneLiveState | null): number {
  const ts = state?.ts ? Date.parse(state.ts) : NaN;
  return Number.isNaN(ts) ? -Infinity : ts;
}

export const useFleetStore = defineStore("fleet", () => {
  const drones = shallowReactive(new Map<string, FleetDrone>());
  const loaded = shallowReactive({ done: false, failed: false });
  // Sorted by id, so a state change or a new drone never makes the list order jump.
  const list = computed(() => [...drones.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)));
  let hasConnected = false;

  /** Keep a state only if it is not older than the stored one (events may arrive out of order). */
  function applyState(event: DroneStateEvent): void {
    const known = drones.get(event.drone_id);
    if (!known) {
      drones.set(event.drone_id, { id: event.drone_id, name: event.drone_id, state: event.state });
      void fetchName(event.drone_id);
      return;
    }
    if (stamp(event.state) < stamp(known.state)) return;
    drones.set(known.id, { ...known, state: event.state });
  }

  async function fetchName(id: string): Promise<void> {
    try {
      const { data } = await api.GET("/drones/{drone_id}", { params: { path: { drone_id: id } } });
      const current = drones.get(id);
      if (data && current) drones.set(id, { ...current, name: data.name });
    } catch {
      // The marker already shows the id; a missing name is not worth an error.
    }
  }

  /** Load one drone that is not in the list yet (a deep link). */
  async function loadOne(id: string): Promise<"ok" | "not_found" | "error"> {
    try {
      const { data, response } = await api.GET("/drones/{drone_id}", { params: { path: { drone_id: id } } });
      if (!data) return response.status === 404 ? "not_found" : "error";
      const kept = drones.get(id)?.state ?? null;
      drones.set(id, { id: data.id, name: data.name, state: stamp(kept) > stamp(data.state) ? kept : data.state });
      return "ok";
    } catch {
      return "error";
    }
  }

  /** REST is the source of truth: replace our state with the server's, following next_cursor. */
  async function loadAll(): Promise<void> {
    const items: Drone[] = [];
    let cursor: string | undefined;
    try {
      do {
        const { data } = await api.GET("/drones", { params: { query: { limit: 200, cursor } } });
        if (!data) {
          loaded.failed = true;
          return;
        }
        items.push(...data.items);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
    } catch {
      loaded.failed = true;
      return;
    }
    const previous = new Map(drones);
    drones.clear();
    for (const item of items) {
      const kept = previous.get(item.id)?.state ?? null;
      // An event that already arrived may be newer than the page.
      const state = stamp(kept) > stamp(item.state) ? kept : item.state;
      drones.set(item.id, { id: item.id, name: item.name, state });
    }
    loaded.done = true;
    loaded.failed = false;
  }

  function startRealtime(): void {
    const auth = useAuthStore();
    const connection = useConnectionStore();
    hasConnected = false;
    connection.set("connecting");
    openFleetSocket(() => auth.token, {
      onConnect: () => {
        connection.set("live");
        if (hasConnected) void loadAll(); // events sent while we were away are gone
        hasConnected = true;
      },
      onDisconnect: () => connection.set("reconnecting"),
      onDroneState: applyState,
      onCommandUpdated: (event) => useCommandsStore().applyUpdate(event),
      onAuthError: () => auth.logout("expired"),
    });
  }

  function stopRealtime(): void {
    closeFleetSocket();
    useConnectionStore().set("offline");
  }

  function clear(): void {
    drones.clear();
    loaded.done = false;
    loaded.failed = false;
    hasConnected = false;
  }

  return { drones, list, loaded, applyState, loadAll, loadOne, startRealtime, stopRealtime, clear };
});
