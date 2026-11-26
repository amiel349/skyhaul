<script setup lang="ts">
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import area from "../../../contracts/geo/demo-area.v1.json";
import { useFleetStore } from "../stores/fleet";
import { useMapPickStore } from "../stores/mapPick";
import { useSelectionStore } from "../stores/selection";
import { useThemeStore } from "../theme/theme";
import AppButton from "./AppButton.vue";
import { markerLook } from "./markerStyle";
import { DEFAULT_ATTRIBUTION, tileUrlFor } from "./tiles";

function tileUrl(): string | undefined {
  return tileUrlFor(theme.resolved, import.meta.env.MODE, import.meta.env.VITE_TILE_URL);
}

const fleet = useFleetStore();
const selection = useSelectionStore();
const mapPick = useMapPickStore();
const theme = useThemeStore();
const container = ref<HTMLElement | null>(null);
const markerCount = ref(0);
const center = ref({ ...area.center });
const zoom = ref(0);
// Drones are registered but none has reported a position: the map is empty for a reason worth saying.
const noLiveData = computed(
  () => fleet.loaded.done && fleet.list.length > 0 && fleet.list.every((drone) => !drone.state?.position),
);
const devHint = import.meta.env.DEV;
const tileUrlAttr = computed(() => tileUrl() ?? "");

let map: L.Map | null = null;
let tiles: L.TileLayer | null = null;
let sizeObserver: ResizeObserver | null = null;
const markers = new Map<string, L.CircleMarker>();
let targetMarker: L.CircleMarker | null = null;
/**
 * The map opens on the demo area and keeps fitting it while its box settles (fonts, saved panel
 * sizes, the window) until the dispatcher takes over: a drag, a zoom, a click or Follow. Fitting only
 * once would fit a half-laid-out box and leave a view that is far too wide.
 */
let userMoved = false;

function takeOver(): void {
  userMoved = true;
}

function fitArea(): void {
  if (!map || userMoved) return;
  map.invalidateSize();
  const size = map.getSize();
  if (size.x === 0 || size.y === 0) return; // not laid out yet; the resize observer calls again
  map.fitBounds(
    [
      [area.bounds.lat_min, area.bounds.lon_min],
      [area.bounds.lat_max, area.bounds.lon_max],
    ],
    { padding: [16, 16], animate: false },
  );
}

function labelNode(
  id: string,
  text: string,
  lost: boolean,
  lat: number,
  lon: number,
  selected: boolean,
): HTMLElement {
  // Text node, never an HTML string: drone names are untrusted.
  const span = document.createElement("span");
  span.textContent = text;
  span.dataset.testid = `fleet-marker-label-${id}`;
  span.dataset.link = lost ? "LOST" : "UP";
  span.dataset.lat = String(lat);
  span.dataset.lon = String(lon);
  span.dataset.selected = String(selected);
  return span;
}

/** What is drawn for each marker, so a change is applied only when something differs. */
const drawn = new Map<string, string>();
/** The followed position the map was last centred on; cleared when Follow is off. */
let centredOn = "";

