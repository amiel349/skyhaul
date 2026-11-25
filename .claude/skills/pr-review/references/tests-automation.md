# Tests and automation

Applies to `tests/**`, `**/tests/**`, `web/**/*.spec.ts` and `web/**/*.test.ts`. The test infrastructure is the centerpiece of this project; review it with the same rigor as product code. See ADR 0004.

## The pyramid
- Each behavior is proven at the lowest layer that can prove it: unit, then contract, integration, API E2E, and UI E2E last.
- A new UI E2E test needs a reason it cannot live lower. UI E2E covers critical dispatcher journeys only.
- Every acceptance criterion maps to at least one test, and the test name says which behavior it proves.

## Test quality
- One behavior per test. Arrange, act, assert are visible. No logic (loops, ifs) in the test body unless it is a parametrized table.
- Assertions check the outcome the user or caller sees, specific values, not just "no exception" or "status is truthy".
- A test must be able to fail. Mocking the unit under test, or asserting on the mock's own return value, proves nothing.
- Failure paths are tested, not only the happy path: NACK, timeout, permission denied, invalid input, link loss.
- Parametrize instead of copy-pasting near-identical tests.

## Waiting and time
- No `time.sleep`, `page.wait_for_timeout` or fixed delays. Wait on a condition with a timeout: UI state, a telemetry message, an event.
- Periodic and timeout behavior is unit-tested on a controlled clock.
- Timeouts are explicit and justified, not inflated to hide slowness.

## Isolation and parallelism
- Tests run in parallel with pytest-xdist and in any order. No shared mutable state, no dependence on another test's data.
- Each worker uses its own namespace (tenant and drone IDs). Fixtures reset the simulator namespace before each test.
- Test data is created through builders and the public API, not by writing to the database behind the API (except in DB-level integration tests).
- Fixtures clean up after themselves, or rely on namespace reset, never on test order.

## Framework layers
- Clients (API, simulator control, WebSocket) → page and component objects → flows → fixtures → tests. Layers don't skip each other.
- Page objects locate elements by `data-testid` and expose user actions. They don't contain assertions.
- No CSS or XPath selectors tied to layout or styling.
- Failure scenarios are set up through the simulator control API, never through the shared message interface.

## Integration tests
- Real dependencies through Testcontainers (Postgres, RabbitMQ, Redis), not mocks, for code whose correctness depends on them.
- Containers are started once per session and isolated per test by namespace or transaction.

## Evidence and flakiness
- On failure: trace, screenshot and logs filtered by `correlation_id` are attached to the report.
- Evidence is scrubbed of auth headers, cookies and tokens before upload. Unscrubbed evidence is blocking.
- A test that passes on rerun is recorded as flaky. Retries that hide failures without recording them are blocking.
- Skipped tests carry a reason and a linked issue. A new `skip` without one is should-fix.

## CI for tests
- New suites are wired into CI and run on every PR or on a stated schedule.
- Coverage gates are not lowered to make a PR pass.
