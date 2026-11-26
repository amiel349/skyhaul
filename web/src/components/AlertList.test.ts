import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { alertFaketory, droneFaketory } from "../../tests/faketories";
import { fakeDb } from "../../tests/fake-db";
import { alertEndpoints } from "../../tests/fake-endpoints";
import { renderComponent } from "../../tests/render";
import { worker } from "../../tests/worker";
import { useAlertsStore } from "../stores/alerts";
import { useSelectionStore } from "../stores/selection";
import AlertList from "./AlertList.vue";

const A1 = "00000000-0000-4000-8000-0000000000a1";
const A2 = "00000000-0000-4000-8000-0000000000a2";
const A3 = "00000000-0000-4000-8000-0000000000a3";

const render = (role: "dispatcher" | "viewer" = "dispatcher") => renderComponent(AlertList, { user: { role } });
const rows = () => page.getByRole("listitem");

describe("AlertList", () => {
  it("loads the open alerts and lists severity, code, drone, message and raised time", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1, drone_id: "d1", message: "Battery below 20%" }));

    await render();

    await expect.element(page.getByText("WARNING", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByText("LOW_BATTERY", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByRole("button", { name: "Open drone d1" })).toBeInTheDocument();
    await expect.element(page.getByText("Battery below 20%")).toBeInTheDocument();
    await expect.element(page.getByText("2026-01-01 00:00:00 UTC")).toBeInTheDocument();
    expect(fakeDb.alerts.listed[0]?.get("open")).toBe("true");
  });

  it("shows the drone id for an alert whose drone is not in the fleet", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1, drone_id: "ghost-9" }));

    await render();

    await expect.element(page.getByRole("button", { name: "Open drone ghost-9" })).toBeInTheDocument();
  });

  it("puts a raised alert at the top without a reload", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1, message: "Old one", raised_at: "2026-01-01T00:00:00Z" }));
    const { pinia } = await render();
    await expect.element(page.getByText("Old one")).toBeInTheDocument();

    useAlertsStore(pinia).applyUpdate(
      alertFaketory.build({ id: A2, message: "Fresh one", raised_at: "2026-01-01T00:10:00Z" }),
    );

    await expect.element(rows().first()).toMatchTextContent("Fresh one");
  });

  it("changes a row in place when it is acknowledged or cleared", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1 }), alertFaketory.build({ id: A2, code: "LINK_LOST" }));
    const { pinia } = await render();
    await expect.element(rows().first()).toBeInTheDocument();
    const alerts = useAlertsStore(pinia);
    await userEvent.selectOptions(page.getByLabelText("Show"), "all"); // a cleared alert only shows in "all"

    alerts.applyUpdate(
      alertFaketory.build({ id: A1, acknowledged_by: "someone-else", acknowledged_at: "2026-01-01T00:05:00Z" }),
    );
    alerts.applyUpdate(alertFaketory.build({ id: A2, code: "LINK_LOST", cleared_at: "2026-01-01T00:07:00Z" }));

    await expect
      .element(page.getByText("Acknowledged by someone-else at 2026-01-01 00:05:00 UTC"))
      .toBeInTheDocument();
    await expect.element(page.getByText("Cleared at 2026-01-01 00:07:00 UTC")).toBeInTheDocument();
  });

  it("acknowledges an open alert with the button and shows who acknowledged it", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1 }));
    await render();

    await userEvent.click(page.getByRole("button", { name: /Acknowledge LOW_BATTERY/ }));

    await expect.element(page.getByText("Acknowledged by you at 2026-01-01 00:05:00 UTC")).toBeInTheDocument();
    expect(fakeDb.alerts.acked).toEqual([A1]);
    await expect.element(page.getByRole("button", { name: /Acknowledge LOW_BATTERY/ })).not.toBeInTheDocument();
  });

  it("shows the first acknowledger, with no error, when someone else got there first (409)", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1 }));
    worker.use(alertEndpoints.respondWith(409, { acknowledgedBy: "dispatcher-two" }));
    await render();

    await userEvent.click(page.getByRole("button", { name: /Acknowledge LOW_BATTERY/ }));

    await expect.element(page.getByText(/Acknowledged by dispatcher-two/)).toBeInTheDocument();
    await expect.element(page.getByRole("alert")).not.toBeInTheDocument();
  });

  it("says Not allowed when the server answers 403", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1 }));
    worker.use(alertEndpoints.respondWith(403));
    await render();

    await userEvent.click(page.getByRole("button", { name: /Acknowledge LOW_BATTERY/ }));

    await expect.element(page.getByRole("alert")).toHaveTextContent("Not allowed");
  });

  it("shows no Acknowledge button to a viewer", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1 }));

    await render("viewer");

    await expect.element(page.getByText("LOW_BATTERY", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByRole("button", { name: /Acknowledge/ })).not.toBeInTheDocument();
  });

  it("shows only the chosen severity", async () => {
    fakeDb.alerts.add(
      alertFaketory.build({ id: A1, severity: "WARNING", message: "warn msg" }),
      alertFaketory.build({ id: A2, severity: "CRITICAL", message: "crit msg" }),
    );
    await render();
    await expect.element(page.getByText("warn msg")).toBeInTheDocument();

    await userEvent.selectOptions(page.getByLabelText("Severity"), "CRITICAL");

    await expect.element(page.getByText("crit msg")).toBeInTheDocument();
    await expect.element(page.getByText("warn msg")).not.toBeInTheDocument();
  });

  it("filters by drone and by open only versus all, using ?drone_id= and ?open=", async () => {
    fakeDb.alerts.add(
      alertFaketory.build({ id: A1, drone_id: "d1", message: "on d1" }),
      alertFaketory.build({ id: A2, drone_id: "d2", message: "on d2" }),
      alertFaketory.build({ id: A3, drone_id: "d1", message: "old one", cleared_at: "2026-01-01T00:09:00Z" }),
    );
    await render();
    await expect.element(page.getByText("on d2")).toBeInTheDocument();
    await expect.element(page.getByText("old one")).not.toBeInTheDocument();

    await userEvent.selectOptions(page.getByLabelText("Drone"), "d1");
    await expect.element(page.getByText("on d2")).not.toBeInTheDocument();
    await userEvent.selectOptions(page.getByLabelText("Show"), "all");

    await expect.element(page.getByText("old one")).toBeInTheDocument();
    const last = fakeDb.alerts.listed.at(-1);
    expect(last?.get("drone_id")).toBe("d1");
    expect(last?.has("open")).toBe(false);
  });

  it("shows CRITICAL first in its group, as a text label", async () => {
    fakeDb.alerts.add(
      alertFaketory.build({ id: A1, severity: "WARNING", message: "warn msg", raised_at: "2026-01-01T00:09:00Z" }),
      alertFaketory.build({ id: A2, severity: "CRITICAL", message: "crit msg", raised_at: "2026-01-01T00:01:00Z" }),
    );

    await render();

    await expect.element(rows().first()).toMatchTextContent("CRITICAL");
    await expect.element(rows().first()).toMatchTextContent("crit msg");
  });

  it("opens the drone in the detail panel when its name is clicked", async () => {
    fakeDb.drones.add(droneFaketory.build({ id: "d1", name: "Courier 1" }));
    fakeDb.alerts.add(alertFaketory.build({ id: A1, drone_id: "d1" }));
    const { pinia } = await render();

    await userEvent.click(page.getByRole("button", { name: "Open drone d1" }));

    await expect.poll(() => useSelectionStore(pinia).selectedId).toBe("d1");
  });

  it("shows a message that contains HTML as plain text", async () => {
    fakeDb.alerts.add(alertFaketory.build({ id: A1, message: "<img src=x onerror=alert(1)> <b>bold</b>" }));

    await render();

    await expect.element(page.getByText("<img src=x onerror=alert(1)> <b>bold</b>")).toBeInTheDocument();
    // No role or text query can prove an element was NOT created from the message.
    expect(document.querySelector("img[src='x']")).toBeNull();
  });
});
