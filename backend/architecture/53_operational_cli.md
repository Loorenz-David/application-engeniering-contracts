# 53 - Operational CLI Contract

## Purpose

Standardize operational command tooling for reproducible runtime operations and AI-agent operability.

This contract extends:
- 27_cli_scripts.md
- 52_replayability.md
- 51_worker_runtime.md
- 49_observability_runtime.md

---

## CLI Foundation

Use Typer for operational command interfaces.

Command groups should be explicit and predictable:
- replay
- inspect
- backfill
- cleanup
- seed
- repair
- queue
- worker
- diagnostics

---

## Command Structure Rules

Each command should define:
- input contract
- dry-run behavior
- safety guards
- structured logging events
- success/failure exit behavior

No hidden runtime auto-discovery or implicit plugin loading.

---

## Destructive Operation Safeguards

Destructive commands must require explicit confirmation and should support dry-run.

Required patterns:
- --dry-run defaults to true where destructive risk exists
- --confirm flag required to execute irreversible operations
- clear warning output and structured warning log before execution

---

## Operational Reproducibility

Operational commands must:
- be deterministic for same input set
- avoid reliance on interactive hidden prompts
- support non-interactive CI/operator execution
- emit machine-readable logs

---

## Makefile Integration Guidance

Expose stable make targets that wrap operational CLI commands:
- make seed
- make replay
- make inspect
- make reset-db
- make logs
- make worker
- make validate

Make targets should remain thin wrappers around explicit scripts.

---

## Naming Conventions

Command naming:
- verb-noun or noun-verb with consistent project convention
- avoid ambiguous names like run or exec

Examples:
- replay events
- inspect queues
- backfill users
- cleanup orphans
- worker start

---

## Operational Diagnostics Philosophy

Operational CLI is part of runtime governance, not developer convenience only.

Required diagnostics behavior:
- every command emits start and end events
- failures include context IDs and root cause summary
- diagnostics commands are read-only by default

---

## Anti-Patterns

- command behavior that differs between local and CI for same flags
- destructive commands without explicit confirm gate
- operational scripts with print-only unstructured output
- one-off shell scripts without contract ownership

---

## Recommended Read Order

1. 27_cli_scripts.md
2. 53_operational_cli.md
3. 52_replayability.md
4. 51_worker_runtime.md
5. 49_observability_runtime.md
