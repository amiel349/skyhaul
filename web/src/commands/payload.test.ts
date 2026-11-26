import { describe, expect, it } from "vitest";
import { COMMAND_TYPES, validatePayload } from "./payload";

describe("command payload validation", () => {
  it("never offers START_MISSION", () => {
    expect(COMMAND_TYPES).not.toContain("START_MISSION");
    expect(COMMAND_TYPES).toHaveLength(6);
  });

  it("blocks empty required fields and numbers outside the contract limits", () => {
    expect(validatePayload("TAKEOFF", { altitude_m: "" })).toEqual({ ok: false, errors: { altitude_m: "Required" } });
    const result = validatePayload("GOTO", { lat: "91", lon: "180", altitude_m: "4", speed_mps: "21" });
    expect(result.ok).toBe(false);
    expect(Object.keys(result.ok ? {} : result.errors).sort()).toEqual(["altitude_m", "lat", "lon", "speed_mps"]);
  });

  it("builds a numeric payload and leaves out an empty optional field", () => {
    expect(validatePayload("GOTO", { lat: "32.1", lon: "34.8", altitude_m: "30", speed_mps: "" })).toEqual({
      ok: true,
      payload: { lat: 32.1, lon: 34.8, altitude_m: 30 },
    });
    expect(validatePayload("ARM", {})).toEqual({ ok: true, payload: {} });
  });
});
