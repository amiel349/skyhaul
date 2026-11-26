import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import BatteryBar from "./BatteryBar.vue";
import { renderComponent } from "../../tests/render";

describe("BatteryBar", () => {
  it("shows the percentage", async () => {
    await renderComponent(BatteryBar, { props: { pct: 64, testid: "battery" } });

    const bar = page.getByTestId("battery");
    await expect.element(bar).toHaveTextContent("64%");
    await expect.element(bar).toHaveAttribute("data-low", "false");
  });

  it("says LOW in words under 20%", async () => {
    await renderComponent(BatteryBar, { props: { pct: 12, testid: "battery" } });

    const bar = page.getByTestId("battery");
    await expect.element(bar).toHaveTextContent("LOW 12%");
    await expect.element(bar).toHaveAttribute("data-low", "true");
  });

  it("says CRITICAL in words under 10%", async () => {
    await renderComponent(BatteryBar, { props: { pct: 5, testid: "battery" } });

    const bar = page.getByTestId("battery");
    await expect.element(bar).toHaveTextContent("CRITICAL 5%");
    await expect.element(bar).toHaveAttribute("data-critical", "true");
  });

  it("says No data without a value", async () => {
    await renderComponent(BatteryBar, { props: { pct: null, testid: "battery" } });

    await expect.element(page.getByTestId("battery")).toHaveTextContent("No data");
  });
});
