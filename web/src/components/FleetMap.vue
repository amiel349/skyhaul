<script setup lang="ts">
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { useThemeStore } from "../theme/theme";
import { baseStyle, markerLook } from "./markerStyle";

type GeoData = Parameters<typeof L.geoJSON>[0];

const OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)";

const fleet = useFleetStore();
const selection = useSelectionStore();
const theme = useThemeStore();
const container = ref<HTMLElement | null>(null);
const markerCount = ref(0);
const baseState = ref<"loading" | "ready" | "unavailable">("loading");

let map: L.Map | null = null;
let base: L.GeoJSON | null = null;
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

function restyleBase(): void {
  base?.setStyle((feature) => baseStyle(feature?.properties?.kind as string | undefined));
}

async function loadBase(): Promise<void> {
  try {
    // Raw text, parsed here: Vite only treats .json as a JSON module, and a bad file lands in the catch below.
    const data = JSON.parse((await import("../assets/basemap.geojson?raw")).default) as GeoData;
    if (!map) return; // unmounted while loading
    base = L.geoJSON(data, {
      interactive: false,
      style: (feature) => baseStyle(feature?.properties?.kind as string | undefined),
    }).addTo(map);
    base.bringToBack();
    baseState.value = "ready";
  } catch {
    baseState.value = "unavailable"; // markers stay on the plain themed background
  }
}

onMounted(() => {
  if (!container.value) return;
  map = L.map(container.value, {
    renderer: L.canvas(),
    center: [32.0, 34.8],
    zoom: 13,
    attributionControl: false,
  });
  L.control.attribution({ prefix: false }).addAttribution(OSM_ATTRIBUTION).addTo(map);
  // Tiles are optional and go on top of the local layer. Tests never load them, even if a local .env sets the URL.
  const tileUrl: string | undefined = import.meta.env.MODE === "test" ? undefined : import.meta.env.VITE_TILE_URL;
  if (tileUrl) {
    L.tileLayer(tileUrl, {
      attribution: import.meta.env.VITE_TILE_ATTRIBUTION ?? "",
      maxZoom: 19,
    }).addTo(map);
  }
  sync();
  void loadBase();
});

// One pass over the drones per change; only moved markers are touched, the map is not rebuilt.
watch(() => fleet.list, sync, { flush: "post" });
watch(() => selection.selectedId, sync, { flush: "post" });
// A theme switch changes the CSS tokens; the canvas has to be told to redraw with the new values.
watch(
  () => theme.resolved,
  () => {
    restyleBase();
    sync();
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  map?.remove();
  map = null;
  base = null;
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
        :data-base="baseState"
      />
      <p
        v-if="baseState === 'unavailable'"
        class="notice"
        role="status"
        data-testid="basemap-unavailable"
      >
        Base map unavailable
      </p>
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
  min-height: 20rem;
}
.map {
  position: absolute;
  inset: 0;
}
.notice {
  position: absolute;
  left: 8px;
  top: 8px;
  z-index: 1000;
  margin: 0;
  padding: 2px 8px;
  border: 1px dashed var(--warn);
  border-radius: 4px;
  background: var(--panel);
  color: var(--warn);
  font-size: 12px;
}
</style>
