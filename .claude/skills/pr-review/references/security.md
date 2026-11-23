# Security (always applied)

Skyhaul is a public repository: anything pushed is public at once. See `docs/security/threat-model.md`. If a change adds a new surface, the threat model is updated in the same PR.

## Secrets
- No keys, tokens, passwords, connection strings or private keys in code, tests, fixtures, docs, examples, logs, screenshots or recorded traces. Blocking, and the secret must be rotated, not just deleted.
- `.env.example` keeps empty values. Test credentials are generated per run, never hard-coded "test123" values that look real.
- Secrets reach code only through environment variables, with no defaults.

## Authentication and access control
- Every new endpoint, socket event and tool checks authentication and the user's role on the server.
- Every read and write of tenant data is scoped to the caller's tenant. A missing tenant filter is blocking (IDOR).
- Resources from another tenant return 404, not 403, so their existence isn't revealed.
- Passwords are hashed with argon2id. Login is rate-limited. Tokens are short-lived and checked for signature, expiry and audience.

## Input and output
- All external input is validated: HTTP bodies, query params, headers used in logic, RabbitMQ messages, MCP tool arguments, model output.
- No SQL, shell commands, file paths or URLs built from untrusted input.
- No `v-html` with untrusted text. No `eval`, `pickle`, or `yaml.load` without `SafeLoader` on untrusted data.
- Error messages don't leak internals: stack traces, SQL, file paths, versions.

## Test-only surfaces
- The simulator control API, reset endpoints and the fake LLM run only in the test profile, behind a token, bound to localhost or the internal network. Any path that exposes them in production config is blocking.
- The fleet API has no reset or debug endpoints.

## Logging and evidence
- Logs never contain passwords, tokens, API keys, cookies or full request headers.
- Test evidence is scrubbed before upload or publishing.

## Privacy and provenance
- No personal data of the author (phone, personal email) in code, docs or fixtures.
- Nothing from the author's past or current employers: no code, names, documents or domain-specific message names.

## Supply chain
- New dependencies are justified, maintained and locked. Watch for typosquatted package names.
- CI changes follow `infra-ci.md`: SHA-pinned actions, minimal permissions, no `pull_request_target` on PR code, no script injection.
