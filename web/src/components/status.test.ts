import { describe, expect, it } from "vitest";
import { makeLiveState } from "../../tests/factories/live-state";
import { batteryView, linkChip } from "./status";

describe("status views", () => {
  it("keeps the battery bar inside 0..100 and says LOW and LOST in words", () => {
    expect(batteryView(0)).toMatchObject({ width: 0, low: true, critical: true, text: "CRITICAL 0%" });
    expect(batteryView(15)).toMatchObject({ low: true, critical: false, text: "LOW 15%" });
    expect(batteryView(100)).toMatchObject({ width: 100, low: false, text: "100%" });
    expect(batteryView(140).width).toBe(100);
    expect(batteryView(null).text).toBe("No data");
    expect(linkChip({ id: "d", name: "d", state: makeLiveState({ link: "LOST" }) }).text).toBe("LOST");
  });
});
