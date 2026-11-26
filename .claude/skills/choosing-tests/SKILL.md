---
name: choosing-tests
description: How to decide which kind of test to write for a change in Skyhaul (unit, concurrency, contract, integration, component, API E2E, UI E2E, eval) and where it goes. Use when writing the test notes of an issue, planning tests for a change, or reviewing whether a test sits at the right layer.
---

# Choosing the right test

The rule from CLAUDE.md: **prove each behaviour at the lowest layer that can prove it.** Lower layers are faster, steadier and point at the cause. A higher layer is added only for what the lower ones cannot show: that the parts are wired together.

## The layers in this project

| Layer | Proves | Lives in | Run with |
|---|---|---|---|
| **Python unit** | one function, class or handler: rules, state machines, permissions, codec | `services/*/tests/` (`unit/`, `test_*.py`), `contracts/tests/` | `make test` (`pytest -m "not integration"`) |
| **Concurrency** | thread-safety of shared state (jobs board, outbox, shutdown) | `services/drone_sim/tests/concurrency/` | `make test`; run 3 times, not 10 |
| **Web unit** | a Pinia store, composable or pure function (merge rules, filters, validation) | next to the source, `*.test.ts` (jsdom) | `pnpm test` in `web/` |
| **Web component** | one Vue component in a real browser: what the user sees and does | next to the `.vue`, `Name.test.ts` | `pnpm test:components` |
| **Contract** | the shape agreed between parts: JSON Schemas, the OpenAPI spec, generated types, no drift | `contracts/`, `test_openapi_spec.py` | `make contracts-check`, `make openapi-check` |
| **Integration** | our code against real Postgres, RabbitMQ or Redis (Testcontainers): migrations, queries, publisher confirms, ingestor writes | `services/*/tests/integration/`, marker `integration` | `make test-integration` (needs Docker) |
| **API E2E** | a flow across services with no browser: REST call, broker, simulator, telemetry back | repo-root `tests/` (M4, not built yet) | pytest, httpx, simulator control client |
| **UI E2E** | a critical journey through the real browser and the real stack | repo-root `tests/` (M4, not built yet), Playwright | `make e2e` |
| **AI eval** | copilot tool choice and arguments, triage accuracy | repo-root `tests/` (M5/M6) | eval harness |

Roughly the pyramid: many unit and component tests, fewer integration and contract tests, a handful of API E2E flows, about 15 UI E2E journeys at most.

## Decide in this order

Ask these questions top to bottom and stop at the first yes. Write the test there.

1. **Can I prove it by calling one function or one store action with plain inputs?** Unit test. Most business rules end here: a mission state transition, a status merge order, payload validation, a battery threshold.
2. **Is the risk two threads touching the same state?** Concurrency test, only where there is real shared state.
3. **Is it about what one component shows or does** (text, label, enabled state, keyboard, an emitted event, an error message)? Component test. Fake the API with the fake endpoints, never the real one.
4. **Is it about the shape or version of a message or an endpoint** (a new field, a renamed type, a required property)? Contract test: change the schema or spec, regenerate, and let the drift check fail. Do not re-test the shape in other layers.
5. **Does it depend on a real database, broker or cache behaving as it does** (a SQL constraint, a migration, a publisher confirm, a routing key, Redis expiry)? Integration test. A mock cannot prove it.
6. **Is it a flow that crosses services** (a command goes through the API and the broker to the simulator, the ACK comes back and the status is stored)? API E2E, one happy path plus the failure cases that matter (NACK, timeout, link loss), using the simulator's fault injection.
7. **Is it a journey that only exists in the browser on the real stack** (log in, see a drone move, send a command, see ACKED; a fault shows an alert)? UI E2E. Only the critical journeys, and only if no lower layer can show it.

If a behaviour seems to need layer 7, first ask whether layers 3 plus 6 already prove it: a component test for what the user sees plus an API E2E for what the system does.

## Worked examples from this project

| Change | Test |
|---|---|
| Battery under 20% shows "LOW" | component (`BatteryBar`) |
| A late ACK beats TIMED_OUT in the command list | web unit (commands store) |
| `drone.state` older than the stored one is dropped | web unit (fleet store) |
| Search box filters rows by name or id | web unit (filter selector) + one component test of the box |
| New optional field on `POST /commands` | contract (schema + OpenAPI), then unit for the handler |
| Two API replicas must not double-publish a command | integration (real RabbitMQ) |
| A command to a drone in link loss times out and shows TIMED_OUT | API E2E with `LINK_LOSS` injected; the label itself is a component test |
| Dispatcher logs in, selects a drone, sends ARM, sees SENT then ACKED | UI E2E (one happy path) |
| Jobs board loses a job when two handlers upsert at once | concurrency |
| Migration adds a column | integration (upgrade, downgrade, upgrade again) |
| A viewer cannot send a command | unit for the permission check, API E2E for the 403, one component test that the form is hidden |

## Rules

- **One layer per behaviour.** Do not prove the same rule in unit, component and E2E. Higher layers assert the wiring, not the rule.
- **Every acceptance criterion maps to at least one test**, and the issue's test notes name the layer for each.
- **A UI E2E test needs a written reason** it cannot live lower. Keep the suite near 15 journeys.
- **Mock only the boundary you do not own.** Component tests fake the HTTP API; unit tests fake nothing they can build; integration tests do not mock the database.
- **Faults come from the simulator**, not from patched code, in E2E: `LINK_LOSS`, `NACK_NEXT`, `REPLY_DELAY`, `BATTERY_DRAIN`, `GPS_DRIFT`.
- **Light mode (see CLAUDE.md):** one test per promised behaviour, one happy path per feature at the E2E layer, extras go to issue #19.
- **If the right layer does not exist yet** (API E2E and UI E2E start in M4): write the lowest test that can exist now, and add the missing higher-layer test to the "Deferred to #19" list of the PR.

## Smells

- A UI E2E test for something a component test shows (a label, a disabled button).
- A unit test that mocks so much it only tests the mocks.
- An integration test that asserts business rules instead of real infrastructure behaviour.
- A test that passes when the feature is deleted (assertion too weak).
- A sleep instead of a wait on state.
- The same case written at three layers.

## Before you write a test

- [ ] I named the behaviour in one sentence.
- [ ] I went down the questions above and picked the first layer that can prove it.
- [ ] No lower layer already proves it.
- [ ] The test notes in the issue (or PR) say which layer and why.
