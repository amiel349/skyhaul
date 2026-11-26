// Fake drone table behind GET /drones and GET /drones/{id}.
import type { Drone } from "../../src/api/types";
import { registerReset } from "../reset";

export const dronesDb = {
  drones: new Map<string, Drone>(),
  add(...drones: Drone[]): void {
    for (const drone of drones) this.drones.set(drone.id, drone);
  },
  get(id: string): Drone | undefined {
    return this.drones.get(id);
  },
  list(): Drone[] {
    return [...this.drones.values()];
  },
  reset(): void {
    this.drones.clear();
  },
};
registerReset(() => dronesDb.reset());
