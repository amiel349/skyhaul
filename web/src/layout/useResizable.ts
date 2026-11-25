import { computed, onBeforeUnmount, onMounted, reactive, watch } from "vue";

// Layout sizes only (three numbers). Never put the token or anything else under this key (ADR 0015).
export const LAYOUT_KEY = "skyhaul.layout";

export interface LayoutSizes {
  fleet: number; // fleet list width, px
  panel: number; // drone panel width, px
  map: number; // map card height, px
}

export const DEFAULTS: LayoutSizes = { fleet: 270, panel: 330, map: 420 };
export const SIDE_MIN = 200;
export const SIDE_MAX = 480;
export const CENTER_MIN = 320;
export const MAP_MIN = 240;
export const ALERTS_MIN = 120;
const GUTTERS = 24; // two 12px vertical dividers
const CHROME = 150; // page padding, top bar, gaps and the map/alerts divider

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), Math.max(lo, hi));

export function readSizes(): LayoutSizes {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null");
    if (typeof raw !== "object" || raw === null) return { ...DEFAULTS };
    const r = raw as Record<string, unknown>;
    const ok = (v: unknown, lo: number, hi: number): v is number =>
      typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
    if (ok(r.fleet, SIDE_MIN, SIDE_MAX) && ok(r.panel, SIDE_MIN, SIDE_MAX) && ok(r.map, MAP_MIN, 4000)) {
      return { fleet: r.fleet, panel: r.panel, map: r.map };
    }
  } catch {
    // unreadable storage or bad JSON: use the defaults
  }
  return { ...DEFAULTS };
}

function writeSizes(sizes: LayoutSizes): void {
  try {
    const { fleet, panel, map } = sizes;
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ fleet, panel, map }));
  } catch {
    // storage may be blocked; the layout still works for this session
  }
}

export function useResizable() {
  const sizes = reactive<LayoutSizes>(readSizes());
  const view = reactive({ w: window.innerWidth, h: window.innerHeight });

  const limits = computed(() => ({
    fleet: {
      min: SIDE_MIN,
      max: Math.max(SIDE_MIN, Math.min(SIDE_MAX, view.w - sizes.panel - GUTTERS - CENTER_MIN)),
    },
    panel: {
      min: SIDE_MIN,
      max: Math.max(SIDE_MIN, Math.min(SIDE_MAX, view.w - sizes.fleet - GUTTERS - CENTER_MIN)),
    },
    map: { min: MAP_MIN, max: Math.max(MAP_MIN, view.h - CHROME - ALERTS_MIN) },
  }));

  function set(name: keyof LayoutSizes, value: number): void {
    const { min, max } = limits.value[name];
    sizes[name] = Math.round(clamp(value, min, max));
  }
  function reset(name: keyof LayoutSizes): void {
    set(name, DEFAULTS[name]);
  }
  function fit(): void {
    view.w = window.innerWidth;
    view.h = window.innerHeight;
    set("fleet", sizes.fleet);
    set("panel", sizes.panel);
    set("map", sizes.map);
  }

  onMounted(() => {
    window.addEventListener("resize", fit);
    fit();
  });
  onBeforeUnmount(() => window.removeEventListener("resize", fit));
  watch(sizes, () => writeSizes(sizes));

  return { sizes, limits, set, reset };
}
