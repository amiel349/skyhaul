## What and why

<!-- Link the issue: Closes #123 -->

## How it was tested

- [ ] Unit / component tests
- [ ] Integration or contract tests
- [ ] E2E (API or UI)
- [ ] Manual check (describe)

## Acceptance criteria

<!-- Copy from the issue and tick each one with its evidence. -->

## Security checklist

- [ ] No secrets, tokens or passwords in code, tests, fixtures, logs or screenshots
- [ ] New endpoints and socket events check permissions and tenant on the server
- [ ] User and model input is validated, and never rendered with `v-html`
- [ ] Test-only code (control API, reset, fake LLM) is not reachable in production
- [ ] New dependencies are needed, maintained and pinned in the lockfile
