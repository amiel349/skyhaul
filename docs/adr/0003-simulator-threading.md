# 0003 · Simulator threading model

Status: accepted · 2026-11-23 · amended 2026-11-23

## Context

The simulator answers commands, sends periodic telemetry for many drones, and moves drones on its own. A naive design starts threads per message type and shares one pika connection, which causes three problems: pika connections are not thread-safe, threads read state while another thread writes it, and the thread count grows with the number of drones.

## Decision

Exactly three long-lived threads, each with one job:

1. **Consumer** (main thread): owns its own connection, decodes, calls the handler from a decorator registry, acks after handling. Never sends, and never sleeps while handling a message (see the second amendment for the two bounded waits it does have).
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

Settled while building the jobs board (#7, PR #12); details in `docs/architecture/m1-design.md` section 5.

- **Due times stay on each job's own grid.** On time, `next_due += period`. Using `now + period` instead adds each tick's wake-up delay to the period, so a 1 s heartbeat averaged 1.05 to 1.1 s on a real clock. After a stall, `next_due` jumps to the first slot on the job's grid after `now` (`floor((now - next_due) / period) + 1` slots): the job emits once, with no catch-up burst, and keeps its phase. A non-finite `now` raises `ValueError`.
- **Jobs are frozen.** A `Job` validates `period_s` (finite, > 0) and `next_due` (finite) when built. Only the contents of `Job.values` change in place; identity, period and correlation id change only by upserting a new `Job`. The board keeps the real next due time in its own entry, and `World.get` returns a private copy carrying it. On upsert, the stored `next_due` is kept if the period is unchanged; otherwise the earlier of the stored and the requested one wins.
- **Transactions are copy-on-write.** Changes made through the `World` are staged and committed only when the `with` block ends normally, and dropped if it raises or on `world.discard()`. `dispatch` discards when a handler fails, so a failed handler leaves the board unchanged (this replaces the earlier "no rollback" rule). One `dispatch` per transaction.
- **No nested locking.** A public board call from the thread that already holds the lock raises `RuntimeError` instead of deadlocking.
- **A raising job is isolated.** `due()` returns `DueResult(messages, errors)`. A job whose `tick` or `render` raises is reported in `errors`, its output for that call is dropped, and the other jobs go on. The scheduler logs the errors after the lock is released. Reply, tick-event and render payloads are deep copies, so the publisher never reads live state.
- The control API thread is the fourth long-lived thread mentioned above. The count stays four regardless of how many drones exist.

## Amendment · 2026-11-24 · consumer loop, waits and daemon threads

Settled while wiring the threads to RabbitMQ (#7, PR #15).

- **The consumer polls instead of blocking.** It runs `process_data_events(time_limit=0.2)` while the stop event is clear, instead of `start_consuming` plus `add_callback_threadsafe(stop_consuming)`. The SIGTERM/SIGINT handler only sets the stop event and never calls pika from inside a signal handler. Cost: up to 0.2 s more to stop.
- **Two bounded waits, never while handling a message.** The 0.2 s poll above is the broker waiting for deliveries, not the consumer sleeping. After a lost connection, the consumer waits 0.5 s on the stop event before reconnecting, so a channel error right after connecting can't become a tight loop. Neither wait holds the board lock or delays a message already received.
- **The scheduler and publisher are daemon threads**, joined by the main thread with bounds. A publish that a blocked broker never confirms must not keep the process alive past the 10 s shutdown limit. Anything still unsent when the 5 s drain ends is counted and logged as dropped, so nothing is lost silently.
- **A broker nack is retried a bounded number of times.** The publisher offers one message at most 5 times (backing off 0.05 s, doubling, capped at 1 s; the stop event ends the wait early). After that it drops the message, counts and logs it, and moves on, so one message the broker keeps refusing can't pin the publisher, fill the outbox and stall the consumer.
