---
name: web-component-tests
description: How to write and place component tests for the Vue dispatcher UI in web/. Use whenever you add or change a .vue file (component, view or App.vue), write a Vitest test for one, or add faketories, fake databases or fake endpoints for it.
---

# Web component tests

**Every `.vue` file has its own test file next to it with the same name, and that test renders and tests that one component.**

```
web/src/components/PanelSplitter.vue
web/src/components/PanelSplitter.test.ts
web/src/views/FleetView.vue
web/src/views/FleetView.test.ts
```

Applies to `components/`, `views/` and `App.vue`. Stores, composables and pure functions have their own `*.test.ts` next to them (plain jsdom, no browser).

> **Status.** The shared infrastructure below is being moved to `web/tests/` in two PRs (infrastructure and fakes first, then the query migration). Until they land, it still lives in `web/src/testing/` and each data-backed component has a `<Name>.fakes.ts`. Tests written in the meantime should already follow the query, interaction and structure rules in this file.

## Shared infrastructure: `web/tests/`

Not the repo-root `tests/` (that is the Python and Playwright framework).

```
web/tests/
  setup-browser.ts      # global hooks for component tests
  setup-unit.ts         # global hooks for unit tests
  render.ts             # the thin renderComponent helper
  worker.ts             # MSW worker (browser), node-server.ts (unit tests)
  reset.ts              # fake dbs register their reset() here
  public/mockServiceWorker.js
  faketories/           # one per domain: drone, live-state, command, user (+ index.ts)
  fake-db/              # one stateful in-memory db per domain, each with reset()
  fake-endpoints/       # one per domain: drones, commands, auth (+ index.ts)
  fake-socket.ts
```

- **Faketories** build one piece of typed test data with overrides: `droneFaketory.build({ link: "LOST" })`. No behaviour, no state. Never write a raw mock payload inside a `.test.ts`.
- **Fake db** is a small in-memory store per domain. It holds what the test put in and what the API changed (a `POST /commands` shows up in the next `GET /commands`). Each has `reset()`.
- **Fake endpoints** are MSW handlers per domain over that domain's fake db, typed from the generated OpenAPI types (`Ok<path, method>`, `Body<path, method>`, `apiUrl()`), so a contract change breaks the build, not just a test. The happy path for every domain is registered once in the setup, so a request with no handler still fails the test. A test overrides only what it needs through named helpers, for example `commandEndpoints.respondWith(429, { retryAfter: 12 })`, with `worker.use(...)`.
- **Never** mock `fetch`, an HTTP library or the API client module.
- The socket is faked with `fake-socket.ts`, which tests drive by calling the registered handlers.

## The mount helper: `renderComponent`

One thin helper in `web/tests/render.ts`:

```ts
await renderComponent(FleetList, { props, route: "/", user: { role: "dispatcher" } });
```

It does **only plumbing**: a fresh Pinia, an optional router with an initial route, an optional signed-in user with a role, and the global plugins. It does **no stubbing**, **no data seeding** and nothing specific to one component. Data comes from the test's own faketories and fake db. If a test needs something special it passes it as an option, never through a hidden default. Each test file still imports and renders its own component and asserts on it.

## What a component test does

