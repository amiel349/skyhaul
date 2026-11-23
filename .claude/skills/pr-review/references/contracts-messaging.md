# Contracts and messaging

Applies to `contracts/**` and to any code that publishes or consumes RabbitMQ messages. See ADR 0002.

## Schemas
- One JSON Schema per message type, plus the envelope: `msg_id` (UUID), `correlation_id` (UUID), `drone_id`, `type`, `schema_version`, `ts` (RFC 3339 with timezone).
- Every numeric field has a unit in its name or description (meters, m/s, percent, degrees, WGS84) and a min/max. Enums for reason codes and states, not free strings.
- Required fields are listed. `additionalProperties: false` unless the ADR says otherwise.
- Each schema has valid and invalid example messages, and the contract tests run them.

## Versioning
- Adding an optional field is compatible. Removing a field, renaming it, making it required, narrowing a range or changing a type is breaking.
- A breaking change bumps `schema_version`, and the codec keeps reading the previous version until both sides have moved. A breaking change without a version bump is blocking.
- Generated Pydantic models and TS types are regenerated in the same PR. CI's drift check must pass.
- No test-only fields in the shared interface. Test control goes through the simulator's control API.

## Codec
- All encoding and decoding goes through the `Codec`. Hand-built JSON for messages anywhere else is blocking.
- Decode rejects oversized bodies before parsing, then validates. Encode validates too, so nothing invalid leaves either side.
- Errors are typed (unknown type, unsupported version, invalid message) and carry what the caller needs to send a NACK.

## RabbitMQ topology
- Commands go to the `fleet.cmd` topic exchange, telemetry to `fleet.telemetry`, routing key `drone.<id>.<type>`.
- Exchanges and queues are declared durable, messages are persistent.
- Consumers use manual ack after handling, with a bounded prefetch. `auto_ack=True` is blocking.
- Poison messages are rejected without requeue and dead-lettered, never redelivered in a loop.
- Every reply copies the request's `correlation_id`. Every message gets a fresh `msg_id`.
- Receivers are idempotent for duplicate `msg_id` (at-least-once delivery means duplicates happen).

## Changes to watch
- A new message type needs a schema, examples, a handler (or an explicit NACK), and tests on both sides.
- A new reason code or enum value needs both producer and consumer updated in the same PR.
- Topology changes (new exchange, queue, binding) need a note in the PR and an update to the architecture doc.
