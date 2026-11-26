/** Text shown when a value has not arrived yet. Never "null" and never a made-up 0. */
export const NO_DATA = "No data";

/** `2026-01-01 00:00:10 UTC`, so the text does not depend on the browser's locale or zone. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return NO_DATA;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return NO_DATA;
  return `${new Date(time).toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return NO_DATA;
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  if (minutes === 0) return `${rest} s`;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

export function formatPoint(point: { lat: number; lon: number } | null | undefined): string {
  return point ? `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}` : NO_DATA;
}
