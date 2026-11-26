# Frontend · Vue 3 and TypeScript

Applies to `web/**`.

## Components
- `<script setup lang="ts">` with typed `defineProps` and `defineEmits`. No Options API in new code.
- Components stay small: one job each. Data fetching and business rules live in Pinia stores or composables, not in templates.
- Props are never mutated. Derived values are `computed`, not watchers that copy state.
- Watchers and listeners started in a component are cleaned up on unmount (WebSocket handlers, intervals, map listeners).
- Lists use a stable `:key` from data (the drone ID), never the array index.

## State (Pinia)
- One store per domain (fleet, missions, alerts, session). Stores expose actions; components don't reach into another store's internals.
- Live updates from the WebSocket go through store actions, so there is one path for state changes.
- Optimistic updates keep the previous value and roll back on NACK or error, and the user sees a message saying why.
- No duplicated server state in several stores.

## API access
- Calls go through the client generated from the OpenAPI spec. Hand-written `fetch` for an endpoint that exists in the spec is should-fix.
- Every request handles loading, success, empty and error states in the UI.
- Requests that can be superseded (search, map bounds) are cancelled or ignored when stale.

## TypeScript
- Strict mode passes. No `any`, no non-null `!` assertions, no `as` casts without a comment explaining why the type is guaranteed.
- Types for API data come from the generated types, not redeclared by hand.

## Security
- Never `v-html` with user, drone or model text. Copilot markdown is rendered through DOMPurify. `v-html` on untrusted text is blocking.
- The access token lives in memory only. Writing tokens to localStorage or sessionStorage is blocking.
- No secrets in frontend code or `VITE_*` variables; everything shipped to the browser is public.
- Role checks in the UI are for convenience only. The server enforces them; check that the matching endpoint does.

## Testability and accessibility
- Every element a test clicks or asserts has a stable `data-testid` in kebab-case (`drone-panel-battery`). Changing or removing one needs the matching E2E update in the same PR.
- Inputs have labels, buttons have accessible names, focus is visible, and controls work with the keyboard.
- Status is not shown by color alone; it also has text or an icon (battery low, link lost).

## Tests (Vitest browser mode, MSW)
Full rules: the `web-component-tests` skill.
- Test behavior the user sees: render, interact, assert on roles, labels and text. No assertions on internal component state (`wrapper.vm`, refs, methods). A spy on a third-party library (Leaflet) is the only exception.
- Every new or changed `.vue` file has a sibling test file with the same name that tests that component with its real children (`PanelSplitter.vue` and `PanelSplitter.test.ts`). A component without one is should-fix.
- Queries follow the priority order: role or label, then placeholder, then text, then `getByTestId` as a last resort. Queries by CSS class or tag, or a `getByTestId` where a role or label exists, are should-fix.
- Interactions use `userEvent` from `vitest/browser`. Timers use fake timers, never real sleeps.
- Data comes from faketories, the fake db and fake endpoints in `web/tests/`, typed from the generated API types. A raw mock payload in a test, a mocked `fetch` or API client, or a stub of a child component (outside a heavy third-party part, with a comment) is should-fix.
- Components are rendered through the thin `renderComponent` helper. A helper that seeds data or stubs children is should-fix.
- Stores are tested for their actions, including rollback paths.
