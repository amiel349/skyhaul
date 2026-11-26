import { alertEndpoints } from "./alerts";
import { authEndpoints } from "./auth";
import { commandEndpoints } from "./commands";
import { droneEndpoints } from "./drones";

export { alertEndpoints, authEndpoints, commandEndpoints, droneEndpoints };
export type { RespondWithOptions } from "./commands";

/** The happy-path handlers of every domain; the browser setup registers them for each test. */
export const fakeEndpoints = [
  ...droneEndpoints.happyPath,
  ...commandEndpoints.happyPath,
  ...alertEndpoints.happyPath,
  ...authEndpoints.happyPath,
];
