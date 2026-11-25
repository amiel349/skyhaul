<script setup lang="ts">
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useFleetStore } from "../stores/fleet";
import { markerLook } from "./markerStyle";

const fleet = useFleetStore();
const container = ref<HTMLElement | null>(null);
const markerCount = ref(0);

let map: L.Map | null = null;
const markers = new Map<string, L.CircleMarker>();

function labelNode(id: string, text: string, lost: boolean): HTMLElement {
  // Text node, never an HTML string: drone names are untrusted.
  const span = document.createElement("span");
  span.textContent = text;
  span.dataset.testid = `fleet-marker-label-${id}`;
  span.dataset.link = lost ? "LOST" : "UP";
  return span;
}

function sync(): void {
  if (!map) return;
  const seen = new Set<string>();
  for (const drone of fleet.list) {
    const position = drone.state?.position;
    if (!position) continue; // not drawn until it has a position
    seen.add(drone.id);
    const look = markerLook(drone);
    const at = L.latLng(position.lat, position.lon);
    const existing = markers.get(drone.id);
    if (existing) {
      existing.setLatLng(at);
      existing.setStyle(look.path);
      existing.setTooltipContent(labelNode(drone.id, look.label, look.lost));
    } else {
      const marker = L.circleMarker(at, { radius: 8, ...look.path }).addTo(map);
      marker.bindTooltip(labelNode(drone.id, look.label, look.lost), {
        permanent: true,
        direction: "right",
        offset: [10, 0],
      });
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

const DEFAULT_TILE_URL = "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png";
const DEFAULT_TILE_ATTRIBUTION = "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors";

onMounted(() => {
  if (!container.value) return;
  map = L.map(container.value, { renderer: L.canvas(), center: [32.0, 34.8], zoom: 13 });
  // Stadia (no key needed on localhost) is the dev default, so the map works without a web/.env file. Tests never load tiles, even when a local .env sets the URL.
  const tileUrl = import.meta.env.MODE === "test" ? "" : (import.meta.env.VITE_TILE_URL ?? DEFAULT_TILE_URL);
  if (tileUrl) {
    L.tileLayer(tileUrl, {
      attribution: import.meta.env.VITE_TILE_ATTRIBUTION ?? DEFAULT_TILE_ATTRIBUTION,
      maxZoom: 19,
    }).addTo(map);
  }
  sync();
});

// One pass over the drones per change; only moved markers are touched, the map is not rebuilt.
watch(() => fleet.list, sync, { flush: "post" });

onBeforeUnmount(() => {
  map?.remove();
  map = null;
  markers.clear();
});
</script>

<template>
  <div
    ref="container"
    class="map"
    role="region"
    aria-label="Fleet map"
    data-testid="fleet-map"
    :data-marker-count="markerCount"
  />
</template>

<style scoped>
.map {
  height: 100%;
  min-height: 24rem;
}
</style>
