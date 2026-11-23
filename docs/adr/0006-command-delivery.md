# 0006 · Command delivery semantics

Status: accepted · 2026-11-23

## Context

Commands travel from the fleet API (and from tests and the MCP server) to the simulator over RabbitMQ. The backend team must be able to rely on clear guarantees: whether a command can be lost or duplicated, what an ACK means, what happens to a message the simulator cannot read, and what a drone that does not exist does. ADR 0003 fixes the simulator at three threads, which rules out one consumer per drone.

## Decision

- **One durable quorum queue, `drone_sim.cmd`**, bound to `fleet.cmd` with `drone.*.*`, with `x-single-active-consumer=true`, `x-delivery-limit=5` and `x-dead-letter-exchange=fleet.dlx`. Command senders declare it too (through `declare_topology`), so commands sent before the simulator first starts are kept. Commands are published persistent, with `mandatory=True`.
- **Bounded queues.** `drone_sim.cmd` has `x-max-length=10000` with `x-overflow=reject-publish`: when it is full, the broker nacks new publishes, so senders (which use publisher confirms) see back-pressure and no accepted command is dropped silently. `fleet.dead` has `x-max-length=10000` with `x-overflow=drop-head`: it is evidence for humans, and under a flood the newest evidence matters most. Queue arguments cannot change after the first declare, so these bounds are fixed in v1; changing them needs a new queue name.
- **At-least-once with dedup.** The consumer uses manual ack, `prefetch_count=32`, and acks only after the replies are in the outbox (or after a deliberate drop). A redelivered `msg_id` gets its cached replies again and is not applied twice. The cache holds the last 256 commands per drone.
- **ACK means accepted, not completed.** Completion is visible in telemetry. ACK and NACK carry the command's `correlation_id` and `command_msg_id`.
- **Poison handling.** Undecodable bodies, invalid envelopes and routing-key mismatches are rejected without requeue and land in `fleet.dead`. Decodable but wrong commands (unknown type, unsupported version with a readable envelope, invalid payload) get a NACK and are acked. A handler that raises gets NACK `INTERNAL_ERROR` and is acked. A message that keeps crashing the process is dead-lettered by the delivery limit.
- **An unknown drone gets silence.** The command is logged, acked and counted (`unknown_drone_commands` in `/stats`), exactly as a real drone out of reach would behave.
- **Dispatch order:** unknown drone, link-loss fault, dedup cache, injected NACK, handler, cache, reply delay (see `docs/architecture/m1-design.md` section 5). Link loss comes before dedup so a drone out of reach answers nothing, not even a duplicate.

## Consequences

- Per-drone order holds for free: one queue, one active consumer, replies in handling order on one publisher channel.
- The backend must implement its own timeout for commands to unknown or unreachable drones; the simulator will never tell it "no such drone". This is the behavior it needs against real hardware anyway.
- Head-of-line blocking across drones is possible but harmless, because handlers never block.
- `fleet.dead` needs occasional human inspection; nothing consumes it. Past 10 000 entries the oldest are dropped.
- A command sender that ignores publisher confirms would lose commands silently when the command queue is full. Senders must use confirms.

## Alternatives considered

- One queue per drone: natural isolation, but spawn and reset would need broker round trips and the consumer count would grow with the fleet, against ADR 0003.
- Classic queue: lighter, but no delivery limit (a crash loop redelivers forever) and weaker durability.
- Unbounded queues: simplest, but a flood of commands or poison messages could fill the broker's disk and stop every service.
- `drop-head` on the command queue: never blocks senders, but silently drops the oldest accepted commands, which a sender already counts as delivered.
- NACK `UNKNOWN_DRONE`: friendlier, but trains the backend to expect an answer that real drones cannot give.
- Requeue on handler error: retries a deterministic bug forever and blocks the queue.
