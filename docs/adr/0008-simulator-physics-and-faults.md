# 0008 · Simulator physics and fault model

Status: accepted · 2026-11-23 · amended 2026-11-24

## Context

The simulated drones must move, drain their batteries and raise alerts believably enough to build ingestion, missions and alerting against, and tests must be able to inject failures (lost link, rejections, slow replies, fast drain, bad GPS) into one drone. All of it runs on the scheduler thread of ADR 0003 and must be deterministic under a controlled clock.

## Decision

- **Movement:** straight lines at constant speed, computed per scheduler tick with an explicit `dt`, using local flat-earth offsets (`dN = Δlat·111 320`, `dE = Δlon·111 320·cos lat`). Accurate enough inside the 5 km geofence. Climb 3 m/s, descend 2 m/s, at the same time as horizontal movement. Arrival within 1 m horizontally and 0.5 m vertically snaps to the target, so there is no overshoot.
- **Battery** (% per second): LANDED 0, ARMED 0.01, airborne `0.05 + 0.004·ground_speed (+0.03 while climbing)`, about 18 minutes at 10 m/s. At 0 % an airborne drone lands in place.
- **Critical battery** (product owner decision): at or below `CRITICAL_BATTERY_PCT` (10 %) a drone only returns home or lands. TAKEOFF, GOTO, START_MISSION and SET_SPEED get NACK `BATTERY_TOO_LOW`. Nobody can launch, redirect or slow a drone heading home on its last charge, and no command is acked and then silently overridden by the forced landing at 0 %. ARM keeps its own 15 % minimum.
- **Alerts are latched per threshold** and re-arm once the condition is false again (battery back above the threshold, drone back inside the fence): `LOW_BATTERY` at ≤20 % (WARNING), `CRITICAL_BATTERY` at ≤10 % (CRITICAL; starts RETURN_HOME only when airborne and not already RETURNING or LANDING), `GEOFENCE_BREACH` when the reported position leaves the radius (WARNING, `value` = meters outside the fence), `LINK_RESTORED` when a link loss expires.
- **Aborting a mission answers at once.** LAND or RETURN_HOME during a mission reply `[ACK, final MISSION_STATUS ABORTED]` from the handler, instead of leaving the final status to the next tick, so a START_MISSION arriving before that tick cannot swallow it.
- **Configuration** (non-secret, bounded, with defaults): `SIM_SEED`, `SIM_GEOFENCE_RADIUS_M`, `SIM_HOME_LAT`, `SIM_HOME_LON`.
- **Determinism:** each drone has its own `random.Random` seeded from a stable hash (blake2b) of the configured seed and the drone ID. Same seed and same commands give the same telemetry.
- **Faults are state on the drone,** changed under the board lock and expired on scheduler ticks:
  - `LINK_LOSS` (1 to 600 s, required): commands are dropped (acked, not cached, not applied), nothing is published, events produced meanwhile (alerts, final statuses) are dropped rather than queued, delayed replies falling due are dropped, physics keeps running. Ends with ALERT `LINK_RESTORED`, unless the faults are cleared or the drone is reset.
  - `NACK_NEXT` (1 to 100, no duration): the next N new commands get NACK `INJECTED` and are not applied.
  - `REPLY_DELAY` (0.1 to 30 s): replies become scheduled one-shots on the board, staged through the handler's transaction (`World.delay`). No handler ever sleeps. At most 1 000 wait per namespace; the cap is checked before the handler, so an overflowing command gets NACK `INTERNAL_ERROR` and is never applied. A duplicate's replay or a reply carrying a mission's final status that no longer fits goes out undelayed rather than being dropped.
  - `BATTERY_DRAIN` (factor 1 to 100): multiplies the drain rate.
  - `GPS_DRIFT` (1 to 500 m): offsets the reported position only; arrival and physics use the true position. The offset is redrawn every tick and stored on the drone, so POSITION and the geofence check agree.
  - The optional `duration_s` is 1 to 600 s for every kind that takes one. Posting a kind that is already active replaces it.

## Consequences

- Unit tests drive physics and faults with a fake clock and fixed `dt`, with no sleeps.
- `GEOFENCE_BREACH` follows the reported position, so `GPS_DRIFT` near the edge can trigger it. This mirrors what a ground station would see.
- The model is not realistic flight dynamics (no wind, acceleration or energy model). That is out of scope for M1.

## Alternatives considered

- Haversine or geodesic math: more precise, but the error of the flat-earth step inside 5 km is far below the 1 m arrival tolerance.
- Delaying replies with `sleep` or a timer thread: breaks ADR 0003 (handlers never sleep, constant thread count).
- Global faults: simpler, but tests must target one drone without disturbing parallel workers.
- `random` module global state: not reproducible per drone once drones are spawned in a different order.
