import { page, userEvent } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../stores/auth";
import { THEME_KEY, useThemeStore } from "../theme/theme";
import UserMenu from "./UserMenu.vue";
import { renderComponent } from "../../tests/render";

// The socket module is the one thing the auth store's logout reaches outside the page.
vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

// Renders only UserMenu, for a signed-in user, with the theme store started on a dark page.
async function renderMenu() {
  document.documentElement.setAttribute("data-theme", "dark");
  const { pinia } = await renderComponent(UserMenu, { user: { role: "admin" } });
  useThemeStore(pinia).init();
  return { auth: useAuthStore(pinia) };
}

const avatar = () => page.getByRole("button", { name: "Account menu for a@skyhaul.dev" });
const menu = () => page.getByRole("menu", { name: "Account" });
const themeToggle = () => page.getByRole("menuitemcheckbox", { name: "Dark mode" });

describe("UserMenu", () => {
  it("shows the initials in the avatar and the login and role in the menu", async () => {
    await renderMenu();
    await expect.element(avatar()).toHaveTextContent("A");

    await userEvent.click(avatar());

    await expect.element(avatar()).toHaveAttribute("aria-expanded", "true");
    await expect.element(page.getByText("a@skyhaul.dev", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByText("admin", { exact: true })).toBeInTheDocument();
  });

  it("closes with Escape and puts the focus back on the avatar", async () => {
    await renderMenu();
    await userEvent.click(avatar());
    await expect.element(menu()).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    await expect.element(menu()).not.toBeInTheDocument();
    await expect.element(avatar()).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    await renderMenu();
    const outside = document.createElement("button");
    outside.textContent = "Outside";
    document.body.append(outside);
    await userEvent.click(avatar());
    await expect.element(menu()).toBeInTheDocument();

    await userEvent.click(page.getByRole("button", { name: "Outside" }));
    outside.remove();

    await expect.element(menu()).not.toBeInTheDocument();
  });

  it("switches the theme at once with the toggle and remembers only its name", async () => {
    await renderMenu();
    await userEvent.click(avatar());
    await expect.element(themeToggle()).toHaveAttribute("aria-checked", "true");

    await userEvent.click(themeToggle());

    await expect.element(themeToggle()).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("logs out from the menu and closes it", async () => {
    const { auth } = await renderMenu();
    await userEvent.click(avatar());

    await userEvent.click(page.getByRole("menuitem", { name: "Log out" }));

    expect(auth.token).toBeNull();
    await expect.element(menu()).not.toBeInTheDocument();
  });
});
