import type { Theme } from "../theme/theme";

export const DEFAULT_ATTRIBUTION = "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors";

export const TILES: Record<Theme, string> = {
  light: "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png",
  dark: "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png",
};

/**
 * Tile URL for the theme; `override` (VITE_TILE_URL) fixes one URL for both themes.
 * In test mode there is none, so tests never touch the network even when a local .env sets the override.
 */
export function tileUrlFor(resolved: Theme, mode: string, override?: string): string | undefined {
  if (mode === "test") return undefined;
  return override || TILES[resolved];
}
