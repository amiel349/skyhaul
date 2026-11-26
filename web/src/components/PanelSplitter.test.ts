import { page, userEvent } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import PanelSplitter from "./PanelSplitter.vue";
import { renderComponent } from "../../tests/render";

// Renders only PanelSplitter, in a real browser. The parent's v-model and reset handlers are passed as props.
async function renderSplitter(over: Record<string, unknown> = {}, attrs: Record<string, unknown> = {}) {
  const onUpdate = vi.fn();
  const onReset = vi.fn();
  await renderComponent(PanelSplitter, {
    props: {
      modelValue: 270,
      min: 200,
      max: 480,
      label: "Resize fleet list",
      orientation: "vertical",
      testid: "splitter",
      "onUpdate:modelValue": onUpdate,
      onReset,
      ...over,
    },
    attrs,
  });
  return { onUpdate, onReset };
}

// Alone on the page the bar has no height, so there is nothing to click: focus it like Tab would.
function focusSplitter(): void {
  (page.getByTestId("splitter").element() as HTMLElement).focus();
}

describe("PanelSplitter", () => {
  it("is a labelled separator that shows its value and limits", async () => {
    await renderSplitter();

    const bar = page.getByTestId("splitter");
    await expect.element(bar).toHaveAttribute("role", "separator");
    await expect.element(bar).toHaveAttribute("aria-label", "Resize fleet list");
    await expect.element(bar).toHaveAttribute("aria-valuenow", "270");
    await expect.element(bar).toHaveAttribute("aria-valuemin", "200");
    await expect.element(bar).toHaveAttribute("aria-valuemax", "480");
  });

  it("moves by 16 with the arrow keys and by 64 with Shift", async () => {
    const { onUpdate } = await renderSplitter();
    focusSplitter();

    await userEvent.keyboard("{ArrowRight}");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");

    expect(onUpdate).toHaveBeenNthCalledWith(1, 286);
    expect(onUpdate).toHaveBeenNthCalledWith(2, 206);
  });

  it("jumps to the limits with Home and End, and resets with Enter", async () => {
    const { onUpdate, onReset } = await renderSplitter();
    focusSplitter();

    await userEvent.keyboard("{Home}");
    await userEvent.keyboard("{End}");
    await userEvent.keyboard("{Enter}");

    expect(onUpdate).toHaveBeenNthCalledWith(1, 200);
    expect(onUpdate).toHaveBeenNthCalledWith(2, 480);
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("follows a pointer drag and stops after release", async () => {
    // A horizontal bar needs a width and height to be grabbed; the target is a dot 50px above it.
    const { onUpdate } = await renderSplitter(
      { orientation: "horizontal", modelValue: 420, min: 240, max: 600 },
      { style: "width: 300px; height: 12px; margin-top: 120px" },
    );
    const rect = page.getByTestId("splitter").element().getBoundingClientRect();
    const target = document.createElement("div");
    target.style.cssText = `position: absolute; width: 2px; height: 2px; left: ${rect.left + window.scrollX + 20}px; top: ${rect.top + window.scrollY + rect.height / 2 - 50}px`;
    document.body.append(target);

    await userEvent.dragAndDrop(page.getByTestId("splitter"), target);
    target.remove();

    expect(onUpdate).toHaveBeenCalled();
    expect(onUpdate.mock.lastCall?.[0]).toBeGreaterThan(360);
    expect(onUpdate.mock.lastCall?.[0]).toBeLessThan(380);
    const calls = onUpdate.mock.calls.length;
    await userEvent.hover(page.getByTestId("splitter"));
    expect(onUpdate).toHaveBeenCalledTimes(calls);
  });
});
