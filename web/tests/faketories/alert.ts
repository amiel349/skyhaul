import type { Alert } from "../../src/api/types";

export const alertFaketory = {
  build(over: Partial<Alert> = {}): Alert {
    return {
      id: "00000000-0000-4000-8000-0000000000a1",
      drone_id: "d1",
      code: "LOW_BATTERY",
      severity: "WARNING",
      message: "Battery below 20%",
      value: 19,
      source: "DRONE",
      raised_at: "2026-01-01T00:00:00Z",
      acknowledged_by: null,
      acknowledged_at: null,
      cleared_at: null,
      ...over,
    };
  },
};
