import { commandsDb } from "./commands";
import { dronesDb } from "./drones";
import { usersDb } from "./users";

export type { SentRequest } from "./commands";

/** Every fake table. Each one registers its own reset(), which the browser setup runs after each test. */
export const fakeDb = { drones: dronesDb, commands: commandsDb, users: usersDb };
