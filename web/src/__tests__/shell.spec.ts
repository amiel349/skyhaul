import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, vi } from "vitest";
import basemapRaw from "../assets/basemap.geojson?raw";
import FleetView from "../views/FleetView.vue";
import { baseStyle } from "../components/markerStyle";
import { batteryView, linkChip } from "../components/status";
import { useAuthStore } from "../stores/auth";
import { useSelectionStore } from "../stores/selection";
import { THEME_KEY, resolveTheme, useThemeStore } from "../theme/theme";
import { drone, listDrones, liveState, loginOk, server } from "./server";

vi.mock("../realtime/socket", async () => await import("./fakeSocket"));

async function mountView(items: ReturnType<typeof drone>[]) {
  server.use(loginOk, listDrones(items));
  const pinia = createPinia();
  setActivePinia(pinia);
  await useAuthStore().login("a@skyhaul.dev", "pw");
  const view = render(FleetView, { global: { plugins: [pinia] } });
  return { ...view, pinia };
}

const at = (lat: number, lon: number) => ({ lat, lon, altitude_m: 42, heading_deg: 0, ground_speed_mps: 5 });

describe("visual shell", () => {
  it("shows the regions, rows with name, id, chip and battery, and the placeholders", async () => {
    const lost = drone("d2", liveState({ link: "LOST", battery_pct: 12 }));
    await mountView([drone("d1"), lost, drone("d3", null)]);

    expect(await screen.findByTestId("fleet-row-d1")).toBeTruthy();
    expect(screen.getByTestId("fleet-logout")).toBeTruthy();
    expect(screen.getByTestId("connection-badge")).toBeTruthy();
    expect(screen.getByTestId("fleet-user").textContent).toBe("a@skyhaul.dev");
    expect(screen.getByTestId("fleet-row-chip-d1").textContent).toBe("UP");
    expect(screen.getByTestId("fleet-row-chip-d2").textContent).toBe("LOST");
    expect(screen.getByTestId("fleet-row-battery-d2").textContent).toContain("LOW 12%");
    expect(screen.getByTestId("fleet-row-chip-d3").textContent).toBe("NO DATA");
    expect(screen.getByTestId("fleet-row-id-d1").textContent).toBe("d1");

    expect(screen.getByTestId("drone-panel-empty").textContent).toContain("Select a drone");
    expect(screen.getByTestId("alerts-empty").textContent).toContain("No alerts");
    expect(screen.getByTestId("command-log-empty").textContent).toContain("No commands yet");
  });

  it("selects by click and by keyboard, highlights row and marker, and fills the panel", async () => {
    await mountView([
      drone("d1", liveState({ position: at(32.0123, 34.8123), battery_pct: 64 })),
      drone("d2", liveState({ position: at(32.01, 34.81) })),
      drone("d3", null),
    ]);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId("fleet-map").dataset.markerCount).toBe("2"));

    const label = screen.getByTestId("fleet-marker-label-d1");
    expect(label.dataset.lat).toBe("32.0123");
    expect(label.dataset.lon).toBe("34.8123");

    await user.click(screen.getByTestId("fleet-row-d1"));
    expect(screen.getByTestId("fleet-row-d1").dataset.selected).toBe("true");
    expect(screen.getByTestId("fleet-marker-label-d1").dataset.selected).toBe("true");
    const panel = screen.getByTestId("drone-panel");
    expect(within(panel).getByTestId("drone-panel-battery").textContent).toContain("64%");
    expect(within(panel).getByTestId("drone-panel-altitude").textContent).toContain("42 m");
    expect(within(panel).getByTestId("drone-panel-position").textContent).toContain("32.01230, 34.81230");
    expect(within(panel).getByTestId("drone-panel-link").textContent).toContain("UP");
    for (const b of within(screen.getByTestId("drone-panel-commands")).getAllByRole("button")) {
      expect((b as HTMLButtonElement).disabled).toBe(true);
    }

    // Keyboard: Tab to the next row, Enter selects it; Space does the same on the one after.
    screen.getByTestId("fleet-row-d1").focus();
    await user.tab();
    expect(document.activeElement).toBe(screen.getByTestId("fleet-row-d2"));
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("fleet-row-d2").dataset.selected).toBe("true");
    expect(screen.getByTestId("fleet-row-d1").dataset.selected).toBe("false");
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByTestId("fleet-row-d3").dataset.selected).toBe("true");
    expect(screen.getByTestId("drone-panel-position").textContent).toContain("No data");
  });

  it("deselects a drone that is removed from the fleet", async () => {
    const { pinia } = await mountView([drone("d1")]);
    await screen.findByTestId("fleet-row-d1");
    useSelectionStore(pinia).select("d1");
    const fleet = (await import("../stores/fleet")).useFleetStore(pinia);

    fleet.drones.delete("d1");
    fleet.drones.set("d9", { id: "d9", name: "x", state: null }); // triggers the watcher

    await waitFor(() => expect(useSelectionStore(pinia).selectedId).toBeNull());
  });

  it("renders a hostile drone name as text in the list and the panel", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const { container } = await mountView([{ ...drone("d1"), name: hostile }]);
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("fleet-row-d1"));

    expect(screen.getByTestId("fleet-row-name-d1").textContent).toBe(hostile);
    expect(screen.getByTestId("drone-panel-name").textContent).toBe(hostile);
    expect(container.querySelector("img")).toBeNull();
  });

  it("draws the local base layer and loads no tiles in test mode", async () => {
    vi.stubEnv("VITE_TILE_URL", "https://tiles.example/{z}/{x}/{y}.png");
    await mountView([drone("d1")]);

    await waitFor(() => expect(screen.getByTestId("fleet-map").dataset.base).toBe("ready"));

    expect(document.querySelector(".leaflet-tile")).toBeNull();
    expect(screen.queryByTestId("basemap-unavailable")).toBeNull();
    expect(document.querySelector(".leaflet-control-attribution")?.textContent).toContain(
      "© OpenStreetMap contributors (ODbL)",
    );
    vi.unstubAllEnvs();
  });
});

