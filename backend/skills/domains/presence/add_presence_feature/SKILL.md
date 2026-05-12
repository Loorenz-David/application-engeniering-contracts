# Presence Add Feature

## Intent

Implement presence tracking or realtime presence updates for a feature.

## Trigger conditions

- User asks for online status, activity heartbeat, or live presence views.

## Required inputs

- Presence semantics (online/away/offline/custom)
- TTL/heartbeat timing
- Realtime channel scope

## Contracts to load

- `backend/architecture/48_presence.md`: presence baseline
- `backend/architecture/13_sockets.md`: realtime transport
- `backend/architecture/12_infra_redis.md`: shared state/pubsub
- `backend/architecture/49_observability_runtime.md`: metrics and diagnostics

## Optional local extension companions

- `backend/architecture/48_presence_local.md`

## Execution protocol

1. Define presence state transitions and TTL behavior.
2. Implement heartbeat/update transport and persistence boundary.
3. Wire pubsub/socket fanout and connection management.
4. Add tests for expiration and reconnect behavior.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Presence behavior is predictable under reconnect/latency scenarios.
- Runtime diagnostics exist for stale/expired sessions.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
