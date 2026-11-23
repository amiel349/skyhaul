# 0004 · Test isolation by namespace and reset

Status: accepted · 2026-11-23 · amended 2026-11-23 (see [0009](0009-reset-epochs.md))

## Context

E2E tests need a clean state. Starting a fresh environment per test guarantees that, but costs startup time on every test and rules out fast parallel runs.

## Decision

- One long-running stack per CI shard.
- Each pytest-xdist worker gets its own namespace: its own tenant and drone IDs. Workers never touch each other's data.
- A fixture calls the simulator's `POST /reset` for the worker's namespace before each test: it stops that namespace's periodic jobs, clears faults and state.
- The fleet API has no reset endpoint. Test data is created through the public API and scoped by tenant.

## Consequences

- Tests run in parallel without a fresh environment per test.
- A leak between tests shows up as a cross-namespace bug, which is itself worth finding.
- The control API is a test-only surface: it runs only in the test compose profile, behind a token. (Amended below: it is published on 127.0.0.1.)

## Amendment · 2026-11-23 · host binding and reset epochs

- **Reachability.** Tests and the MCP server run on the host, not inside the compose network, so the control API has to be reachable from there. It is published only on `127.0.0.1:8100`, never `0.0.0.0`, and only under the compose `test` profile (`make sim` for local demos). Every route except `/healthz` needs `Authorization: Bearer <SIM_CONTROL_TOKEN>`. The token has no default and must be at least 32 characters. The production deployment never includes the simulator.
- **Reset epochs.** `POST /reset` bumps a per-namespace epoch, and the simulator drops any outgoing message produced under an older epoch. Messages queued before a reset are therefore never published after it. Namespaces match `^[a-z0-9]{1,16}$` and drone IDs are `<namespace>-dNNN`, so one namespace never matches another's drones. See [0009](0009-reset-epochs.md).
