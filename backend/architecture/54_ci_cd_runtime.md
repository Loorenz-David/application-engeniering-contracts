# 54 - CI/CD Runtime Contract

## Purpose

Standardize CI/runtime validation flow for deterministic infrastructure startup, migration safety, and deployment-parity checks.

This contract extends:
- 33_deployment.md
- 31_health_observability.md
- 30_migrations.md
- 49_observability_runtime.md

---

## CI Runtime Principles

CI execution must be:
- deterministic
- reproducible
- explicit
- fail-loud

No hidden environment assumptions are allowed.

---

## Required Validation Flow

Minimum CI validation order:
1. dependency install
2. lint and formatting gates
3. infrastructure startup validation
4. migration validation
5. app startup validation
6. health/readiness checks
7. test execution

Any failed gate fails pipeline immediately.

---

## Docker and Compose Validation

Compose validation should confirm:
- services start with explicit healthchecks
- dependencies reach healthy state
- startup ordering is deterministic
- runtime can start in isolated validation mode

Dynamic validation ports are required to avoid host collisions and improve reproducibility.

---

## Migration Safety Checks

CI must validate migrations by:
- running upgrade head on clean database
- failing on migration errors
- verifying app startup after migration

When feasible, include downgrade safety checks for early-stage projects.

---

## Health and Readiness Requirements

Validation must include explicit calls to:
- health endpoint
- readiness endpoint (when implemented)

Expected behavior:
- DB/Redis dependency status visible
- degraded states fail validation gates

---

## Runtime Parity Expectations

Validation runtime should match production assumptions for:
- dependency boundaries
- startup ordering
- environment validation behavior
- worker/runtime wiring

This contract does not require Kubernetes or multi-service orchestration.

---

## GitHub Actions Expectations

Workflows should include jobs for:
- lint
- format
- tests
- docker validation
- migration checks
- health checks

Jobs should be explicit and independently diagnosable.

---

## Rollback and Recovery Expectations

CI should validate rollback readiness by ensuring:
- migration history is coherent
- deploy-time failure signals are observable
- recovery paths are documented

---

## Anti-Patterns

- CI pipelines that continue after failed readiness checks
- implicit environment dependency on pre-running host services
- migration checks skipped in main branch builds
- health checks treated as informational only

---

## Recommended Read Order

1. 33_deployment.md
2. 30_migrations.md
3. 31_health_observability.md
4. 54_ci_cd_runtime.md
5. 49_observability_runtime.md
