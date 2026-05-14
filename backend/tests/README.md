# Backend Tests

This directory holds the canonical test suite shipped with every bootstrapped app.

## Structure

```
tests/
  bootstrap_tests/   Full integration test suite for a freshly bootstrapped app.
  test_summary/      Per-run test notes and results (created on bootstrap, written at runtime).
```

## bootstrap_tests/

11 sequential test suites that validate the entire app stack end-to-end.

| Script | Suite | What it covers |
|--------|-------|----------------|
| `01_seed_identity.sh` | 01 | Admin user + workspace seed (required — aborts on failure) |
| `02_health_auth.sh` | 02 | Health endpoint, sign-in, logout, token revocation |
| `03_notifications.sh` | 03 | Notification read/unread, delivery endpoints |
| `04_vapid.sh` | 04 | VAPID public key endpoint |
| `05_s3_images_files.sh` | 05 | Presigned upload/download for images and files |
| `06_cases_crud.sh` | 06 | Cases CRUD, links, reorder |
| `07_execution_layer.py` | 07 | Task router, worker, schedulers, LISTEN/NOTIFY |
| `08_audit_logs.sh` | 08 | Audit log creation and query |
| `09_scaling_baseline.py` | 09 | DB pool, Redis eviction policy, task router LISTEN/NOTIFY |
| `10_cases_cache.sh` | 10 | Query caching and cache invalidation latency |
| `11_sleep_mode.py` | 11 | Sleep mode: ActivityTracker, HTTP wake-up, idle simulation |

## Running the tests

From `<project>/backend/tests/bootstrap_tests/`:

```bash
bash run_all.sh
```

`run_all.sh` starts the API server and background workers automatically if they
are not already running. It cleans up everything it started on exit.

## test_summary/

Date-stamped notes written during a test run. Not committed after the initial
directory creation — contents are per-project and per-run.