function sync(force = false): void {
  if (!map) return;
  const seen = new Set<string>();
  if (!(selection.follow && selection.selectedId)) centredOn = "";
  for (const drone of fleet.list) {
    const position = drone.state?.position;
    if (!position) continue; // not drawn until it has a position
    seen.add(drone.id);
    const selected = selection.selectedId === drone.id;
    const look = markerLook(drone, selected);
    const at = L.latLng(position.lat, position.lon);
    const signature = `${position.lat}|${position.lon}|${look.label}|${look.lost}|${selected}`;
    const label = (): HTMLElement =>
      labelNode(drone.id, look.label, look.lost, position.lat, position.lon, selected);
    const existing = markers.get(drone.id);
    if (existing) {
      if (force || drawn.get(drone.id) !== signature) {
        existing.setLatLng(at);
        existing.setStyle(look.path);
        existing.setRadius(look.radius);
        existing.setTooltipContent(label());
        existing.getTooltip()?.getElement()?.classList.toggle("is-selected", selected);
        if (selected) existing.bringToFront();
        drawn.set(drone.id, signature);
      }
      // Follow is paused while picking, so the map does not move under the click; it resumes after.
      if (selected && selection.follow && !mapPick.active) {
        const key = `${drone.id}|${position.lat}|${position.lon}`;
        if (key !== centredOn) {
          centredOn = key;
          takeOver(); // Follow is the dispatcher choosing the view
          map.panTo(at, { animate: false });
        }
      }
    } else {
      const marker = L.circleMarker(at, { radius: look.radius, ...look.path }).addTo(map);
      marker.bindTooltip(label(), {
        permanent: true,
        direction: "right",
        offset: [10, 0],
        className: "drone-tag",
      });
      // While picking, a click on a drone is a pick: the click bubbles to the map, the drone is not selected.
      marker.on("click", () => {
        if (!mapPick.active) selection.select(drone.id);
      });
      markers.set(drone.id, marker);
      drawn.set(drone.id, signature);
    }
  }
  for (const [id, marker] of markers) {
    if (!seen.has(id)) {
      marker.remove();
      markers.delete(id);
      drawn.delete(id);
    }
  }
  markerCount.value = markers.size;
}

/** The GoTo target marker: drawn while the store holds a target, with its position as data attributes. */
function syncTarget(): void {
  if (!map) return;
  const point = mapPick.target;
  if (!point) {
    targetMarker?.remove();
    targetMarker = null;
    return;
  }
  const at = L.latLng(point.lat, point.lon);
  const label = document.createElement("span");
  label.textContent = "Target";
  label.dataset.testid = "map-target-label";
  label.dataset.lat = String(point.lat);
  label.dataset.lon = String(point.lon);
  if (targetMarker) {
    targetMarker.setLatLng(at);
    targetMarker.setTooltipContent(label);
    return;
  }
  targetMarker = L.circleMarker(at, {
    radius: 7,
    color: "#ffffff",
    weight: 2,
    fillColor: "#e5484d",
    fillOpacity: 1,
    interactive: false, // a click on the target goes to the map, so it can be picked again
  }).addTo(map);
  targetMarker.bindTooltip(label, { permanent: true, direction: "top", offset: [0, -8], className: "drone-tag" });
}

function onEscape(event: KeyboardEvent): void {
  if (event.key === "Escape") mapPick.cancel();
}

function applyTiles(): void {
  if (!map) return;
  const url = tileUrl();
  if (!url) return;
  if (tiles) tiles.setUrl(url);
  else tiles = L.tileLayer(url, { maxZoom: 19 }).addTo(map);
}

onMounted(() => {
  if (!container.value) return;
  map = L.map(container.value, {
    renderer: L.canvas(),
    center: [area.center.lat, area.center.lon],
    zoom: 13,
    attributionControl: false,
    // A zoom animation ends in a timer; a test that unmounts mid-zoom would let it fire on a removed map.
    zoomAnimation: import.meta.env.MODE !== "test",
  });
  const attribution = import.meta.env.VITE_TILE_URL
    ? (import.meta.env.VITE_TILE_ATTRIBUTION ?? "")
    : DEFAULT_ATTRIBUTION;
  if (import.meta.env.MODE !== "test") {
    L.control.attribution({ prefix: false }).addAttribution(attribution).addTo(map);
  }
  applyTiles();
  // Leaflet fires `click` only for a real click: after a drag it does not, so a drag never picks.
  map.on("click", (event: L.LeafletMouseEvent) => {
    if (mapPick.active) mapPick.pick(event.latlng.lat, event.latlng.lng);
  });
  syncTarget();
  // Dragging the map by hand is the dispatcher taking over: Follow turns off.
  map.on("dragstart", () => selection.setFollow(false));
  map.on("move", () => {
    const c = map?.getCenter();
    if (c) center.value = { lat: c.lat, lon: c.lng };
    if (map) zoom.value = map.getZoom();
  });
  // Any hand on the map (mouse, touch, wheel, keys, the zoom buttons) ends the automatic fit.
  for (const type of ["pointerdown", "wheel", "keydown"]) container.value.addEventListener(type, takeOver, { passive: true });
  fitArea();
  sync();
  // The card can be resized by the splitters; Leaflet only needs to re-measure, not redraw.
  if (typeof ResizeObserver !== "undefined") {
    sizeObserver = new ResizeObserver(() => {
      map?.invalidateSize();
      fitArea();
    });
    sizeObserver.observe(container.value);
  }
});

