# 0004 · Test isolation by namespace and reset

Status: accepted · 2026-11-23

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
- The control API is a test-only surface: it runs only in the test compose profile, on the internal network, behind a token.
