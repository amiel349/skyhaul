# 0012 · Telemetry storage and retention

Status: accepted · 2026-11-24

## Context

The simulator sends HEARTBEAT every 1 s per drone, POSITION every 1 s while airborne, BATTERY every 5 s, plus MISSION_STATUS, ALERT, ACK and NACK. At demo scale (about 20 drones) that is roughly 35 stored messages per second, about 3 M rows a day. Delivery is at-least-once (ADR 0006 and the ingestor queue), so the same message can arrive twice. Dispatchers need history for missions and charts, not a permanent archive.

## Decision

- **Stored in `telemetry`:** POSITION, BATTERY, MISSION_STATUS and ALERT, keyed by `msg_id`, with the drone's `ts`, our `received_at`, the `correlation_id` and the payload as `jsonb`. Index `(drone_id, type, ts desc)`.
- **ACK and NACK update `commands`**, not `telemetry`.
- **HEARTBEAT is not stored.** It updates the Redis latest state and the link watchdog only, which removes about 40 % of the rows.
- **Dedup by `msg_id`:** `INSERT ... ON CONFLICT (msg_id) DO NOTHING`. Drone ALERTs also become `alerts` rows, unique on `msg_id`.
- **Retention 7 days** (`TELEMETRY_RETENTION_DAYS=7`). The ingestor deletes older rows hourly, in batches of 10,000 by `ts`. Its database role has DELETE on `telemetry` and nothing else.
- **A plain table, no partitioning, in M2.**

## Consequences

- Redelivery and replay leave the row count unchanged.
- No heartbeat history: link problems are visible through `LINK_LOST` alerts and Redis, not by querying old heartbeats.
- Batched deletes create dead tuples; autovacuum handles them at this volume. A much larger fleet would need partitions.
- **Path to partitioning:** range-partition `telemetry` by `ts` per day and drop old partitions instead of deleting. That needs DDL at run time, so either the owner role runs a maintenance job or a separate maintenance role exists. The primary key would become `(msg_id, ts)`, so dedup would have to include `ts` (which is in the envelope, so redeliveries still match).

## Alternatives considered

- Store everything, HEARTBEAT included: complete, but 40 % more rows for data nobody queries.
- Partition now (native or TimescaleDB): the right tool at scale, but adds DDL rights or an extension for a demo-sized table.
- Keep telemetry only in Redis streams: cheap, but no SQL history for missions and no durable evidence.
- Unlimited retention: the table grows without bound on a laptop.
