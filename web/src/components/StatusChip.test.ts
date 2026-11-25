import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import StatusChip from "./StatusChip.vue";

describe("StatusChip", () => {
  it("shows its text in words with the tone it was given", async () => {
    await render(StatusChip, { props: { text: "LOST", tone: "crit", testid: "chip" } });

    const chip = page.getByTestId("chip");
    await expect.element(chip).toHaveTextContent("LOST");
    await expect.element(chip).toHaveAttribute("data-tone", "crit");
  });
});
