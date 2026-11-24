# 0006 · Command delivery semantics

Status: accepted · 2026-11-23 · amended 2026-11-24 · amended by [0013](0013-least-privilege-infrastructure.md)

## Context

Commands travel from the fleet API (and from tests and the MCP server) to the simulator over RabbitMQ. The backend team must be able to rely on clear guarantees: whether a command can be lost or duplicated, what an ACK means, what happens to a message the simulator cannot read, and what a drone that does not exist does. ADR 0003 fixes the simulator at three threads, which rules out one consumer per drone.

## Decision

- **One durable quorum queue, `drone_sim.cmd`**, bound to `fleet.cmd` with `drone.*.*`, with `x-single-active-consumer=true`, `x-delivery-limit=5` and `x-dead-letter-exchange=fleet.dlx`. Command senders declare it too (through `declare_topology`), so commands sent before the simulator first starts are kept. The MCP server is the exception (see the amendment). Commands are published persistent, with `mandatory=True`.
- **Bounded queues.** `drone_sim.cmd` has `x-max-length=10000` with `x-overflow=reject-publish`: when it is full, the broker nacks new publishes, so senders (which use publisher confirms) see back-pressure and no accepted command is dropped silently. `fleet.dead` has `x-max-length=10000` with `x-overflow=drop-head`: it is evidence for humans, and under a flood the newest evidence matters most. Both queues also have `x-max-length-bytes=655360000` (10,000 × the 64 KiB message limit): the message count alone does not bound bytes when a publisher skips the Codec. Queue arguments cannot change after the first declare, so these bounds are fixed in v1; changing them needs a new queue name.
- **At-least-once with dedup.** The consumer uses manual ack, `prefetch_count=32`, and acks only after the replies are in the outbox (or after a deliberate drop). A redelivered `msg_id` gets its cached replies again and is not applied twice. The cache holds the last 256 commands per drone.
- **ACK means accepted, not completed.** Completion is visible in telemetry. ACK and NACK carry the command's `correlation_id` and `command_msg_id`.
- **Poison handling.** Undecodable bodies, invalid envelopes and routing-key mismatches are rejected without requeue and land in `fleet.dead`. Decodable but wrong commands (unknown type, unsupported version with a readable envelope, invalid payload) get a NACK and are acked. A handler that raises gets NACK `INTERNAL_ERROR` and is acked. A message that keeps crashing the process is dead-lettered by the delivery limit.
- **NACKs go only to the drone the routing key names.** For `UnsupportedVersion` the envelope could not be validated, so `hint.drone_id` is only a claim: the consumer sends NACK `UNSUPPORTED_VERSION` only if the routing key's `<id>` equals it (and, when `hint.type` is readable, the routing key's `<type>` equals it too), and dead-letters the message otherwise. NACK `detail` comes from `DecodeError.detail` (≤ 256 characters, never input values).
- **An unknown drone gets silence.** The command is logged, acked and counted (`unknown_drone_commands` in `/stats`), exactly as a real drone out of reach would behave.
- **Dispatch order:** unknown drone, link-loss fault, dedup cache, reply-delay cap, injected NACK, handler, cache, reply delay (see `docs/architecture/m1-design.md` section 5). Link loss comes before dedup so a drone out of reach answers nothing, not even a duplicate. The dedup cache holds only the ACK or NACK, so a duplicate never replays telemetry a handler returned with it (a final MISSION_STATUS).

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

## Amendment · 2026-11-24 · version NACK type check, MCP declares, no-reply result

Settled while wiring the consumer (#7, PR #15) and building the MCP server (#11, PR #21).

- **`UNSUPPORTED_VERSION` checks the type too.** A message whose readable `hint.type` differs from the routing key's `<type>` is dead-lettered instead of NACKed, stricter than the original rule. A NACK then names exactly the drone and the type the broker routed.
- **The MCP server does not declare `drone_sim.cmd`.** It declares only `fleet.cmd`, `fleet.telemetry` and its own exclusive reply queue. Otherwise its `mandatory=True` publish could never come back unroutable: the queue would always exist, and a developer would wait out the timeout instead of hearing "the simulator is not running". Once the simulator has run, its durable queue remains, so a command sent while it is stopped is kept and the tool answers `no_reply`. The test command client and the fleet API (M2) still call `declare_topology`.
- **Silence is a result, not an error.** `send_command` returns `result: no_reply` with the likely causes (link loss, a REPLY_DELAY longer than the wait, an unknown drone, a stopped simulator), and its `timeout_s` goes up to 60 so a 30 s REPLY_DELAY fits. The fleet API's command timeout (M2) treats silence the same way.

## Amendment · 2026-11-24 · `broker-init` owns the topology (M2, #23)

Decided with the M2 design ([ADR 0013](0013-least-privilege-infrastructure.md)).

- **The fleet services declare nothing.** A one-shot `broker-init` declares the whole topology through `declare_topology` and `declare_ingestor_queue` and creates the per-service broker users. The fleet API does not call `declare_topology`, which replaces "Command senders declare it too" for the API (and the M2 sentence in the amendment above). Commands sent before the simulator first starts are still kept, because `broker-init` has already declared `drone_sim.cmd`.
- **The simulator and the test command client still declare**, with the admin broker user (test profile only, until M4).
- **The MCP exception stands:** the MCP server declares only `fleet.cmd`, `fleet.telemetry` and its exclusive reply queue, never `drone_sim.cmd`. Once `broker-init` has run, `drone_sim.cmd` exists even if the simulator never started, so in a full stack a stopped simulator shows up as `no_reply` rather than unroutable. In the simulator-only profile the signal works as before.
- **The API's command timeout is the ingestor's sweeper** (ADR 0010); it treats silence as the amendment above says.
