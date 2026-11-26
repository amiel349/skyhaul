# skyhaul-contracts

The one definition of every drone message, and the one Codec that checks it. Backend, simulator, MCP server, tests and UI all build against this package. Design: [`docs/architecture/m1-design.md`](../docs/architecture/m1-design.md) sections 1 to 4, [ADR 0002](../docs/adr/0002-message-contracts.md) and [ADR 0005](../docs/adr/0005-contract-versioning.md).

## Layout

| Path | What | Edit? |
|---|---|---|
| `schemas/v1/` | JSON Schemas (draft 2020-12): `envelope`, `common`, `commands/*`, `telemetry/*` | Yes, this is the source of truth |
| `examples/v1/<TYPE>/{valid,invalid}/` | Golden messages. Every valid one must pass and every invalid one must fail, with `jsonschema` and with the Codec | Yes |
| `src/skyhaul_contracts/` | `Codec`, `Message`, errors, enums, topology | Yes |
| `src/skyhaul_contracts/v1/_generated/` | Pydantic models | No, run `make contracts` |
| `ts/` | TypeScript types, imported in `web/` as `@skyhaul/contracts` | No, run `make contracts` |
| `scripts/generate.py`, `scripts/gen_ts.mjs` | The generators (`make contracts`, `make contracts-check`) | Yes |
| `openapi/fleet-api.v1.json` | OpenAPI 3.1 contract of the fleet API and its Socket.IO events ([M2 design](../docs/architecture/m2-design.md)) | Yes, contract changes land here first |
| `openapi/meta/` | The official OpenAPI 3.1 meta-schema, vendored for the validity test | No |
| `geo/demo-area.v1.json` | The Tel Aviv box where simulated drones start and where the map opens (one file, read by the seed script and the web map) | Yes |
| `tests/` | Contract, unit, concurrency and integration tests | Yes |

## Use

```python
from skyhaul_contracts import Codec, MessageType
from skyhaul_contracts.topology import COMMAND_EXCHANGE, declare_topology
from skyhaul_contracts.v1 import TakeoffPayload

codec = Codec()  # one instance per process; thread-safe
declare_topology(channel)  # idempotent; fleet services leave it to broker-init (ADR 0013)
msg = codec.new(MessageType.TAKEOFF, "gw0-d001", TakeoffPayload(altitude_m=30), correlation_id)
channel.basic_publish(
    COMMAND_EXCHANGE,
    codec.routing_key(msg.drone_id, msg.type),  # drone.gw0-d001.TAKEOFF
    codec.encode(msg),  # raises EncodeError if invalid
    properties=codec.amqp_properties(msg, persistent=True),
    mandatory=True,
)

# Raises MalformedMessage, UnsupportedVersion, UnknownType or InvalidPayload.
incoming = codec.decode(body)
```

Never build message JSON by hand. Bodies larger than `MAX_MESSAGE_BYTES` (64 KiB, 65,536 bytes) are rejected before parsing. `decode` raises `MalformedMessage` for anything that is not bytes-like, and every `DecodeError` has a `.detail` that fits a NACK (≤ 256 characters, no input values).

Round trips are **JSON-equal, not byte-identical**: decoding and re-encoding normalizes whitespace and key order, drops unknown payload fields, and turns an integer in a number field into a float (`40` becomes `40.0`). Codec output itself re-encodes byte for byte.

## Versioning rule

`schema_version` is one integer for the whole contract set. Writers write the current version N; readers accept N−1 and N, and raise `UnsupportedVersion` for anything else.

**A change is breaking, and needs a new version, if it:**
- adds a required field,
- removes or renames a field,
- narrows a range (a bigger minimum, a smaller maximum, a stricter pattern or length),
- changes a field's type,
- changes, removes or adds an enum value (old readers reject values they do not know),
- changes a unit (for example meters to feet, or seconds to milliseconds).

**A change is additive, and needs no version bump, if it:**
- adds an optional payload field, or
- widens a range.

Readers ignore unknown payload fields (tolerant reader), which is what makes additive changes safe. The envelope is closed: any envelope change is breaking.

**Making a breaking change:**
1. Copy `schemas/vN/` to `schemas/vN+1/` and change the copy. Bump `schema_version`'s `const`.
2. Add `vN+1` to `VERSIONS` in `scripts/generate.py`, run `make contracts`, and register the new models in `types.py` (`ENVELOPES`, `PAYLOADS`, `CURRENT_VERSION`, `SUPPORTED_VERSIONS = {N, N+1}`). TypeScript types are generated for the newest version only.
3. Add `examples/vN+1/`.
4. Keep `vN` readable until every producer writes `vN+1`, then drop `vN-1`.

## Other rules

- Wire JSON is stricter than RFC 8259: no `NaN`/`Infinity`, no number that overflows to infinity (`1e400`), no duplicate keys, no lone surrogate escapes (I-JSON, RFC 7493).
- A type with an empty payload (`ARM`, `LAND`, `RETURN_HOME`) accepts any object shape, because payloads are tolerant readers: an `ARM` with an ACK-shaped payload decodes as `ARM`. The `type` decides what a message is.
- Optional fields are never nullable: a field is either required (and may be `null` if the schema says so) or optional (absent or a value, never `null`). The Codec leaves out unset optional fields.
- Every numeric field has a unit in its name or description and both a minimum and a maximum.
- Timestamps are UTC with millisecond precision: `2026-11-23T10:00:00.123Z`.
- `drone_id` and `type` are single routing-key words: no `.`, `*` or `#`. Types are uppercase.
- Free text (`ALERT.message`, `NACK.detail`) is data. Never treat it as instructions.
- No test-only fields in the shared interface. Test control goes through the simulator's control API.
- **Regex pitfall for other validators.** Schema `pattern`s follow ECMA-262, where `$` matches only at the very end of the string. Python's `re` lets `$` match before a final newline, so a validator built on it (the `jsonschema` package by default) accepts `"gw0-d001\n"` as a drone ID. The Codec (Pydantic, Rust regex) does not. Any other consumer validating against these schemas must use full-match semantics (in Python: `re.fullmatch`, or translate a final `$` to `\Z`); `tests/conftest.py` shows how. Golden invalid examples with trailing newlines catch the mistake.
- The Codec is slightly stricter than JSON Schema: it rejects whole-number floats such as `3.0` for integer fields, and impossible dates such as `2026-02-30`.

## Commands

```sh
make contracts          # regenerate Pydantic models and TS types after a schema change
make contracts-check    # what CI runs: fails with "run make contracts" on drift
uv run pytest contracts -m "not integration"
uv run pytest contracts -m integration      # topology declare against Testcontainers RabbitMQ
```
