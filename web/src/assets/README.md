# Assets

`basemap.geojson` is the local base layer of the live map (ADR 0016): land, coastline and main roads around the simulator depot.

- Source: © OpenStreetMap contributors, Open Database Licence (ODbL) 1.0, https://www.openstreetmap.org/copyright
- Built by `scripts/build_basemap.py` (the Overpass query is in its header). Rebuild: `python3 scripts/build_basemap.py`.
- The map shows "© OpenStreetMap contributors (ODbL)" in its attribution line. Keep it.
- Size limit: 100 KB (checked by a unit test).
