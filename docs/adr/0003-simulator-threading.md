# 0003 · Simulator threading model

Status: accepted · 2026-11-23

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
