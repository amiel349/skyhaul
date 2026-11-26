// Fake command table behind GET and POST /drones/{id}/commands: a POST shows up in the next GET.
import type { Command } from "../../src/api/types";
import type { Body } from "../api";
import { commandFaketory } from "../faketories";
import { registerReset } from "../reset";

export interface SentRequest {
  droneId: string;
  idempotencyKey: string | null;
  body: Body<"/drones/{drone_id}/commands", "post">;
}

export const commandsDb = {
  commands: [] as Command[],
  /** Every POST the API received, in order (recorded before it is answered). */
  requests: [] as SentRequest[],
  /** The drone id of every GET of a command history. */
  listed: [] as string[],
  add(...commands: Command[]): void {
    this.commands.push(...commands);
  },
  forDrone(droneId: string): Command[] {
    return this.commands.filter((c) => c.drone_id === droneId);
  },
  /** What a 202 stores: a SENT command of the requested type. */
  create(droneId: string, body: SentRequest["body"]): Command {
    const command = commandFaketory.build({
      id: `cmd-${this.requests.length}`,
      drone_id: droneId,
      type: body.type,
      status: "SENT",
    });
    this.commands.push(command);
    return command;
  },
  reset(): void {
    this.commands = [];
    this.requests = [];
    this.listed = [];
  },
};
registerReset(() => commandsDb.reset());
