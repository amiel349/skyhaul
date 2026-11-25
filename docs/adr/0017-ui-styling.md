# 0017 · UI styling: plain CSS tokens, no UI kit

Status: proposed · 2026-11-25

## Context

Issue #47 gives the dispatcher UI a dark-first ops console look with a light theme. The map canvas also needs the theme colours, and Leaflet cannot read `var()`. The screens are few: a top bar, a list, a panel, two placeholders.

## Decision

- Plain CSS custom properties on `:root` (`web/src/styles/global.css`), plus small hand-written components (`StatusChip`, `BatteryBar`, `ThemeToggle`). No UI kit (Naive UI, PrimeVue).
- Dark values are the default. Light values apply through `prefers-color-scheme: light` and through `data-theme="light"` on the root element. `data-theme="dark"` forces dark, and an unknown value falls back to the system setting. The Pinia theme store follows that attribute; the toggle sets it.
- The theme name (`light` or `dark`) may be saved in `localStorage`. Nothing else is, and never the token (ADR 0015).
- The map reads tokens with `getComputedStyle` and re-styles on a theme change.
- IBM Plex Sans and Mono come from the `@fontsource` packages, so Vite bundles the woff2 files. No font CDN, no request to another host.
- No component hard-codes a colour. State is never shown by colour alone: chips carry text, the LOST chip a dashed border, a low battery the word LOW and a hatched bar.

## Consequences

- No new runtime dependency beyond the fonts; the bundle stays small and the DOM is plain, so `data-testid` and focus handling stay under our control.
- We write the few controls ourselves, including focus rings and keyboard handling (the list rows are real buttons). A bigger console (tables, dialogs, forms) is the point to revisit this.
- Contrast was chosen by hand for both token sets (WCAG AA for text). An automated scan is deferred to #19.

## Alternatives considered

- Naive UI or PrimeVue: ready-made controls and theming, but a large dependency, its own look to override, and theme tokens that the canvas map still cannot read.
