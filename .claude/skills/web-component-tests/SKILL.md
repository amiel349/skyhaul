---
name: web-component-tests
description: How to write and place component tests for the Vue dispatcher UI in web/. Use whenever you add or change a .vue file (component, view or App.vue), write a Vitest test for one, or add fake data and fake endpoints for it.
---

# Web component tests

One rule: **every `.vue` file has its own test file next to it with the same name, and that test renders only that component.**

```
web/src/components/PanelSplitter.vue
web/src/components/PanelSplitter.test.ts      # always
web/src/components/FleetList.vue
web/src/components/FleetList.test.ts          # always
web/src/components/FleetList.fakes.ts         # only if FleetList needs data or endpoints
```

Applies to `components/`, `views/` and `App.vue`. Stores, composables and pure functions have their own `*.test.ts` next to them (plain jsdom, no browser).

## What a component test does

- Renders **one** component. Child components that are not the point are stubbed. It does not render the app, the router or a parent view unless the component under test *is* that view.
- Mounts the component directly in the test file. There is no shared `mount()` helper: each file shows what it renders, with which props, store state and fakes.
- Asserts what a user sees and does: text, roles, `data-testid`, clicks, keys, emitted events. Never component internals.
- One behaviour per `it`, named as a sentence. Light mode applies (see CLAUDE.md): one test per behaviour the issue promises, no extra edge cases.
- Runs in the real browser project (Vitest browser mode) so layout, focus, pointer and canvas are real. Do not stub `HTMLCanvasElement` or `ResizeObserver` there.

## Fakes: per component, never global

A component that reads or writes through the API gets its own `<Component>.fakes.ts` with:

- a **fake db**: a small in-memory, stateful store with only the entities this component touches and a `reset()`;
- **fake endpoints**: MSW handlers over that db, typed from the generated OpenAPI types (`web/src/api/types.ts`) so a contract change breaks the build, not just the test.

Rules:
- Another component's fakes are never imported. If two components need the same shape, share a **factory** instead.
- **Factories** (`makeDrone()`, `makeAlert()`, `makeUser()` ...) build typed test data with overrides: `makeDrone({ link: "LOST" })`. They live in `web/src/testing/factories/`, one file per entity, and have no behaviour.
- Network is faked at the HTTP layer (MSW), never by stubbing the API client module.
- The socket is faked with `web/src/testing/fake-socket.ts`, which tests drive by calling the registered handlers. Use it with `vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"))`.
- Type fake endpoints with `Ok<path, method>` and `Body<path, method>` from `web/src/testing/api.ts` and build URLs with `apiUrl()`.
- A fake db registers its `reset()` with `registerReset()` from `web/src/testing/reset.ts`; the setup file runs them after each test.

## Shared setup: `web/src/testing/setup-browser.ts`

Global hooks only, registered once as the Vitest setup file:
- start the MSW worker (`web/src/testing/worker.ts`) before all tests and stop it after;
- after each test: reset the fake db, new Pinia, clear `localStorage`, remove `data-theme`, restore fake timers;
- fail a test on any request no handler matched, and on any `console.error`.

It does not contain test data or a mount helper.

## Skeleton

```ts
// FleetList.test.ts
import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { createPinia } from "pinia";
import { describe, expect, it } from "vitest";
import FleetList from "./FleetList.vue";
import { fakeDb, fakeEndpoints } from "./FleetList.fakes";
import { makeDrone } from "../testing/factories/drone";
import { worker } from "../testing/worker";

describe("FleetList", () => {
  it("shows a row with the battery and link state for each drone", async () => {
    fakeDb.drones.add(makeDrone({ id: "d1", battery_pct: 3 }));
    worker.use(...fakeEndpoints);

    await render(FleetList, { global: { plugins: [createPinia()] } });

    await expect.element(page.getByTestId("fleet-row-battery-d1")).toHaveTextContent("LOW 3%");
  });
});
```

Adjust imports to the versions installed in `web/package.json`; keep the shape. `render` is async: always `await` it. Import `worker` from `testing/worker.ts`, never from the setup file (a test that imports the setup file runs its hooks twice). `toHaveTextContent("x")` is an exact match; use `toMatchTextContent("x")` for a part of the text.

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

- [ ] `Foo.vue` has `Foo.test.ts` beside it, rendering only `Foo`.
- [ ] Data-backed components have `Foo.fakes.ts` (fake db + fake endpoints) and no import from another component's fakes.
- [ ] No shared mount helper, no global fake db, no module stubbing of the API client.
- [ ] Every asserted element has a stable `data-testid` (`area-thing[-id]`).
- [ ] `pnpm lint && pnpm typecheck && pnpm test && pnpm build` pass in `web/`.
