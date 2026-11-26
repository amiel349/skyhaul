import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { renderComponent } from "../../tests/render";
import AppSelect from "./AppSelect.vue";

const OPTIONS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Bravo" },
  { value: "c", label: "Charlie" },
];

// The component is controlled: the test plays the parent and feeds the emitted value back in.
async function render(props: Record<string, unknown> = {}, attrs: Record<string, unknown> = {}) {
  const view = await renderComponent(AppSelect, {
    props: {
      modelValue: "a",
      options: OPTIONS,
      label: "Letter",
      "onUpdate:modelValue": (value: string) => void view.rerender({ modelValue: value }),
      ...props,
    },
    attrs,
  });
  return view;
}
const select = () => page.getByRole("combobox", { name: "Letter" });

describe("AppSelect", () => {
  it("ties a visible label to the native select", async () => {
    await render({ id: "letters" });

    await expect.element(page.getByText("Letter", { exact: true })).toBeVisible();
    await expect.element(page.getByLabelText("Letter")).toHaveAttribute("id", "letters");
    await expect.element(select()).toHaveDisplayValue("Alpha");
  });

  it("changes the value when an option is chosen", async () => {
    await render();

    await userEvent.selectOptions(select(), "b");

    await expect.element(select()).toHaveDisplayValue("Bravo");
  });

  it("changes the value with the keyboard", async () => {
    await render();
    await userEvent.tab(); // the label is not focusable, so the first Tab lands on the select

    await userEvent.keyboard("B"); // type-ahead picks "Bravo" on every platform (ArrowDown opens the popup on macOS)

    await expect.element(select()).toHaveDisplayValue("Bravo");
  });

  it("cannot be used when disabled", async () => {
    await render({ disabled: true });

    await expect.element(select()).toBeDisabled();
  });

  it("still renders with no options and with one option", async () => {
    const view = await render({ modelValue: "", options: [] });
    await expect.element(select()).toBeInTheDocument();

    await view.rerender({ modelValue: "a", options: [OPTIONS[0]] });

    await expect.element(select()).toHaveDisplayValue("Alpha");
  });

  it("passes data-testid to the select and keeps its name with a long option label", async () => {
    const long = "A very long option label that cannot possibly fit in a narrow right panel without being cut";
    await render(
      { modelValue: "x", options: [{ value: "x", label: long }] },
      { "data-testid": "letters", style: "width: 120px" },
    );

    await expect.element(page.getByTestId("letters")).toHaveDisplayValue(long);
    await expect.element(select()).toBeInTheDocument();
  });
});
