"""The demo area file: where simulated drones start and where the map opens."""

import json
import math
from pathlib import Path

AREA_FILE = Path(__file__).resolve().parents[1] / "geo" / "demo-area.v1.json"


def test_demo_area_is_a_sane_box_with_its_center_inside() -> None:
    area = json.loads(AREA_FILE.read_text())
    box, center = area["bounds"], area["center"]

    assert -90 <= box["lat_min"] < box["lat_max"] <= 90
    assert -180 <= box["lon_min"] < box["lon_max"] <= 180
    assert box["lat_min"] <= center["lat"] <= box["lat_max"]
    assert box["lon_min"] <= center["lon"] <= box["lon_max"]
    height_km = (box["lat_max"] - box["lat_min"]) * 111.0
    width_km = (box["lon_max"] - box["lon_min"]) * 111.0 * math.cos(math.radians(center["lat"]))
    # a city area, not a country
    assert 1 <= height_km <= 20
    assert 1 <= width_km <= 20