- Renders **the component under test with its real children**. Do not stub children. Stub only a heavy third-party part where a real one makes the test slow or unreliable (for example the Leaflet map inside a view test), and say why in a comment. A test never renders a parent of the component it tests.
- Runs in the real browser project (Vitest browser mode) so layout, focus, pointer and canvas are real. Do not stub `HTMLCanvasElement` or `ResizeObserver`.
- **Black box.** Assert only on what is rendered, emitted events, navigation and network requests. Never read or write component internals (`wrapper.vm`, refs, methods). Calling the store through the UI is fine, reaching into it is not. A third-party library is the one exception (a spy on Leaflet's `panTo` in a map test).
- **Arrange, Act, Assert**, in that order, separated by a blank line. Keep `describe` flat, one per component. Name each `it` as the user-facing outcome: `"shows LOW when the battery is under 20%"`.
- One behaviour per `it`. Light mode applies (see CLAUDE.md): one test per behaviour the issue promises, no extra edge cases.
- **Fake timers** for debounce, polling, toast dismissal and countdowns: `vi.useFakeTimers()` then `vi.advanceTimersByTimeAsync(...)`. Never wait with a real `setTimeout`. The setup restores real timers after each test.
- **Isolation.** The setup resets after every test: fake dbs, endpoint overrides, Pinia, `localStorage`, `data-theme`, spies and timers. A test never relies on another test's state.

## Element queries, in this order

Use the browser-mode locators from `vitest/browser` (`page.getBy...`). Prefer the most user-centric one that works:

1. `getByRole` (with `name`) or `getByLabelText`: buttons, links, headings, inputs, checkboxes, selects.
2. `getByPlaceholder`: an input with no visible label.
3. `getByText`: non-interactive text (messages, badges, cells).
4. A display value check: the current value of a filled field.
5. `getByAltText` or `getByTitle`: images and icons without text.
6. `getByTestId`: last resort, for a container with no role or text. The `data-testid` attributes stay because the E2E page objects depend on them; component tests do not need them for most things.

Never query by CSS class, tag or structure. Assert presence with `await expect.element(locator).toBeInTheDocument()` (it retries until the element appears, so there is no separate "wait for" query) and absence with `.not.toBeInTheDocument()`. If a query needs a role or label that the component lacks, fix the component's accessibility: that is a real bug the test found.

## Interactions

Use `userEvent` from `vitest/browser`: `await userEvent.click(locator)`, `await userEvent.fill(locator, "text")`, `await userEvent.keyboard("{Enter}")`. It sends real browser events. Do not dispatch synthetic events by hand unless a library cannot be driven otherwise, and then say so in a comment.

## Skeleton

```ts
// FleetList.test.ts
import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import FleetList from "./FleetList.vue";
import { renderComponent } from "../../tests/render";
import { droneFaketory } from "../../tests/faketories";
import { fakeDb } from "../../tests/fake-db";

describe("FleetList", () => {
  it("shows LOW next to a drone whose battery is under 20%", async () => {
    fakeDb.drones.add(droneFaketory.build({ id: "d1", name: "Courier 1", battery_pct: 3 }));

    await renderComponent(FleetList, { user: { role: "dispatcher" } });

    await expect.element(page.getByRole("button", { name: /Courier 1/ })).toMatchTextContent("LOW");
  });

  it("filters the rows by the search text", async () => {
    fakeDb.drones.add(droneFaketory.build({ name: "Alpha" }), droneFaketory.build({ name: "Bravo" }));
    await renderComponent(FleetList, { user: { role: "dispatcher" } });

    await userEvent.fill(page.getByLabelText("Search drones"), "alp");

    await expect.element(page.getByText("Alpha")).toBeInTheDocument();
    await expect.element(page.getByText("Bravo")).not.toBeInTheDocument();
  });
});
```

Adjust imports to the installed versions. `toHaveTextContent("x")` is an exact match; use `toMatchTextContent("x")` for part of the text. Import `worker` from `tests/worker.ts`, never from a setup file (a test that imports a setup file runs its hooks twice).

## Running the tests

Run everything from `web/`.

| What | Command |
|---|---|
| Unit tests (stores, composables, pure functions; jsdom) | `pnpm test` |
| Component tests, headless (what CI runs) | `pnpm test:components` |
| One component file | `pnpm test:components src/components/PanelSplitter.test.ts` |
| One test by name | add `-t "Home and End"` |
| Watch them in a visible browser, slowed down | `pnpm test:components:watch` |
| Every `.vue` has a sibling test | `pnpm check:tests` (also in `make check`) |

- **First time on a machine:** `pnpm exec playwright install chromium` (about 100 MB, Playwright's own Chromium, not your Chrome).
- **Watching:** `pnpm test:components:watch` opens a visible Chromium with `VITEST_SLOWMO=600` so actions can be followed. The component config is headless with no slow motion unless `VITEST_SLOWMO` (ms) is set. Press `q` to quit watch mode.
- **Everything before a PR:** `pnpm lint && pnpm typecheck && pnpm test && pnpm test:components && pnpm build && pnpm check:tests`.
- **Adding packages:** pnpm refuses versions published less than 7 days ago (`minimumReleaseAge` in `pnpm-workspace.yaml`). Pin a version older than that. Never exclude a package to get around it. Keep `vitest`, `@vitest/browser-playwright` and `vitest-browser-vue` on matching, compatible versions.

### Keep the two kinds of tests apart

Both kinds are named `*.test.ts`, so the configs split them by location, not by suffix:
- the **component** project includes `src/{components,views}/[A-Z]*.test.ts` (a test named after its PascalCase `.vue` file) and `src/App.test.ts`;
- the **unit** project (`pnpm test`) includes everything else and excludes those paths. A unit test of a plain module that lives in `components/` (`avatar.test.ts`, `tiles.test.ts`) is lowercase, so it stays in the unit project.

A component test that shows up in `pnpm test` fails there, because it imports `vitest/browser`. Fix the include and exclude globs, not the test.

### Real-browser gotchas

- Layout limits depend on the viewport; the component project runs at 1280x800.
- A component alone on a page may have no size (a divider with no height, an absolutely positioned popup). Clicking it then waits until the test times out. Give it room in the test, or focus the element directly (`element.focus()`) when the behaviour is keyboard only.
- Do not stub the canvas, `ResizeObserver` or layout APIs: they are real here.
- To debug, watch it visibly with the command above, or run one file headed with `--browser.headless=false`.

## Before you finish a component change

- [ ] `Foo.vue` has `Foo.test.ts` beside it, testing `Foo` with its real children.
- [ ] Data comes from faketories, the fake db and fake endpoints in `web/tests/`; no raw payloads in the test, no mocked `fetch` or API client.
- [ ] Rendered through `renderComponent`; no stubs except a heavy third-party part, with a comment.
- [ ] Queries follow the priority order; `data-testid` only as a last resort. No CSS-class or tag selectors.
- [ ] `userEvent` for interactions; fake timers instead of real sleeps; Arrange, Act, Assert.
- [ ] `pnpm lint && pnpm typecheck && pnpm test && pnpm test:components && pnpm check:tests && pnpm build` pass in `web/`.
