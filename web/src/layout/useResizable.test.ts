import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { LAYOUT_KEY, readSizes, useResizable } from "./useResizable";

afterEach(() => localStorage.clear());

// useResizable hooks into the component lifecycle, so it runs inside a tiny host.
function host() {
  let api!: ReturnType<typeof useResizable>;
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useResizable();
        return () => null;
      },
    }),
  );
  return { api, wrapper };
}

describe("useResizable", () => {
  it("keeps sizes within limits, and reset goes back to the default", () => {
    const { api } = host();

    api.set("fleet", 10);
    expect(api.sizes.fleet).toBe(api.limits.value.fleet.min);
    api.set("fleet", 9999);
    expect(api.sizes.fleet).toBe(api.limits.value.fleet.max);
    api.reset("fleet");
    expect(api.sizes.fleet).toBe(270);
  });

  it("saves sizes, restores them, and ignores bad stored data", async () => {
    const first = host();
    first.api.set("map", 404);
    await vi.waitFor(() =>
      expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}")).toEqual({ fleet: 270, panel: 330, map: 404 }),
    );
    expect(readSizes().map).toBe(404);

    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ fleet: "wide", panel: 9999, map: 1 }));
    expect(readSizes()).toEqual({ fleet: 270, panel: 330, map: 420 });
  });
});
