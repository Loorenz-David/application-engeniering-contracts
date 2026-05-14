# Bootstrap Full-Build Test Suite

## Purpose

This directory contains the canonical, stable test suite for validating every full backend build.
Each test is self-contained with its own payloads, validation checks, and pass/fail output.

**Rule:** Run these tests in order after every new full build. If a test fails, record the issue at:
```
run_test/bootstrap_test_full_build/issues/
```
following the naming pattern: `YYYY-MM-DD_<short-description>.md`

---

## Prerequisites

### 1. Environment

- Docker services running: `postgres` + `redis`
- Alembic migrations applied
- App running on `http://localhost:8000`
- `APP_ENV=development`
- `.venv` active with all `requirements.txt` installed
- For S3 tests: `.env.s3` with valid AWS credentials present

### 2. Run from App Root

All tests must be run from:
```
run_test/bootstrap_test_full_build/backend/app/
```

```bash
cd run_test/bootstrap_test_full_build/backend/app
source .venv/bin/activate
export APP_ENV=development
```

---

## Test Execution Order

| File | Test | Description |
|------|------|-------------|
| `00_cleanup_and_reset.sh` | 00 | **MANDATORY** — Stop Docker, drop DB, clean state files |
| `01_seed_identity.sh` | 01 | DB injection: user, workspace, role, membership |
| `02_health_auth.sh` | 02 | Health check + sign-in + logout |
| `03_notifications.sh` | 03 | Notification query & mutation endpoints |
| `04_vapid.sh` | 04 | VAPID public key endpoint |
| `05_s3_images_files.sh` | 05 | S3 presigned upload/download for images + files |
| `06_cases_crud.sh` | 06 | Cases full CRUD — 22 endpoint assertions |
| `07_execution_layer.py` | 07 | Execution layer: task router / worker / retry |
| `08_audit_logs.sh` | 08 | Audit log write and query validation |
| `09_scaling_baseline.py` | 09 | DB pool, Redis policy, task router LISTEN/NOTIFY checks |
| `10_cases_cache.sh` | 10 | Query caching + invalidation latency proof |
| `11_sleep_mode.py` | 11 | Sleep mode: ActivityTracker API, HTTP wake-up, idle→sleep simulation |

### Quick Run All

```bash
bash run_all.sh
```

---

## Identity Reference (used across all tests)

| Field | Value |
|-------|-------|
| user client_id | `usr_user_test` |
| username | `user_test` |
| email | `user_test@test.local` |
| password | `Test1234!` |
| workspace client_id | `ws_workspace_test` |
| workspace name | `workspace_test` |
| role client_id | `role_workspace_test_admin` |
| role name | `ADMIN` |
| workspace_role client_id | `wsr_workspace_test_admin` |
| workspace_role name | `admin` |
| membership client_id | `wsm_user_test` |

---

## Issue Reporting

When a test fails:

1. Note the test file and step that failed.
2. Copy the full curl command and raw response.
3. Create a file at `issues/YYYY-MM-DD_<short-description>.md` with:
   - Test reference (e.g. `05_cases_crud.sh / Step G1`)
   - Symptom
   - HTTP status + response body
   - Root cause hypothesis
   - Fix applied (or "pending")
   - Fixed in bootstrap: yes/no + file changed
