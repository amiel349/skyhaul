# 0008 · Simulator physics and fault model

Status: accepted · 2026-11-23

## Context

The simulated drones must move, drain their batteries and raise alerts believably enough to build ingestion, missions and alerting against, and tests must be able to inject failures (lost link, rejections, slow replies, fast drain, bad GPS) into one drone. All of it runs on the scheduler thread of ADR 0003 and must be deterministic under a controlled clock.

## Decision

- **Movement:** straight lines at constant speed, computed per scheduler tick with an explicit `dt`, using local flat-earth offsets (`dN = Δlat·111 320`, `dE = Δlon·111 320·cos lat`). Accurate enough inside the 5 km geofence. Climb 3 m/s, descend 2 m/s, at the same time as horizontal movement. Arrival within 1 m horizontally and 0.5 m vertically snaps to the target, so there is no overshoot.
- **Battery** (% per second): LANDED 0, ARMED 0.01, airborne `0.05 + 0.004·ground_speed (+0.03 while climbing)`, about 18 minutes at 10 m/s. At 0 % an airborne drone lands in place.
- **Alerts are latched per threshold:** `LOW_BATTERY` at ≤20 % (WARNING), `CRITICAL_BATTERY` at ≤10 % (CRITICAL, starts RETURN_HOME), `GEOFENCE_BREACH` when the reported position leaves the radius (re-armed once back inside), `LINK_RESTORED` when a link loss ends.
- **Determinism:** each drone has its own `random.Random` seeded from a stable hash (blake2b) of the configured seed and the drone ID. Same seed and same commands give the same telemetry.
- **Faults are state on the drone,** changed under the board lock and expired on scheduler ticks:
  - `LINK_LOSS` (1 to 600 s): commands are dropped (acked, not cached, not applied), nothing is published, delayed replies falling due are dropped, physics keeps running. Ends with ALERT `LINK_RESTORED`, unless the faults are cleared or the drone is reset.
  - `NACK_NEXT` (1 to 100): the next N new commands get NACK `INJECTED` and are not applied.
  - `REPLY_DELAY` (0.1 to 30 s): replies become scheduled one-shots on the board. No handler ever sleeps.
  - `BATTERY_DRAIN` (factor 1 to 100): multiplies the drain rate.
  - `GPS_DRIFT` (1 to 500 m): offsets the reported position only; arrival and physics use the true position.

## Consequences

- Unit tests drive physics and faults with a fake clock and fixed `dt`, with no sleeps.
- `GEOFENCE_BREACH` follows the reported position, so `GPS_DRIFT` near the edge can trigger it. This mirrors what a ground station would see.
- The model is not realistic flight dynamics (no wind, acceleration or energy model). That is out of scope for M1.

## Alternatives considered

- Haversine or geodesic math: more precise, but the error of the flat-earth step inside 5 km is far below the 1 m arrival tolerance.
- Delaying replies with `sleep` or a timer thread: breaks ADR 0003 (handlers never sleep, constant thread count).
- Global faults: simpler, but tests must target one drone without disturbing parallel workers.
- `random` module global state: not reproducible per drone once drones are spawned in a different order.
