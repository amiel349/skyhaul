<script setup lang="ts">
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { useThemeStore } from "../theme/theme";
import { markerLook } from "./markerStyle";
import { DEFAULT_ATTRIBUTION, tileUrlFor } from "./tiles";

function tileUrl(): string | undefined {
  return tileUrlFor(theme.resolved, import.meta.env.MODE, import.meta.env.VITE_TILE_URL);
}

const fleet = useFleetStore();
const selection = useSelectionStore();
const theme = useThemeStore();
const container = ref<HTMLElement | null>(null);
const markerCount = ref(0);
const tileUrlAttr = computed(() => tileUrl() ?? "");

let map: L.Map | null = null;
let tiles: L.TileLayer | null = null;
let sizeObserver: ResizeObserver | null = null;
const markers = new Map<string, L.CircleMarker>();

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

function sync(): void {
  if (!map) return;
  const seen = new Set<string>();
  for (const drone of fleet.list) {
    const position = drone.state?.position;
    if (!position) continue; // not drawn until it has a position
    seen.add(drone.id);
    const selected = selection.selectedId === drone.id;
    const look = markerLook(drone, selected);
    const at = L.latLng(position.lat, position.lon);
    const label = (): HTMLElement =>
      labelNode(drone.id, look.label, look.lost, position.lat, position.lon, selected);
    const existing = markers.get(drone.id);
    if (existing) {
      existing.setLatLng(at);
      existing.setStyle(look.path);
      existing.setRadius(look.radius);
      existing.setTooltipContent(label());
      existing.getTooltip()?.getElement()?.classList.toggle("is-selected", selected);
      if (selected) existing.bringToFront();
    } else {
      const marker = L.circleMarker(at, { radius: look.radius, ...look.path }).addTo(map);
      marker.bindTooltip(label(), {
        permanent: true,
        direction: "right",
        offset: [10, 0],
        className: "drone-tag",
      });
      marker.on("click", () => selection.select(drone.id));
      markers.set(drone.id, marker);
    }
  }
  for (const [id, marker] of markers) {
    if (!seen.has(id)) {
      marker.remove();
      markers.delete(id);
    }
  }
  markerCount.value = markers.size;
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
    center: [32.0, 34.8],
    zoom: 13,
    attributionControl: false,
  });
  const attribution = import.meta.env.VITE_TILE_URL
    ? (import.meta.env.VITE_TILE_ATTRIBUTION ?? "")
    : DEFAULT_ATTRIBUTION;
  if (import.meta.env.MODE !== "test") {
    L.control.attribution({ prefix: false }).addAttribution(attribution).addTo(map);
  }
  applyTiles();
  sync();
  // The card can be resized by the splitters; Leaflet only needs to re-measure, not redraw.
  if (typeof ResizeObserver !== "undefined") {
    sizeObserver = new ResizeObserver(() => map?.invalidateSize());
    sizeObserver.observe(container.value);
  }
});

// One pass over the drones per change; only moved markers are touched, the map is not rebuilt.
watch(() => fleet.list, sync, { flush: "post" });
watch(() => selection.selectedId, sync, { flush: "post" });
// A theme switch changes the CSS tokens; the canvas has to be told to redraw with the new values.
watch(
  () => theme.resolved,
  () => {
    applyTiles();
    sync();
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  sizeObserver?.disconnect();
  sizeObserver = null;
  map?.remove();
  map = null;
  tiles = null;
  markers.clear();
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
        role="region"
        aria-label="Fleet map"
        data-testid="fleet-map"
        :data-marker-count="markerCount"
        :data-tile-url="tileUrlAttr"
      />
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
</style>
