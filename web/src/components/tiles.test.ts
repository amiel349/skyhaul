import { describe, expect, it } from "vitest";
import { TILES, tileUrlFor } from "./tiles";

describe("tiles", () => {
  it("picks the tile url from the theme, an override for both, and none in test mode", () => {
    expect(tileUrlFor("dark", "development")).toBe(TILES.dark);
    expect(tileUrlFor("light", "development")).toBe(TILES.light);
    expect(TILES.dark).toContain("alidade_smooth_dark");
    expect(tileUrlFor("dark", "development", "https://t.example/{z}")).toBe("https://t.example/{z}");
    expect(tileUrlFor("dark", "test", "https://t.example/{z}")).toBeUndefined();
  });
});
