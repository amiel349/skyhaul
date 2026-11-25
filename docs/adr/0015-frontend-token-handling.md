# 0015 · Front-end token handling

Status: proposed · 2026-11-25

## Context

The dispatcher UI needs the access token for REST calls and the `/fleet` socket. The API issues a 15-minute HS256 access token and, today, no refresh token or cookie (ADR 0011 planned one for M3; it is not built). Anything script-readable is exposed to an XSS bug.

## Decision

- Keep the access token in memory only, in the Pinia auth store. Never in `localStorage`, `sessionStorage` or a script-readable cookie.
- Send it through one API client middleware and the socket `auth` callback, never in a URL.
- On a 401, on a socket refusal or disconnect with `token_expired` or `unauthorized`, or 30 s before `expires_in` runs out: clear the store, close the socket, go to login.
- Session continuity (reload, new tab, shifts over 15 minutes) is the job of a future server-side refresh flow with an httpOnly cookie, not of browser storage.

## Consequences

- Until a refresh endpoint exists, every reload, every new tab and every 15 minutes means logging in again. Acceptable for a demo; a backend issue tracks the refresh flow.
- An XSS bug can still act while the page is open, but cannot steal a stored token for later. CSP and the `v-html` ban reduce that further.

## Alternatives considered

- `localStorage`: survives reload, but any injected script can read and exfiltrate the token.
- `sessionStorage`: the same exposure, per tab.
- Building the refresh cookie as part of the UI work: it is a server change with a CSRF design, owned by backend.
