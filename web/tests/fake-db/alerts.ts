// Fake alert table behind GET /alerts and POST /alerts/{id}/ack: an ack shows up in the next GET.
import type { Alert } from "../../src/api/types";
import { registerReset } from "../reset";

/** Who an acknowledgement is recorded for: the default faketory user, so "you" in the UI. */
export const ACKER_ID = "00000000-0000-4000-8000-000000000001";

export const alertsDb = {
  alerts: [] as Alert[],
  /** The query string of every GET /alerts, in order. */
  listed: [] as URLSearchParams[],
  /** The id of every POST /alerts/{id}/ack the API received. */
  acked: [] as string[],
  add(...alerts: Alert[]): void {
    this.alerts.push(...alerts);
  },
  find(id: string): Alert | undefined {
    return this.alerts.find((a) => a.id === id);
  },
  /** Idempotent: the first acknowledger stays. */
  acknowledge(id: string, by: string = ACKER_ID): Alert | undefined {
    const alert = this.find(id);
    if (alert && !alert.acknowledged_at) {
      alert.acknowledged_by = by;
      alert.acknowledged_at = "2026-01-01T00:05:00Z";
    }
    return alert;
  },
  reset(): void {
    this.alerts = [];
    this.listed = [];
    this.acked = [];
  },
};
registerReset(() => alertsDb.reset());
