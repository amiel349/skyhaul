import { createPinia } from "pinia";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../stores/auth";
import { makeUser } from "../testing/factories/user";
import { THEME_KEY, useThemeStore } from "../theme/theme";
import UserMenu from "./UserMenu.vue";

// The socket module is the one thing the auth store's logout reaches outside the page.
vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

// Renders only UserMenu, for a signed-in user, with the theme store started on a dark page.
async function renderMenu(email = "a@skyhaul.dev") {
  document.documentElement.setAttribute("data-theme", "dark");
  const pinia = createPinia();
  await render(UserMenu, { global: { plugins: [pinia] } });
  const auth = useAuthStore(pinia);
  auth.token = "tok-1";
  auth.user = makeUser({ email });
  useThemeStore(pinia).init();
  return { auth };
}

describe("UserMenu", () => {
  it("shows the initials in the avatar and the login and role in the menu", async () => {
    await renderMenu();
    await expect.element(page.getByTestId("user-avatar")).toHaveTextContent("A");

    await userEvent.click(page.getByTestId("user-avatar"));

    await expect.element(page.getByTestId("user-avatar")).toHaveAttribute("aria-expanded", "true");
    await expect.element(page.getByTestId("user-menu-email")).toHaveTextContent("a@skyhaul.dev");
    await expect.element(page.getByTestId("user-menu-role")).toHaveTextContent("admin");
  });

  it("closes with Escape and puts the focus back on the avatar", async () => {
    await renderMenu();
    await userEvent.click(page.getByTestId("user-avatar"));
    await expect.element(page.getByTestId("user-menu")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    await expect.element(page.getByTestId("user-menu")).not.toBeInTheDocument();
    await expect.element(page.getByTestId("user-avatar")).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    await renderMenu();
    const outside = document.createElement("button");
    outside.dataset.testid = "outside";
    outside.textContent = "Outside";
    document.body.append(outside);
    await userEvent.click(page.getByTestId("user-avatar"));
    await expect.element(page.getByTestId("user-menu")).toBeInTheDocument();

    await userEvent.click(page.getByTestId("outside"));
    outside.remove();

    await expect.element(page.getByTestId("user-menu")).not.toBeInTheDocument();
  });

  it("switches the theme at once with the toggle and remembers only its name", async () => {
    await renderMenu();
    await userEvent.click(page.getByTestId("user-avatar"));
    await expect.element(page.getByTestId("theme-toggle")).toHaveAttribute("aria-checked", "true");

    await userEvent.click(page.getByTestId("theme-toggle"));

    await expect.element(page.getByTestId("theme-toggle")).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("logs out from the menu and closes it", async () => {
    const { auth } = await renderMenu();
    await userEvent.click(page.getByTestId("user-avatar"));

    await userEvent.click(page.getByTestId("user-menu-logout"));

    expect(auth.token).toBeNull();
    await expect.element(page.getByTestId("user-menu")).not.toBeInTheDocument();
  });
});
