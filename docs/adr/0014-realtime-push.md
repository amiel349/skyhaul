# 0014 · Realtime push

Status: accepted · 2026-11-24

## Context

Dispatchers watch the fleet live: drone state, command replies, mission changes and alerts. The ingestor produces these changes but serves no HTTP. The API is Flask in one gunicorn process with threads (ADR 0010). Clients must be authenticated, and command events must reach only roles that may send commands.

## Decision

- **Flask-SocketIO in threading mode** inside the API process, namespace `/fleet`. No eventlet or gevent monkey-patching, so pika, SQLAlchemy and Redis clients behave as everywhere else.
- **The ingestor publishes events to Redis**, channel `events`, as `{"event", "data"}` with `data` in the shape of the OpenAPI component schemas (`DroneStateEvent`, `CommandUpdatedEvent`, `MissionUpdatedEvent`, `AlertRaisedEvent`, `AlertUpdatedEvent`). `drone.state` is coalesced to at most once per drone per flush.
- **One fan-out thread per API process** subscribes to `events` and emits to rooms. The room comes from the event name, never from the payload: `command.updated` goes to `role:dispatcher` and `role:admin`; everything else goes to `fleet`.
- **Server-assigned rooms.** The client connects with `auth: {token}` (never the query string, which gets logged). The server verifies the JWT and the active user and joins the rooms itself. There is no client `join` event, and no client events at all in M2.
- **Expiry sweep:** every 30 s the server disconnects sessions whose token has expired.

## Consequences

- One process means in-memory Socket.IO state is enough. More API processes (M4) need `message_queue=redis://` and sticky sessions.
- Redis pub/sub is fire-and-forget: a client that disconnects misses events and must refetch over REST on reconnect. The UI treats REST as the source of truth and events as hints.
- A role change takes effect on the socket at the next connect (or the next expiry), not instantly.

## Alternatives considered

- Server-sent events: simpler, but one-way only, and Socket.IO's reconnect and rooms are useful later.
- Polling: no push infrastructure, but slow and heavy for a live map.
- eventlet or gevent mode: more connections per process, but monkey-patching breaks the threading guarantees the rest of the stack relies on.
- Client-chosen rooms: flexible, but every join becomes an authorization check that can be forgotten.
