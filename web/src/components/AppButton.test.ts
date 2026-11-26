import { page, userEvent } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import { renderComponent } from "../../tests/render";
import AppButton from "./AppButton.vue";

const render = (props: Record<string, unknown> = {}) =>
  renderComponent(AppButton, { props, slots: { default: "Save" } });
const button = () => page.getByRole("button", { name: "Save" });

describe("AppButton", () => {
  it.each(["primary", "secondary", "danger", "quiet"])("renders the %s variant with its text", async (variant) => {
    await render({ variant, size: "sm" });

    await expect.element(button()).toBeVisible();
    await expect.element(button()).toHaveAttribute("type", "button");
  });

  it("fires click when pressed with the mouse or the keyboard", async () => {
    const onClick = vi.fn();
    await render({ onClick });

    await userEvent.click(button());
    (button().element() as HTMLElement).focus();
    await userEvent.keyboard("{Enter}");

    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it("keeps its text and fires no click while loading", async () => {
    const onClick = vi.fn();
    await render({ loading: true, onClick });

    await expect.element(button()).toBeDisabled();
    await expect.element(button()).toHaveAttribute("aria-busy", "true");
    await userEvent.click(button(), { force: true });

    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires no click when disabled", async () => {
    const onClick = vi.fn();
    await render({ disabled: true, onClick });

    await expect.element(button()).toBeDisabled();
    await userEvent.click(button(), { force: true });

    expect(onClick).not.toHaveBeenCalled();
  });
});
