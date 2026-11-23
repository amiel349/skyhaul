# 0002 · Message contracts in JSON Schema

Status: accepted · 2026-11-23

## Context

The drones are treated as a separate system built by another team against a shared interface. Both sides need one source of truth for message shapes, and the backend must not be able to tell the simulator from a real drone.

## Decision

- Message schemas live in `contracts/` as JSON Schema, versioned with `schema_version` in the envelope.
- Pydantic models (Python) and TypeScript types are generated from the schemas.
- A `Codec` class is the only place that encodes and decodes. It validates every message and rejects unknown versions.
- Envelope: `msg_id`, `correlation_id`, `drone_id`, `type`, `schema_version`, `ts`.
- Routing: `fleet.cmd` and `fleet.telemetry` topic exchanges, key `drone.<id>.<type>`.

## Consequences

- Messages stay readable in the RabbitMQ management UI while debugging.
- Contract tests can check both sides against the same schemas.
- Because the codec hides the format, a later switch to protobuf touches one class.

## Alternatives considered

- Protobuf: smaller and faster, but opaque in the queue UI and heavier tooling for a demo-scale system.
