import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import StatusChip from "./StatusChip.vue";
import { renderComponent } from "../../tests/render";

describe("StatusChip", () => {
  it("shows its text in words with the tone it was given", async () => {
    await renderComponent(StatusChip, { props: { text: "LOST", tone: "crit", testid: "chip" } });

    await expect.element(page.getByText("LOST", { exact: true })).toBeInTheDocument();
    // The tone exists only as a data attribute, so the chip is found by its test id.
    await expect.element(page.getByTestId("chip")).toHaveAttribute("data-tone", "crit");
  });
});
