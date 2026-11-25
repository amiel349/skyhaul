#!/usr/bin/env python3
"""Rebuild web/src/assets/basemap.geojson from OpenStreetMap (Overpass API).

Data licence: (c) OpenStreetMap contributors, ODbL 1.0
(https://www.openstreetmap.org/copyright). The generated file is a derived
database; keep the attribution "(c) OpenStreetMap contributors (ODbL)" visible
wherever the map is shown (the UI does).

Usage:  python3 scripts/build_basemap.py [out.geojson] [--save-raw raw.json | --from-raw raw.json]
        python3 scripts/build_basemap.py --query     # print the Overpass query

Area: 6 km north-south around the simulator depot (lat 32.0, lon 34.8), 3 km east of it and
west to lon 34.735 so the Mediterranean coast (about 5 km west) is inside.
Features (property "kind"):
  land   Polygon     built from natural=coastline ways: the longest coastline is
                     chained, extended to the box edge and closed along the edge
                     (OSM puts land on the left of the way). Absent if no coast.
  coast  LineString  natural=coastline
  road   LineString  highway=motorway|trunk|primary (and _link)
Coordinates are rounded to 5 decimals and simplified (Douglas-Peucker, about 7 m)
so the file stays far below the 100 KB ceiling. Stdlib only. The script needs the
network; the app and the tests never do.

Overpass query:
  [out:json][timeout:60];
  ( way["natural"="coastline"](S,W,N,E);
    way["highway"~"^(motorway|trunk|primary)(_link)?$"](S,W,N,E); );
  out geom;
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

LAT, LON = 32.0, 34.8
HALF_KM = 3.0
DLAT = HALF_KM / 111.32
DLON = HALF_KM / (111.32 * math.cos(math.radians(LAT)))
# The coast is about 5 km west of the depot, so the box is 6 km tall and reaches west to include it.
SOUTH, NORTH, WEST, EAST = LAT - DLAT, LAT + DLAT, 34.735, LON + DLON
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
DECIMALS = 5
TOLERANCE = 0.0001
OUT = Path(__file__).resolve().parent.parent / "web/src/assets/basemap.geojson"

QUERY = f"""[out:json][timeout:60];
(
  way["natural"="coastline"]({SOUTH:.5f},{WEST:.5f},{NORTH:.5f},{EAST:.5f});
  way["highway"~"^(motorway|trunk|primary)(_link)?$"]({SOUTH:.5f},{WEST:.5f},{NORTH:.5f},{EAST:.5f});
);
out geom;"""

Point = tuple[float, float]  # (lon, lat)


def fetch() -> dict:
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    headers = {"User-Agent": "skyhaul-basemap-build/1.0", "Accept": "application/json"}
    last: Exception | None = None
    for endpoint in ENDPOINTS:  # public instances are often busy; try the next one
        req = urllib.request.Request(endpoint, data=body, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 (fixed https URLs)
                return json.load(resp)
        except OSError as exc:
            last = exc
            print(f"{endpoint}: {exc}", file=sys.stderr)
    raise SystemExit(f"all Overpass endpoints failed: {last}")


def simplify(pts: list[Point], tol: float) -> list[Point]:
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    norm = math.hypot(x2 - x1, y2 - y1)
    best, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        if norm == 0:
            d = math.hypot(x - x1, y - y1)
        else:
            d = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[: idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def inside(p: Point) -> bool:
    return WEST <= p[0] <= EAST and SOUTH <= p[1] <= NORTH


def clip(pts: list[Point]) -> list[list[Point]]:
    """Runs of consecutive vertices inside the box, each end pushed onto the box edge."""
    runs: list[list[Point]] = []
    cur: list[Point] = []
    for i, p in enumerate(pts):
        if inside(p):
            if not cur and i > 0:
                cur.append(to_edge(pts[i - 1], p))
            cur.append(p)
        elif cur:
            cur.append(to_edge(p, pts[i - 1]))
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def to_edge(out: Point, inn: Point) -> Point:
    """Point where the segment inn->out leaves the box (out is outside)."""
    dx, dy = out[0] - inn[0], out[1] - inn[1]
    ts = [1.0]
    if dx:
        ts += [t for t in ((WEST - inn[0]) / dx, (EAST - inn[0]) / dx) if 0 <= t <= 1]
    if dy:
        ts += [t for t in ((SOUTH - inn[1]) / dy, (NORTH - inn[1]) / dy) if 0 <= t <= 1]
    t = min(ts)
    return (
        min(max(inn[0] + dx * t, WEST), EAST),
        min(max(inn[1] + dy * t, SOUTH), NORTH),
    )


def chain(ways: list[list[Point]]) -> list[list[Point]]:
    """Join ways that share an end point (OSM splits long coastlines)."""
    ways = [w[:] for w in ways]
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(ways):
            for j, b in enumerate(ways):
                if i != j and a[-1] == b[0]:
                    ways[i] = a + b[1:]
                    del ways[j]
                    merged = True
                    break
            if merged:
                break
    return ways


def perimeter_pos(p: Point) -> float:
    """Position along the box edge, counter-clockwise from the SW corner, 0..4."""
    x, y = p
    w, h = EAST - WEST, NORTH - SOUTH
    d = {"s": abs(y - SOUTH), "e": abs(x - EAST), "n": abs(y - NORTH), "w": abs(x - WEST)}
    side = min(d, key=lambda k: d[k])
    if side == "s":
        return (x - WEST) / w
    if side == "e":
        return 1 + (y - SOUTH) / h
    if side == "n":
        return 2 + (EAST - x) / w
    return 3 + (NORTH - y) / h


def close_land(coast: list[list[Point]]) -> list[Point] | None:
    """Close the longest coast run (edge to edge) with the box edge on its left side."""
    runs = [r for c in coast for r in clip(c)]
    if not runs:
        return None
    run = max(runs, key=len)
    pa, pb = perimeter_pos(run[0]), perimeter_pos(run[-1])
    if pa <= pb:
        pa += 4
    corners = [(WEST, SOUTH), (EAST, SOUTH), (EAST, NORTH), (WEST, NORTH)]
    ring = list(run)
    for c in range(math.floor(pb) + 1, math.ceil(pa)):
        ring.append(corners[c % 4])
    ring.append(run[0])
    return ring


def build(data: dict) -> dict:
    coast_ways: list[list[Point]] = []
    roads: list[list[Point]] = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
        if el.get("tags", {}).get("natural") == "coastline":
            coast_ways.append(pts)
        else:
            roads.extend(simplify(r, TOLERANCE) for r in clip(pts))
    coast_ways = chain(coast_ways)
    features: list[dict] = []
    land = close_land([simplify(w, TOLERANCE) for w in coast_ways])
    if land:
        features.append(feature("land", "Polygon", [rnd(land)]))
    for w in coast_ways:
        for r in clip(w):
            features.append(feature("coast", "LineString", rnd(simplify(r, TOLERANCE))))
    features += [feature("road", "LineString", rnd(r)) for r in roads]
    return {"type": "FeatureCollection", "features": features}


def rnd(pts: list[Point]) -> list[list[float]]:
    return [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in pts]


def feature(kind: str, geometry_type: str, coordinates: list) -> dict:
    return {
        "type": "Feature",
        "properties": {"kind": kind},
        "geometry": {"type": geometry_type, "coordinates": coordinates},
    }


def main() -> None:
    if "--query" in sys.argv:
        print(QUERY)
        return
    args = [a for a in sys.argv[1:]]
    raw_out = Path(args.pop(args.index("--save-raw") + 1)) if "--save-raw" in args else None
    raw_in = Path(args.pop(args.index("--from-raw") + 1)) if "--from-raw" in args else None
    args = [a for a in args if a not in ("--save-raw", "--from-raw")]
    out = Path(args[0]) if args else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(raw_in.read_text()) if raw_in else fetch()
    if raw_out:
        raw_out.write_text(json.dumps(data))
    collection = build(data)
    out.write_text(json.dumps(collection, separators=(",", ":")) + "\n")
    kinds = [f["properties"]["kind"] for f in collection["features"]]
    counts = ", ".join(f"{k}={kinds.count(k)}" for k in sorted(set(kinds)))
    print(f"{out}: {out.stat().st_size} bytes, {counts}")


if __name__ == "__main__":
    main()
