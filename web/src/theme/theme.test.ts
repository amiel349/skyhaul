import { createPinia, setActivePinia } from "pinia";
import { afterEach, describe, expect, it } from "vitest";
import { THEME_KEY, resolveTheme, useThemeStore } from "./theme";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
});

describe("theme", () => {
  it("resolves the attribute over the system setting and ignores unknown values", () => {
    expect(resolveTheme(null, false)).toBe("dark");
    expect(resolveTheme(null, true)).toBe("light");
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", true)).toBe("dark");
    expect(resolveTheme("purple", true)).toBe("light");
  });

  it("sets the root element and keeps only the theme name in storage", () => {
    setActivePinia(createPinia());
    const theme = useThemeStore();
    theme.init();
    expect(theme.resolved).toBe("dark");

    theme.set("light");

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(theme.resolved).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(Object.keys(localStorage)).toEqual([THEME_KEY]);
    expect(sessionStorage.length).toBe(0);
  });
});
