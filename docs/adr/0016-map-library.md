# 0016 · Map library: Leaflet

Status: proposed · 2026-11-25 · amended 2026-11-25 (tiles are the default again) · changes the blueprint's MapLibre GL choice

## Context

The fleet view shows about 200 drones, each updating one to two times per second, on a free OSM-based map. It must be testable in Vitest (jsdom) and in headless Playwright in CI.

## Decision

- Leaflet 1.9 with the canvas renderer (`circleMarker`, `setLatLng` on update).
- Base layer: raster XYZ tiles from Stadia Maps `alidade_smooth` (light theme) and `alidade_smooth_dark` (dark theme); the layer URL follows the theme live. Stadia needs no key on localhost; a public demo (M7) needs a free account with a domain-locked key, or another provider. Attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors". `VITE_TILE_URL` (optional) overrides both with one fixed URL, and `VITE_TILE_ATTRIBUTION` is its attribution. Never `tile.openstreetmap.org` (its usage policy forbids app and heavy use); CARTO was the first choice but now requires an API key.
- History: an earlier amendment replaced tiles with a hand-built local GeoJSON layer (no network). It did not read well (one big land polygon, roads not visible), so it was removed and tiles are the default again.
- Tests load no network tiles, even if `VITE_TILE_URL` is set (mode `test`); Playwright routes tile requests to a blank image.

## Consequences

- A small bundle (about 42 kB gz against about 230 kB) and no WebGL, so jsdom and headless Chromium run it without GPU emulation.
- Canvas markers handle hundreds, not tens of thousands. Above a few thousand drones we revisit.
- No vector styling or 3D; dispatch does not need them.

## Alternatives considered

- MapLibre GL: best for large point counts and vector styles, but needs WebGL (mocked in jsdom, SwiftShader in CI) and a style and vector tile source.
- OpenLayers: capable, but larger and more API than we use.
