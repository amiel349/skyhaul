# 0016 · Map library: Leaflet

Status: proposed · 2026-11-25 · amended 2026-11-25 (local base layer is the default) · changes the blueprint's MapLibre GL choice

## Context

The fleet view shows about 200 drones, each updating one to two times per second, on a free OSM-based map. It must be testable in Vitest (jsdom) and in headless Playwright in CI.

## Decision

- Leaflet 1.9 with the canvas renderer (`circleMarker`, `setLatLng` on update).
- Default base layer (amendment): a local GeoJSON file, `web/src/assets/basemap.geojson`, with land, coastline and main roads around the simulator depot, built from OpenStreetMap through the Overpass API by `scripts/build_basemap.py` (ODbL; the map shows "© OpenStreetMap contributors (ODbL)"). It stays under 100 KB, is styled from the CSS tokens (ADR 0017) and needs no tile server and no network. If it fails to load, markers show on a plain themed background with a "Base map unavailable" notice.
- Raster XYZ tiles are optional: they are added on top of the local layer only when `VITE_TILE_URL` is set and the mode is not `test`. Tile providers: Stadia Maps `alidade_smooth` (no key on localhost; CARTO was the first choice but now requires an API key). A public demo (M7) needs a free Stadia account with a domain-locked key, or another provider. Tile URL and attribution come from `VITE_TILE_URL` and `VITE_TILE_ATTRIBUTION`. Never `tile.openstreetmap.org` (its usage policy forbids app and heavy use). Tests load no network tiles; Playwright routes tile requests to a blank image.

## Consequences

- A small bundle (about 42 kB gz against about 230 kB) and no WebGL, so jsdom and headless Chromium run it without GPU emulation.
- Canvas markers handle hundreds, not tens of thousands. Above a few thousand drones we revisit.
- No vector styling or 3D; dispatch does not need them.

## Alternatives considered

- MapLibre GL: best for large point counts and vector styles, but needs WebGL (mocked in jsdom, SwiftShader in CI) and a style and vector tile source.
- OpenLayers: capable, but larger and more API than we use.