// One pass over the drones per change; only moved markers are touched, the map is not rebuilt.
watch(() => fleet.list, () => sync(), { flush: "post" });
watch(() => selection.selectedId, () => sync(), { flush: "post" });
watch(() => selection.follow, () => sync(), { flush: "post" }); // turning Follow on centres at once
watch(() => mapPick.target, () => syncTarget(), { flush: "post" });
watch(() => mapPick.active, (on) => {
  if (on) window.addEventListener("keydown", onEscape);
  else window.removeEventListener("keydown", onEscape);
  sync(); // Follow resumes when pick mode ends
}, { flush: "post" });
// A theme switch changes the CSS tokens; the canvas has to be told to redraw with the new values.
watch(
  () => theme.resolved,
  () => {
    applyTiles();
    sync(true);
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onEscape);
  targetMarker = null;
  sizeObserver?.disconnect();
  sizeObserver = null;
  map?.remove();
  map = null;
  userMoved = false;
  tiles = null;
  markers.clear();
  drawn.clear();
});
</script>

<template>
  <section
    class="wrap"
    aria-labelledby="map-heading"
  >
    <h2 id="map-heading">
      Live map
    </h2>
    <div class="stage">
      <div
        ref="container"
        class="map"
        :class="{ picking: mapPick.active }"
        role="region"
        aria-label="Fleet map"
        data-testid="fleet-map"
        :data-marker-count="markerCount"
        :data-center-lat="center.lat.toFixed(5)"
        :data-center-lon="center.lon.toFixed(5)"
        :data-zoom="zoom"
        :data-tile-url="tileUrlAttr"
      />
      <div class="tools">
        <p
          v-if="mapPick.active"
          class="banner"
          role="status"
          data-testid="map-pick-banner"
        >
          <span>Click the map to set the target. Esc cancels.</span>
          <AppButton
            size="sm"
            data-testid="map-pick-cancel"
            @click="mapPick.cancel()"
          >
            Cancel
          </AppButton>
        </p>
        <p
          v-if="mapPick.outsideArea"
          class="banner warn"
          role="status"
          data-testid="map-target-outside-area"
        >
          Outside the demo area, the drone may refuse it
        </p>
      </div>
      <div
        v-if="noLiveData"
        class="notice"
        role="status"
        data-testid="map-no-live-data"
      >
        <strong>No live data yet</strong>
        <span
          v-if="devHint"
          class="mono"
        >Start the simulator: make sim, then make seed</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wrap {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
h2 {
  margin: 0;
  padding: 11px 14px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}
.stage {
  position: relative;
  flex: 1;
  min-height: 0;
}
.map {
  position: absolute;
  inset: 0;
}
.map.picking,
.map.picking :deep(*) {
  cursor: crosshair !important;
}
.tools {
  position: absolute;
  bottom: 14px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1100;
  max-width: calc(100% - 24px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  pointer-events: none;
}
.banner {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--ink);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  pointer-events: auto;
}
.banner.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.notice {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1100;
  max-width: calc(100% - 24px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 14px;
  text-align: center;
  color: var(--ink);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  pointer-events: none;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}
</style>
