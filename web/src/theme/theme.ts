import { defineStore } from "pinia";
import { computed, ref } from "vue";

export type Theme = "light" | "dark";

export const THEME_KEY = "skyhaul-theme";

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** The `data-theme` value wins when it is "light" or "dark"; anything else follows the system (dark by default). */
export function resolveTheme(attribute: string | null | undefined, systemLight: boolean): Theme {
  if (isTheme(attribute)) return attribute;
  return systemLight ? "light" : "dark";
}

function systemQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)")
    : null;
}

/**
 * Theme state. The root element's `data-theme` attribute is the source of truth (CSS reads it too),
 * so a value set from outside is followed. Only the theme name is ever saved in localStorage.
 */
export const useThemeStore = defineStore("theme", () => {
  const attribute = ref<string | null>(null);
  const systemLight = ref(false);
  const resolved = computed(() => resolveTheme(attribute.value, systemLight.value));
  let started = false;

  function readAttribute(): void {
    attribute.value = document.documentElement.getAttribute("data-theme");
  }

  /** Apply the saved theme, then follow the attribute and the system setting. Call once at start-up. */
  function init(): void {
    if (started) return;
    started = true;
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (isTheme(saved)) document.documentElement.setAttribute("data-theme", saved);
    } catch {
      // Storage may be blocked; the system theme is a fine default.
    }
    readAttribute();
    const query = systemQuery();
    if (query) {
      systemLight.value = query.matches;
      query.addEventListener("change", (e) => (systemLight.value = e.matches));
    }
    new MutationObserver(readAttribute).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  function set(next: Theme): void {
    document.documentElement.setAttribute("data-theme", next);
    readAttribute(); // do not wait for the observer
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Not saved; the choice still applies for this visit.
    }
  }

  return { resolved, init, set };
});

/** Current value of a CSS token (for canvas drawing, which cannot use var()). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
