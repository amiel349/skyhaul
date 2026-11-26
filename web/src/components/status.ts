import type { FleetDrone } from "../stores/fleet";

export type Tone = "ok" | "warn" | "crit" | "neutral";

export interface ChipView {
  text: string;
  tone: Tone;
}

/** Link chip: the state is always in words, the colour only supports it. */
export function linkChip(drone: FleetDrone): ChipView {
  const link = drone.state?.link;
  if (link === "UP") return { text: "UP", tone: "ok" };
  if (link === "LOST") return { text: "LOST", tone: "crit" };
  return { text: "NO DATA", tone: "neutral" };
}

export const LOW_BATTERY_PCT = 20;
export const CRITICAL_BATTERY_PCT = 10;

export interface BatteryView {
  /** Width of the filled part, kept inside 0..100. */
  width: number;
  text: string;
  /** Under 20%: warning (also true when critical). */
  low: boolean;
  /** Under 10%. */
  critical: boolean;
}

/** The level is always in words (LOW, CRITICAL); colour and stripes only support it. */
export function batteryView(pct: number | null | undefined): BatteryView {
  if (pct == null || Number.isNaN(pct)) return { width: 0, text: "No data", low: false, critical: false };
  const width = Math.min(100, Math.max(0, pct));
  const critical = width < CRITICAL_BATTERY_PCT;
  const low = width < LOW_BATTERY_PCT;
  const rounded = `${Math.round(width)}%`;
  const text = critical ? `CRITICAL ${rounded}` : low ? `LOW ${rounded}` : rounded;
  return { width, text, low, critical };
}
