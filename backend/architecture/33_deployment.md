# 33 — Deployment & Release Contract

## The core rule

**Code and schema must never be out of sync in a way that breaks running instances.** During a rolling deploy, old and new versions of the application run simultaneously for a short window. Both versions must be able to operate against the same database schema.

---

## Migration-to-code ordering

The order in which you deploy migrations and code determines whether the rolling window is safe.

### Additive changes (most common)

For changes that add new columns, tables, or indexes:

```
1. Deploy the migration first   (adds the new column — nullable, no default required)
2. Deploy the new code           (code now reads and writes the new column)
```

The old code running during step 1 simply ignores the new column — it doesn't know it exists. Safe.

### Removal changes

For changes that remove columns or tables:

```
1. Deploy the code change first  (remove all references to the column from the application code)
2. Deploy the migration          (drop the column once no code references it)
```

If you drop the column before deploying the code, the old code will crash when it tries to read the now-missing column. Unsafe.

### State-changing column additions (`NOT NULL`)

Follow the three-migration sequence from [30_migrations.md](30_migrations.md). Never combine in one step.

---

## Pre-deployment checklist

Run this before every production deployment:

- [ ] `alembic current` matches the expected migration head on staging
- [ ] Migration has been applied to staging without errors
- [ ] All new environment variables are set in the target environment's secret manager
- [ ] `GET /health` returns `200` on the currently running production version
- [ ] Any new background queues are registered and workers are configured to consume them
- [ ] Rollback plan is defined (see rollback section below)
- [ ] If the deploy includes a `DROP COLUMN` or `DROP TABLE`: explicit team confirmation received

---

## Environment variable promotion

New environment variables must exist in every environment before the code that reads them is deployed. The order:

```
1. Add the variable to staging secret manager
2. Deploy to staging — verify the app boots and reads the variable
3. Add the variable to production secret manager
4. Deploy to production
```

If `os.environ.get("MY_NEW_KEY")` is used with a safe default, step 1 can follow step 2. If the variable is required at startup (raises at boot if missing), steps 1 and 3 must precede the respective deploys.

Application startup must validate required variables and fail fast — a crash at boot is better than a silent failure mid-request:

```python
# config/production.py
import os

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "REDIS_URI",
    "JWT_SECRET_KEY",
    "EMAIL_API_KEY",
]

def validate_env():
    missing = [key for key in REQUIRED_ENV_VARS if not os.environ.get(key)]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")
```

Call `validate_env()` inside `create_app()` for production configs.

---

## Zero-downtime deploy sequence

For a standard rolling deploy (no breaking schema changes):

```
1. Run: alembic upgrade head         (apply pending migrations — additive only)
2. Wait: confirm /health returns 200 on current instances
3. Deploy: roll out new app version instance by instance
4. Monitor: watch /health, error rate, p95 latency for 10 minutes
5. Confirm: all instances are on the new version
```

For deploys with a column/table removal:

```
1. Deploy code without the removal   (all references to the column removed)
2. Monitor for 24 hours              (confirm old code paths are gone)
3. Run: alembic upgrade head         (drop the column)
4. Monitor
```

---

## Rollback procedure

Define the rollback plan before deploying, not after something breaks.

### Application rollback (no schema change)

```bash
# Re-deploy the previous image/revision
# Most platforms: redeploy the previous release tag
# Confirm: GET /health returns 200
# Confirm: error rate returns to baseline
```

### Application rollback (with additive migration already applied)

The migration added a nullable column. The old code doesn't know about it. The old code still works — the column is nullable, so the DB won't reject old writes that don't include it.

```
1. Re-deploy the previous application version
2. Leave the migration in place — do NOT run alembic downgrade -1
3. The new column is unused but harmless
4. Fix the issue in the new version and redeploy
```

**Never run `alembic downgrade -1` in production unless:**
- The migration is provably safe to reverse (no data loss)
- You have explicit confirmation from the team
- You have verified the downgrade() function is correct

### Application rollback (with destructive migration)

A destructive migration (`DROP COLUMN`, `DROP TABLE`) that has already been applied **cannot be automatically reversed**. The data is gone. This is why the pre-deployment checklist requires explicit team confirmation for destructive operations.

If this happens:
1. Restore from the most recent database snapshot
2. Re-deploy the previous application version against the restored snapshot
3. Accept data loss for operations that occurred after the snapshot was taken
4. Conduct a post-mortem

---

## Feature flags for risky deploys

When a new feature introduces significant risk (new payment integration, structural behavior change), deploy it behind a config-driven flag rather than a code gate:

```python
# config/default.py
FEATURE_NEW_CHECKOUT_FLOW = os.environ.get("FEATURE_NEW_CHECKOUT_FLOW", "false") == "true"
```

```python
# services/commands/<domain>/create_record.py
from my_app.config import settings

if settings.feature_new_checkout_flow:
    result = new_checkout_flow(ctx)
else:
    result = legacy_checkout_flow(ctx)
```

Flags are turned on by setting the env var in the target environment — no code deploy required.

**Rules for feature flags:**
- Flags are boolean only. No multi-value flags.
- Flags default to `False` (off). The new behavior is opt-in.
- Flags are temporary. Once the feature is stable and the old path is removed, the flag is removed in the same PR.
- Do not accumulate flags. A flag that has been on for 30+ days without cleanup is tech debt.

---

## Smoke test after deploy

After every production deploy, run these checks before declaring success:

```bash
# 1. Health
curl -s https://api.myapp.com/health | jq .

# 2. Auth round-trip
curl -s -X POST https://api.myapp.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<smoke_test_user>", "password": "<smoke_test_password>"}' | jq .

# 3. Core domain read
# (Use a smoke-test workspace that exists in production)
curl -s https://api.myapp.com/api/v1/records/ \
  -H "Authorization: Bearer <smoke_test_token>" | jq .

# 4. Application version
curl -s https://api.myapp.com/info | jq .version
```

The smoke test user is a dedicated non-human account in production. It must not appear in workspace member lists or business reports — filter it by a flag on the user record or by a reserved email domain.

---

## Deployment decisions at a glance

| Scenario | Action |
|---|---|
| Adding a nullable column | Migrate first, then deploy code |
| Adding a NOT NULL column | Three-migration sequence (see 30) |
| Removing a column | Deploy code first (remove references), then migrate |
| Adding a new env var (required at boot) | Set in secret manager before deploying |
| Adding a new env var (optional, has default) | Can be set after deployment |
| Deploying a risky feature | Use a feature flag; deploy disabled |
| Something is wrong after deploy | Rollback app first; investigate before touching DB |
| Destructive migration already ran | Restore from snapshot; no auto-rollback |
