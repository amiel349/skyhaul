import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import StatusChip from "./StatusChip.vue";
import { renderComponent } from "../../tests/render";

describe("StatusChip", () => {
  it("shows its text in words with the tone it was given", async () => {
    await renderComponent(StatusChip, { props: { text: "LOST", tone: "crit", testid: "chip" } });

    const chip = page.getByTestId("chip");
    await expect.element(chip).toHaveTextContent("LOST");
    await expect.element(chip).toHaveAttribute("data-tone", "crit");
  });
});
