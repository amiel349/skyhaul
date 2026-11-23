# Simulator and concurrency

Applies to `services/drone_sim/**`, and to any code that starts threads or shares state between them. The design is fixed by ADR 0003; a change that breaks one of these rules is blocking unless it comes with a new ADR.

## The three-thread model
- Exactly three long-lived threads: consumer (main thread), scheduler, publisher. Plus one control API thread. A change that starts a thread per drone, per message type or per request is blocking.
- The consumer only decodes, dispatches to a handler, and acks. It never publishes and never sleeps.
- The scheduler wakes every 100 ms with `stop.wait(0.1)`, never `time.sleep`.
- The publisher is the only code that calls `basic_publish`. Any publish from another thread is blocking.
- Each thread that talks to RabbitMQ owns its own pika connection. A pika connection or channel shared between threads is blocking (pika is not thread-safe).

## Handlers
- Handlers are fast: no sleeping, no I/O, no publishing. They put a reply in the outbox or change jobs on the board.
- Handlers are registered with `@handles("TYPE")`. Registering the same type twice fails at startup.
- An exception in a handler becomes a NACK with the request's `correlation_id`; it never kills the consumer.
- Unknown types and unsupported versions get a NACK with a reason code.

## Jobs board
- All reads and writes go through the board's single lock. Any access to job state outside the lock is blocking.
- Check-then-act (does the job exist? create it) happens inside one lock acquisition.
- Ticks (movement, battery drain) run under the same lock as command upserts, so neither overwrites the other.
- Reads return copies. Callers never receive a reference to live job state.
- The lock is held only for in-memory work: never while encoding, publishing, logging to a slow sink, or calling the network.
- Only one lock exists for job state. A second lock needs a documented acquisition order to avoid deadlock.

## Outbox and backpressure
- The outbox is a bounded `queue.Queue`. When it is full, the consumer blocks and RabbitMQ holds the messages. An unbounded queue or a dropped message is blocking.
- Replies carry the request's `correlation_id`. Messages leave in the order they were produced.

## Lifecycle
- One stop event shuts down the scheduler and publisher. Shutdown drains the outbox first, then closes connections.
- SIGTERM triggers the same path. The process exits with code 0 after a clean shutdown.
- No daemon threads that are killed mid-write.

## Faults and namespaces
- Faults (link loss, NACK next, delay, battery drain, GPS drift) live on the board and are applied by the scheduler or publisher. A delay is scheduled, never implemented with `sleep`.
- A namespace reset removes that namespace's jobs, faults and state under the lock, and nothing from other namespaces.

## Tests for this area
- Concurrency tests run many threads many times and assert an invariant (exactly one job, no lost update, exact counts).
- Periods and timeouts are tested on a controlled clock, not with real sleeps.
- Thread count is asserted, so a regression to thread-per-drone is caught.
- Never accept "the race is rare" as a reason. Ask what guarantees it: one owner thread, a lock, or an atomic operation.
