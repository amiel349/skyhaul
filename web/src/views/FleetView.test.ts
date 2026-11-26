import { createPinia } from "pinia";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../stores/auth";
import { createMemoryHistory, createRouter } from "vue-router";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { makeDrone } from "../testing/factories/drone";
import { fake } from "../testing/fake-socket";
import { worker } from "../testing/worker";
import { fakeDb, fakeEndpoints } from "./FleetView.fakes";
import FleetView from "./FleetView.vue";

vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

beforeEach(() => fake.reset());

// Renders only FleetView, signed in. The panels it lays out are stubs; the splitters are real.
async function renderView(path = "/") {
  worker.use(...fakeEndpoints);
  const pinia = createPinia();
  useAuthStore(pinia).token = "tok-1";
  // The view's own two routes; it keeps the URL and the selection in step.
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "fleet", component: FleetView },
      { path: "/drones/:id", name: "drone", component: FleetView },
    ],
  });
  await router.push(path);
  const stub = (name: string) => ({ template: `<div data-testid="${name}-stub" />` });
  const view = await render(FleetView, {
    global: {
      plugins: [pinia, router],
      stubs: {
        ConnectionBadge: stub("connection-badge"),
        UserMenu: stub("user-menu"),
        FleetList: stub("fleet-list"),
        FleetMap: stub("fleet-map"),
        DronePanel: stub("drone-panel"),
      },
    },
  });
  return { pinia, view, router };
}

const bar = (id: string) => page.getByTestId(id);
const focus = (id: string) => (bar(id).element() as HTMLElement).focus();

describe("FleetView", () => {
  it("loads the drones and opens the live socket on mount, and closes it on leaving", async () => {
    fakeDb.add(makeDrone({ id: "d1" }), makeDrone({ id: "d2" }));
    const { pinia, view } = await renderView();

    await expect.poll(() => useFleetStore(pinia).list.length).toBe(2);
    expect(fake.open).toBe(true);

    await view.unmount();
    expect(fake.open).toBe(false);
  });

  it("puts the selected drone in the URL", async () => {
    fakeDb.add(makeDrone({ id: "d1" }));
    const { pinia, router } = await renderView();
    await expect.poll(() => useFleetStore(pinia).list.length).toBe(1);

    useSelectionStore(pinia).select("d1");

    await expect.poll(() => router.currentRoute.value.fullPath).toBe("/drones/d1");
  });

  it("selects the drone named in the URL, loading it by id when the list does not have it yet", async () => {
    fakeDb.add(makeDrone({ id: "d1", name: "Alpha" }));
    const { pinia } = await renderView("/drones/d1");

    await expect.poll(() => useSelectionStore(pinia).selectedId).toBe("d1");
    expect(useFleetStore(pinia).drones.get("d1")?.name).toBe("Alpha");
  });

  it("flags an id in the URL that the API does not know", async () => {
    const { pinia, router } = await renderView("/drones/nope");

    await expect.poll(() => useSelectionStore(pinia).notFoundId).toBe("nope");
    expect(router.currentRoute.value.fullPath).toBe("/drones/nope");
  });

  it("says so when there are no drones, and shows the empty alerts placeholder", async () => {
    await renderView();

    await expect.element(page.getByTestId("fleet-empty")).toHaveTextContent("No drones yet");
    await expect.element(page.getByTestId("alerts-empty")).toHaveTextContent("No alerts");
  });

  it("says so when the drones cannot be loaded", async () => {
    fakeDb.failing = true;
    await renderView();

    await expect.element(page.getByTestId("fleet-error")).toHaveTextContent("Could not load the drones");
  });

  it("resizes a panel with the keyboard within its limits, and Home, End and Enter jump and reset", async () => {
    await renderView();
    focus("splitter-fleet-map");
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", "270");

    await userEvent.keyboard("{ArrowRight}");
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", "286");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", "222");
    await userEvent.keyboard("{Home}{ArrowLeft}");
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", "200");
    await userEvent.keyboard("{End}");
    const max = bar("splitter-fleet-map").element().getAttribute("aria-valuemax") ?? "";
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", max);
    await userEvent.keyboard("{Enter}");
    await expect.element(bar("splitter-fleet-map")).toHaveAttribute("aria-valuenow", "270");

    // The right panel grows when its divider moves left.
    focus("splitter-map-panel");
    await userEvent.keyboard("{ArrowLeft}");
    await expect.element(bar("splitter-map-panel")).toHaveAttribute("aria-valuenow", "346");
  });

  it("changes the map height with a pointer drag", async () => {
    await renderView();
    await expect.element(page.getByTestId("fleet-empty")).toBeInTheDocument(); // the layout has settled
    const handle = bar("splitter-map-alerts");
    await expect.element(handle).toHaveAttribute("aria-valuenow", "420");
    const rect = handle.element().getBoundingClientRect();
    // A dot 50px above the bar's centre, placed in page coordinates so it stays put if the page scrolls.
    const target = document.createElement("div");
    target.style.cssText = `position: absolute; width: 2px; height: 2px; left: ${rect.left + window.scrollX + rect.width / 2}px; top: ${rect.top + window.scrollY + rect.height / 2 - 51}px`; // the dot is 2px: its centre is 1px below its top
    document.body.append(target);

    await userEvent.dragAndDrop(handle, target);
    target.remove();

    await expect.element(handle).toHaveAttribute("aria-valuenow", "370");
  });
});
