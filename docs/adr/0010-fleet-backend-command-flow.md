# 0010 · Fleet backend boundaries and command flow

Status: accepted · 2026-11-24

## Context

M2 adds the fleet backend: the API that dispatchers use and a worker that turns drone messages into rows. Both have to agree on who reads what from the broker, what a `202` for a command means, and how a command or mission reaches a final state when replies can be late, duplicated or overtaken by telemetry. The M1 design note warns that a MISSION_STATUS can arrive before the START_MISSION ACK. ADR 0006 gives at-least-once delivery and per-drone order, and says the backend needs its own timeout because unknown or unreachable drones stay silent.

## Decision

- **The ingestor owns every inbound message**, ACK and NACK included. The API never consumes AMQP: no reply queues, no read permission, no consumer thread. The ingestor is one thread (one consumer loop that also flushes, sweeps and watches links), so its in-memory state has a single owner and no locks.
- **Commands are asynchronous.** `POST /drones/{id}/commands` stores the command as `PENDING`, publishes it, and answers `202` once the broker has confirmed it (`SENT`). The drone's reply arrives later as a `command.updated` event and in `GET /commands/{id}`.
- **One `CommandPublisher` thread per API process** is the only user of that process's AMQP connection (pika is not thread-safe). Request threads hand it a message and wait on a `Future` that the publisher always resolves: confirmed, nacked, unroutable, or dropped unsent because its 5 s deadline passed. A full hand-off queue (1,000) is a 503. The request thread therefore never guesses about a publish whose outcome it does not know.
- **Every state change is a compare-and-set** in SQL: `UPDATE ... WHERE id = :id AND status|state = ANY(:allowed_from)`. Duplicates, redeliveries and out-of-order messages cannot move a command or a mission backwards; terminal states absorb.
- **Late replies win.** The ingestor's sweeper marks `SENT` commands `TIMED_OUT` after `COMMAND_TIMEOUT_S` (10 s). A reply that arrives afterwards still sets `ACKED` or `NACKED`, with `late = true`. A timeout means "we stopped waiting", not "the drone refused".
- **Mission launch is pipelined.** `POST /missions {start: true}` sends ARM (if the drone is LANDED) and START_MISSION back to back without waiting for the first ACK. Per-drone order (ADR 0006: one queue, one active consumer) guarantees the simulator handles ARM first; if ARM is NACKed, START_MISSION is NACKed `INVALID_STATE`.
- **Mission state machine:** `PLANNED → DISPATCHING → ACTIVE → COMPLETED | ABORTED`, plus `DISPATCHING → FAILED` (NACK, or no ACTIVE within 60 s), `DISPATCHING → COMPLETED | ABORTED` (final status overtook the ACK) and `PLANNED → CANCELLED`. `DISPATCHING → ACTIVE` accepts either the START_MISSION ACK or MISSION_STATUS ACTIVE, whichever comes first. A partial unique index allows one live mission per drone.
- **One API process in M2** (gunicorn, one worker, 32 threads). Multi-replica API is deferred to M4.

## Consequences

- One component (the ingestor) decides the fate of every reply, so the ACK-versus-telemetry race is handled in one place, with CAS rather than ordering assumptions.
- A `202` promises "the broker has it", nothing more. The UI must show `SENT` as in flight and update from events.
- The commands table can show `TIMED_OUT` and later `ACKED` for the same command; dashboards must read `late`.
- A crash between commit and publish leaves a `PENDING` row; the sweeper fails it after 30 s.
- Pipelining saves a round trip, but couples mission launch to ADR 0006's ordering. A future transport without per-drone order must revisit it.
- Scaling the API beyond one process needs the Socket.IO message queue and sticky sessions (ADR 0014), and one publisher thread per process stays the rule.

## Alternatives considered

- Synchronous commands (wait for the drone's ACK in the request): simple for clients, but ties up request threads for up to the command timeout and needs reply queues in the API.
- Reply queue per API worker: lets the API see its own ACKs, but splits inbound handling across two services and reintroduces the ordering race between ACK and telemetry.
- Publishing from request threads with a connection pool: more connections, and every thread would have to handle confirms and broker nacks itself.
- Timeout wins (ignore late replies): hides what the drone actually did.
- Wait for the ARM ACK before START_MISSION: one extra round trip and a second state to time out, with no gain given per-drone order.
