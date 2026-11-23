# 0003 · Simulator threading model

Status: accepted · 2026-11-23 · amended 2026-11-23

## Context

The simulator answers commands, sends periodic telemetry for many drones, and moves drones on its own. A naive design starts threads per message type and shares one pika connection, which causes three problems: pika connections are not thread-safe, threads read state while another thread writes it, and the thread count grows with the number of drones.

## Decision

Exactly three long-lived threads, each with one job:

1. **Consumer** (main thread): owns its own connection, decodes, calls the handler from a decorator registry, acks after handling. Never sends, never sleeps.
2. **Scheduler**: every 100 ms asks the jobs board what is due, applies movement ticks, and puts messages in the outbox. Stops on a shared stop event.
3. **Publisher**: the only thread that publishes, on its own connection, reading from a bounded outbox queue.

Shared state:

- **Jobs board**: periodic jobs keyed by `(drone_id, type)`, with values, period and optional tick, behind one lock. Commands and ticks take the same lock, so they cannot overwrite each other.
- **Outbox**: a bounded `queue.Queue`. When it's full, the consumer blocks and RabbitMQ holds the messages (backpressure).
- **Codec**: validation and encoding behind a lock.

A fourth thread serves the HTTP control API (spawn, faults, reset). It changes state only through the jobs board's lock.

## Consequences

- The thread count is constant no matter how many drones run.
- No connection is shared, no state is read while being written.
- Replies leave in the order they were produced, and carry the request's `correlation_id`.

## Alternatives considered

- asyncio: a good fit for the I/O, and a natural follow-up. Threads were kept to match the original design being improved on, and because the logic is easy to reason about with one lock.

## Amendment · 2026-11-23 · scheduler timing and failure isolation

Settled while building the jobs board (#7); details in `docs/architecture/m1-design.md` section 5.

- **Due times stay on the period grid.** After a job emits, `next_due += period`; if that is still `<= now` (a stall of a whole period or more), `next_due = now + period`. Using `now + period` every time adds each tick's wake-up delay to the period, so a 1 s heartbeat averaged 1.05 to 1.1 s on a real clock. After a stall the job still emits exactly once, with no catch-up burst.
- **No rollback for handlers.** A handler that raises after changing the board keeps its changes. Handlers therefore check every precondition before they mutate anything. The consumer logs the traceback after the board transaction, sends NACK `INTERNAL_ERROR` and acks.
- **A raising job is isolated.** If a job's `tick` or `render` raises, the scheduler skips that job for this tick and goes on with the others. It logs the error after releasing the board lock, so one faulty job never stops other drones' telemetry.
- The control API thread is the fourth long-lived thread mentioned above. The count stays four regardless of how many drones exist.
