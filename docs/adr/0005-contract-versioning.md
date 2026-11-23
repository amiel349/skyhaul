# 0005 · Contract versioning and code generation

Status: accepted · 2026-11-23

## Context

ADR 0002 puts the message schemas in `contracts/` with a `schema_version` in the envelope, but leaves open what the version covers, what a breaking change is, how strict readers are, and how the generated code is kept in step with the schemas. Backend, simulator, MCP server and UI all build against these shapes, often in parallel branches, so the rules have to be mechanical.

## Decision

- **One integer `schema_version` for the whole contract set**, not per message type. v1 is `const: 1`.
- **Writers write N; readers read N−1 and N** (`CURRENT_VERSION`, `SUPPORTED_VERSIONS`). Any other integer is `UnsupportedVersion`, answered with NACK `UNSUPPORTED_VERSION` when the envelope can be read.
- **Breaking** (bump N, keep the old schema directory and models readable for one version): a new required field, a removed or renamed field, a narrowed range, a changed type, a changed or **added** enum value, a change in units.
- **Additive** (no bump): a new optional payload field, a widened range.
- **Closed envelope, tolerant payloads.** The envelope has `additionalProperties: false`. Payload schemas leave it open, and the models ignore unknown fields (`extra="ignore"`).
- **Optional fields are never nullable.** An optional field is absent or has a value; the Codec omits unset optional fields. Nullable fields are always required.
- **Generated code is committed.** datamodel-code-generator (Pydantic v2) and json-schema-to-typescript, both pinned exactly. `make contracts` regenerates; `make check` and the CI `contracts` job regenerate in a scratch directory with a pinned formatter config and fail on any byte difference with "run make contracts".
- The Codec validates the envelope in Pydantic strict mode and the already-parsed payload with generated `Strict*` scalar types, and re-decodes its own output on encode, so the generated models are the runtime authority and the JSON Schemas are the source they are generated from. The golden examples prove the two agree.

## Consequences

- Adding an optional payload field is a one-file schema change plus regeneration; old readers keep working.
- An added enum value is breaking because old readers have a closed enum and would reject it. This is deliberate: an unknown `NackReason` or `AlertCode` should not be silently accepted.
- Reviewers see generated diffs in PRs, and generator upgrades show up as regenerated code.
- The envelope can never grow without a version bump.
- The Codec is slightly stricter than JSON Schema in two corners (whole-number floats for integers, impossible calendar dates). Producers never emit either.

## Alternatives considered

- Per-type versions: finer-grained, but every consumer would need a version matrix, and the N−1/N rule would no longer be one number.
- Strict closed payloads: every addition would bump the version and force lockstep deploys of backend and simulator.
- Generating at build time instead of committing: no diffs to review, but every environment (Docker builds, CI, editors) would need both generators and Node.
- Semantic version strings: more expressive than needed. Only "can I read this?" matters to a reader, and an integer answers it.
