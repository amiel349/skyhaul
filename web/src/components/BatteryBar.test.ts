import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import BatteryBar from "./BatteryBar.vue";
import { renderComponent } from "../../tests/render";

describe("BatteryBar", () => {
  it("shows the percentage", async () => {
    await renderComponent(BatteryBar, { props: { pct: 64, testid: "battery" } });

    await expect.element(page.getByText("64%", { exact: true })).toBeInTheDocument();
    // data-low and data-critical exist only as data attributes, so the wrapper is found by its test id.
    await expect.element(page.getByTestId("battery")).toHaveAttribute("data-low", "false");
  });

  it("says LOW in words under 20%", async () => {
    await renderComponent(BatteryBar, { props: { pct: 12, testid: "battery" } });

    await expect.element(page.getByText("LOW 12%", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByTestId("battery")).toHaveAttribute("data-low", "true");
  });

  it("says CRITICAL in words under 10%", async () => {
    await renderComponent(BatteryBar, { props: { pct: 5, testid: "battery" } });

    await expect.element(page.getByText("CRITICAL 5%", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByTestId("battery")).toHaveAttribute("data-critical", "true");
  });

  it("says No data without a value", async () => {
    await renderComponent(BatteryBar, { props: { pct: null, testid: "battery" } });

    await expect.element(page.getByText("No data", { exact: true })).toBeInTheDocument();
  });
});
