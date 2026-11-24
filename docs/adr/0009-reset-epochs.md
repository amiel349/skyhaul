# 0009 · Reset epochs and namespaced drone IDs

Status: accepted · 2026-11-23 · amended 2026-11-24 · amends [0004](0004-test-isolation.md)

## Context

ADR 0004 isolates parallel tests by namespace and a `POST /reset` before each test. Two gaps remain. First, messages produced for a namespace just before its reset can still be waiting in the simulator's outbox, and would be published into the next test. Second, a namespace prefix must never match another namespace's drones (`gw1` against `gw10-…`).

## Decision

- **Namespaces** match `^[a-z0-9]{1,16}$` (no dash). Drone IDs are `<namespace>-dNNN`, so the prefix `namespace + "-"` is unambiguous and every ID is a valid `DroneId`.
- **Every namespace has an epoch.** `reset(ns)` runs atomically under the board lock: it removes the namespace's drones, jobs, faults, delayed replies and dedup caches, resets its ID counter, and increments `epoch[ns]`.
- **Every outgoing message records the epoch** it was produced in. The publisher checks `is_current(namespace, epoch)` (under the board lock, before encoding) and drops stale messages, counting them as `dropped_stale` in `/stats`.
- **Stamping happens only at the lock boundary** (`World.stamp`, `World.delay`, `due()`, `JobsBoard.stamp`), never by the producer guessing. `Outgoing.namespace` and `epoch` default to unstamped (`epoch = -1`), which is never current, so a message nobody stamped is dropped and counted rather than published.
- **Reset fences the in-flight publish.** The check and the publish are not atomic (no network under the lock), so a message that passed its check just before the bump could still go out. After bumping, reset waits up to 2 s for the publisher's in-flight message to be published or dropped. If it doesn't finish in time, `/reset` answers 503 `reset_unconfirmed`; the board is already reset, and resetting again is safe.
- `/reset` returns the new epoch. A reset of an empty namespace succeeds; a missing namespace or a wildcard is a 400.

## Consequences

- After `/reset` returns, nothing produced before it is published, whatever is still in the outbox. Tests need no sleeps or drains between cases.
- IDs restart at `d001` after a reset, so tests can rely on stable IDs.
- The publisher takes the board lock briefly per message; this is measured under the 200-drone load test (invariant 6).
- Deleting one drone (`DELETE /drones/{id}`) does not bump an epoch, so messages already in the outbox for it can still go out within milliseconds. Reset, not delete, is the isolation tool.

## Alternatives considered

- Draining or filtering the outbox on reset: the outbox is a `queue.Queue` owned by two threads; rewriting it under a lock would block producers and couple reset to queue internals.
- A fresh simulator per test: rejected in ADR 0004.
- Random drone IDs per test: no stale-message protection, and harder to read in logs.
