# Architecture Issue: Task Router Poll Interval Too High For Baseline

Date: 2026-05-13
Test: 09_scaling_baseline.py (internally labeled TEST 08)
Status: OPEN (architecture-level)
Severity: Medium

## Observed Failure
- Test summary: 8 Passed, 1 Failed
- Failed check: task router poll interval <= 0.5s
- Runtime value detected: FALLBACK_POLL_SECONDS=30

## Context
- Test script was updated to avoid crashing on missing POLL_INTERVAL_SECONDS and to read FALLBACK_POLL_SECONDS when present.
- Failure is now a real configuration/architecture mismatch, not a script bug.

## Why This Is Architectural
- Constant is defined in execution infrastructure runtime.
- Meeting target requires changing router behavior/config in backend execution system.

## Current Runtime Behavior
- task_router waits up to FALLBACK_POLL_SECONDS timeout for notify event.
- With value 30, fallback wake-up frequency is too low for baseline threshold.

## Suggested Fix Targets
- backend/app/my_app/services/infra/execution/task_router.py
- optional settings-driven override in config (.env / settings)

## Suggested Remediation
- Introduce configurable poll/fallback interval via settings.
- Set effective fallback poll to <= 0.5s for baseline profile or adjust test contract if 30s is intentional design.

## Acceptance Criteria
- 09_scaling_baseline.py check 8 passes with effective poll interval <= 0.5s.
- Test 09 final summary becomes 9 Passed, 0 Failed.
