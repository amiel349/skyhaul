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
- The socket is faked with `web/src/testing/fake-socket.ts`, which tests drive by calling the registered handlers.

## Shared setup: `web/src/testing/setup-browser.ts`

Global hooks only, registered once as the Vitest setup file:
- start the MSW worker before all tests and stop it after;
- after each test: reset the fake db, new Pinia, clear `localStorage`, remove `data-theme`, restore fake timers;
- fail a test on any request no handler matched, and on any `console.error`.

It does not contain test data or a mount helper.

## Skeleton

```ts
// FleetList.test.ts
import { page } from "@vitest/browser/context";
import { render } from "vitest-browser-vue";
import { createPinia } from "pinia";
import { describe, expect, it } from "vitest";
import FleetList from "./FleetList.vue";
import { fakeDb, fakeEndpoints } from "./FleetList.fakes";
import { makeDrone } from "../testing/factories/drone";
import { worker } from "../testing/setup-browser";

describe("FleetList", () => {
  it("shows a row with the battery and link state for each drone", async () => {
    fakeDb.drones.add(makeDrone({ id: "d1", battery_pct: 3 }));
    worker.use(...fakeEndpoints);

    render(FleetList, { global: { plugins: [createPinia()] } });

    await expect.element(page.getByTestId("fleet-row-d1")).toHaveTextContent("LOW");
  });
});
```

Adjust imports to the versions installed in `web/package.json`; keep the shape.

## Before you finish a component change

- [ ] `Foo.vue` has `Foo.test.ts` beside it, rendering only `Foo`.
- [ ] Data-backed components have `Foo.fakes.ts` (fake db + fake endpoints) and no import from another component's fakes.
- [ ] No shared mount helper, no global fake db, no module stubbing of the API client.
- [ ] Every asserted element has a stable `data-testid` (`area-thing[-id]`).
- [ ] `pnpm lint && pnpm typecheck && pnpm test && pnpm build` pass in `web/`.
