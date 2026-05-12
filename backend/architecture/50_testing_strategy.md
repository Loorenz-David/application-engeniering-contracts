# 50 - Testing Strategy Contract

## Purpose

Define deterministic test architecture and boundaries for modular-monolith backends.

This contract extends:
- 15_testing.md
- 30_migrations.md
- 16_background_jobs.md
- 52_replayability.md

---

## Testing Pyramid and Ownership

Required layers:
- Unit tests: pure domain logic and deterministic helper functions.
- Integration tests: command/query behavior with DB and Redis boundaries.
- End-to-end tests: API runtime plus infrastructure dependencies.

Coverage intent:
- Domain tests prove invariants and state transitions.
- Command tests prove write orchestration and event triggering.
- Query tests prove read-only behavior and serialization shape.
- Worker tests prove retry/idempotency behavior.
- Replay tests prove deterministic re-execution safety.
- API contract tests prove response shape stability.

---

## Required Test Structure

tests/
- unit/
- integration/
- e2e/
- fixtures/
- factories/
- helpers/

Rules:
- Keep tests grouped by behavior, not by framework feature.
- Factories create valid defaults with explicit overrides.
- Fixtures are deterministic, isolated, and reusable.

---

## Determinism Requirements

1. No dependence on local machine state.
2. No dependence on execution order.
3. Fixed seeds for randomized test data.
4. Frozen time where time-sensitive logic is tested.
5. Explicit setup and teardown for DB and Redis state.

---

## Async Testing Rules

- Async tests use pytest-asyncio with explicit async fixtures.
- No hidden event loop reuse across test modules.
- Await all async boundaries; no fire-and-forget in tests.

---

## DB and Redis Isolation Rules

DB isolation:
- Integration tests run with transactional rollback or disposable schema.
- Migration tests run against a clean database and validate upgrade path.

Redis isolation:
- Use dedicated test key prefix per test run.
- Cleanup keys after each test or fixture scope.
- Never share production/development Redis prefixes in tests.

---

## Naming Conventions

- test_<behavior>.py for files.
- test_<expected_behavior>() for function names.
- Fixture names describe contract, not implementation detail.

Examples:
- test_rejects_invalid_transition
- test_create_order_emits_event
- test_list_orders_filters_workspace
- test_worker_retry_stops_at_limit

---

## Fixture Patterns

Good fixture pattern:
- create_user_factory fixture returns callable factory.
- db_session fixture guarantees rollback.
- redis_client fixture enforces isolated prefix cleanup.

Avoid:
- giant global fixture with hidden side effects
- shared mutable fixtures across unrelated modules
- fixtures that perform network calls by default

---

## Anti-Patterns

- Tests that only assert status code without behavior checks.
- Unit tests that hit DB/Redis.
- Integration tests relying on previous test output.
- Snapshot tests for unstable fields (timestamps, IDs) without normalization.
- Tests that pass locally but require manual service startup assumptions.

---

## CI Reproducibility Requirements

CI must run deterministic test command sets:
- unit
- integration
- e2e (when configured)

Each pipeline must:
- initialize required infra explicitly
- validate readiness before running tests
- fail loudly on flaky infra state

---

## Recommended Read Order

1. 15_testing.md
2. 50_testing_strategy.md
3. 30_migrations.md
4. 51_worker_runtime.md
5. 52_replayability.md