describe("theme", () => {
  it("resolves the attribute over the system setting and ignores unknown values", () => {
    expect(resolveTheme(null, false)).toBe("dark");
    expect(resolveTheme(null, true)).toBe("light");
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", true)).toBe("dark");
    expect(resolveTheme("purple", true)).toBe("light");
  });

  it("toggles on the root element, keeps only the theme name in storage, and the map styles follow the tokens", async () => {
    document.documentElement.removeAttribute("data-theme");
    localStorage.clear();
    const pinia = createPinia();
    setActivePinia(pinia);
    const theme = useThemeStore();
    theme.init();
    expect(theme.resolved).toBe("dark");

    theme.toggle();

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(theme.resolved).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(Object.keys(localStorage)).toEqual([THEME_KEY]);
    expect(sessionStorage.length).toBe(0);

    document.documentElement.style.setProperty("--land", "#111111");
    document.documentElement.style.setProperty("--road", "#222222");
    expect(baseStyle("land").fillColor).toBe("#111111");
    expect(baseStyle("road").color).toBe("#222222");
    document.documentElement.removeAttribute("data-theme");
  });
});

describe("status views", () => {
  it("keeps the battery bar inside 0..100 and says LOW and LOST in words", () => {
    expect(batteryView(0)).toMatchObject({ width: 0, low: true, text: "LOW 0%" });
    expect(batteryView(100)).toMatchObject({ width: 100, low: false, text: "100%" });
    expect(batteryView(140).width).toBe(100);
    expect(batteryView(null).text).toBe("No data");
    expect(linkChip({ id: "d", name: "d", state: liveState({ link: "LOST" }) }).text).toBe("LOST");
  });
});

describe("base map file", () => {
  it("is valid GeoJSON, around the depot, under 100 KB", () => {
    expect(new Blob([basemapRaw]).size).toBeLessThan(100 * 1024);
    const data = JSON.parse(basemapRaw) as {
      type: string;
      features: { properties: { kind: string }; geometry: { coordinates: unknown } }[];
    };
    expect(data.type).toBe("FeatureCollection");
    const kinds = new Set(data.features.map((f) => f.properties.kind));
    expect(kinds).toContain("road");
    expect(kinds).toContain("coast");
    expect(kinds).toContain("land");

    const flat = (c: unknown): number[][] =>
      Array.isArray(c) && typeof c[0] === "number" ? [c as number[]] : (c as unknown[]).flatMap(flat);
    for (const f of data.features) {
      for (const [lon, lat] of flat(f.geometry.coordinates)) {
        expect(lat).toBeGreaterThan(31.97);
        expect(lat).toBeLessThan(32.03);
        expect(lon).toBeGreaterThan(34.73);
        expect(lon).toBeLessThan(34.84);
      }
    }
  });
});
