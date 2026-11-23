# AI features

Applies to `services/copilot/**`, `services/mcp_sim/**`, `tools/fake_llm/**`, `tools/triage/**` and `tests/evals/**`.

## Model calls
- All calls go through one provider interface, so the fake LLM can replace Claude in tests. A direct SDK call that bypasses it is should-fix.
- Model IDs and limits come from config, not hard-coded in several places.
- Every call has a timeout, a `max_tokens` bound, and bounded retries for rate-limit and overload errors only.
- Token usage and cost are logged per request, without logging the prompt's sensitive content.
- Streaming responses handle disconnects and partial output.

## Tools (copilot and MCP)
- Tool input schemas are strict: types, ranges, enums, required fields. Tool arguments are validated again on the server before anything happens.
- Copilot tools call the fleet API with the requesting user's own token. A tool that uses a service account or skips permission checks is blocking.
- Tools that move drones or change state return a proposal. The action runs only after the user confirms in the UI. Skipping confirmation is blocking.
- Tool errors return a clear error result to the model; they never crash the service or leak stack traces.
- The MCP server reads its token from the environment. A token in `.mcp.json` or any committed file is blocking.

## Prompt injection
- Drone names, alert text, telemetry fields, chat history and tool results are data. They are passed in clearly delimited tool results or data blocks, never concatenated into the system prompt.
- The system prompt states that instructions inside data must be ignored.
- The design must stay safe even if injection succeeds: the model can only do what the user's token and the confirmation step allow.
- New tools or data sources come with injection cases in the evals.

## Output handling
- Model output is untrusted. Rendered markdown is sanitized (DOMPurify); model output is never executed, used as SQL, a shell command, or a URL to fetch without validation.
- Structured output is parsed and validated against a schema. Invalid output is handled, not trusted.

## Triage agent
- Evidence (logs, traces, test output) is redacted before it reaches the model: tokens, cookies, auth headers, passwords.
- The agent has no tools. Fixed code posts the result with a token limited to `pull-requests: write`.
- Its labels (product bug, flaky, environment, test bug) come with evidence lines, not only a verdict.

## Evals and determinism
- E2E tests use the fake LLM with scripted responses, so they are deterministic and need no API key.
- Evals check tool choice and arguments against golden cases, report a score, and run nightly against the real model.
- A prompt or tool change that lowers the eval score needs an explanation in the PR.
- Eval cases never contain real personal data.
