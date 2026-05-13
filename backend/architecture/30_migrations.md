# 30 — Database Migrations Contract

## The rule that overrides everything else

**Never touch the database manually.** No `ALTER TABLE` in psql. No column drops via a DB GUI. No `db.create_all()` in production. Every schema change goes through an Alembic migration that is committed to the repository, reviewed, and applied with `alembic upgrade head`. No exceptions.

---

## When to write a migration

Write a migration whenever you:
- Add, rename, or remove a column
- Add, rename, or remove a table
- Add or remove an index or constraint
- Add, modify, or remove a Postgres enum type
- Change a column's type, nullability, or default

Do **not** write a migration for:
- Data changes (use CLI backfill commands — see [27_cli_scripts.md](27_cli_scripts.md))
- Seeding reference data in development (use `python scripts/seed_dev.py`)
- Seeding required production reference data (write a separate idempotent migration with `op.execute`)

---

## Generating a migration

Always auto-generate from the model change, then review the generated file before applying:

```bash
# 1. Make the change in the SQLAlchemy model
# 2. Generate
alembic revision --autogenerate -m "add_status_to_records"

# 3. Review the generated file in migrations/versions/
# 4. Apply
alembic upgrade head
```

**Never write migration SQL by hand.** Auto-generation catches type mappings and Postgres dialect details you will get wrong manually. Fix the generated file if needed — do not replace it.

---

## Migration file naming

Alembic auto-generates a revision ID prefix. Always provide a descriptive message:

```bash
# Good — describes the change
alembic revision --autogenerate -m "add_status_to_records"
alembic revision --autogenerate -m "add_workspace_id_to_categories"
alembic revision --autogenerate -m "create_record_attachments_table"
alembic revision --autogenerate -m "drop_legacy_active_flag_from_users"

# Bad — no context
alembic revision --autogenerate -m "update"
alembic revision --autogenerate -m "fix"
alembic revision --autogenerate -m "migration_1"
```

The resulting filename: `migrations/versions/20250115_142301_add_status_to_records.py`

---

## Reviewing the generated migration

Before running `alembic upgrade head`, open the generated file and verify every item in this checklist:

- [ ] **Correct table** — The migration targets the right table. Auto-detection can be confused by import order.
- [ ] **Correct column type** — `String(64)` vs `Text`, `Integer` vs `BigInteger`, `DateTime(timezone=True)` vs `DateTime`.
- [ ] **Nullable set correctly** — New columns on populated tables must be `nullable=True` unless you provide a server default. See zero-downtime patterns below.
- [ ] **Index present** — If the column appears in `WHERE` clauses, a `create_index` line must be present.
- [ ] **Enum type handled** — If you added an `SAEnum` column, `create_type=True` should generate a `CREATE TYPE` statement. Verify it's there.
- [ ] **Foreign key correct** — `ForeignKey("workspaces.client_id")` not `ForeignKey("workspace.client_id")` (wrong table name).
- [ ] **Downgrade is safe** — The `downgrade()` function must correctly reverse the upgrade. Auto-generation usually handles this, but verify for complex changes.
- [ ] **No unintended changes** — Sometimes Alembic detects unrelated schema drift. If the migration includes tables you did not change, investigate before applying.

---

## Zero-downtime patterns

Production tables have live traffic. A migration that locks a table for seconds can cause timeouts. Follow these patterns to avoid it.

### Adding a nullable column (safe)

```python
def upgrade():
    op.add_column("records", sa.Column("status", sa.String(32), nullable=True))
```

Adding a nullable column with no default is instantaneous in Postgres — no table rewrite, no lock.

### Adding a NOT NULL column (requires three migrations)

Never do this in one step on a populated table:

```python
# WRONG — locks the table, fails if any existing row has no value
op.add_column("records", sa.Column("status", sa.String(32), nullable=False))
```

The correct three-migration sequence:

**Migration 1 — add nullable:**
```python
def upgrade():
    op.add_column("records", sa.Column("status", sa.String(32), nullable=True))
```

**Run backfill CLI command between migrations:**
```bash
python scripts/backfill_record_status.py --dry-run
python scripts/backfill_record_status.py
```

**Migration 2 — apply NOT NULL after all rows have a value:**
```python
def upgrade():
    op.alter_column("records", "status", nullable=False)
```

**Migration 3 (optional) — add server default for future inserts:**
```python
def upgrade():
    op.alter_column("records", "status", server_default="draft")
```

### Adding an index (safe, non-blocking)

Use `postgresql_concurrently=True` for large tables to avoid a write lock:

```python
def upgrade():
    op.create_index(
        "ix_records_workspace_id_status",
        "records",
        ["workspace_id", "status"],
        postgresql_concurrently=True,
    )
```

Note: `CONCURRENTLY` cannot run inside a transaction. Wrap the operation:

```python
def upgrade():
    op.execute("COMMIT")   # end the implicit transaction
    op.create_index(
        "ix_records_workspace_id_status",
        "records",
        ["workspace_id", "status"],
        postgresql_concurrently=True,
    )
```

### Removing a column (safe, two migrations)

Never remove a column in the same migration where you remove the code that uses it. The sequence:

**Step 1 — remove all code references** (deploy this first, no migration):
- Remove the column from the model file
- Remove all query/command references
- Remove from serializers

**Step 2 — drop the column** (deploy in a separate release):
```python
def upgrade():
    op.drop_column("records", "legacy_flag")
```

This two-step process ensures a rollback of Step 2 does not require re-adding code.

### Renaming a column (zero-downtime, three-step)

Postgres has no atomic rename that works without a lock. The safe pattern:

1. Add the new column (nullable migration)
2. Dual-write to both columns in application code (deploy)
3. Backfill old column values into new column (CLI script)
4. Migrate all reads to the new column (deploy)
5. Drop the old column (migration)

Never use `op.alter_column(..., new_column_name=...)` on a live table without this sequence.

---

## Enum type migrations

Adding a new value to an existing Postgres enum type:

```python
def upgrade():
    op.execute("ALTER TYPE record_status_enum ADD VALUE 'archived'")

def downgrade():
    # Postgres does not support removing enum values — log the manual steps
    # Manual: requires recreating the type, which is a significant operation
    pass
```

**Rules:**
- You can add enum values. You cannot remove them without recreating the type.
- Plan enum fields conservatively — adding is cheap, removing is expensive.
- Never rename an enum value. Add the new name, migrate code, then add a comment that the old value is deprecated (you can never remove it).

---

## Seeding required reference data in migrations

When a new table requires seed rows to exist before the application can function (e.g., `roles`), include the seed in the same migration as the table creation:

```python
def upgrade():
    op.create_table(
        "roles",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
    )
    op.execute("""
        INSERT INTO roles (client_id, name) VALUES
        ('role_admin', 'ADMIN'),
        ('role_member', 'MEMBER'),
        ('role_field', 'FIELD')
        ON CONFLICT DO NOTHING
    """)

def downgrade():
    op.drop_table("roles")
```

`ON CONFLICT DO NOTHING` makes the seed idempotent — safe to re-run if the migration is accidentally applied twice.

---

## Dangerous operations checklist

Before running any of these, get explicit team confirmation:

| Operation | Risk | Mitigation |
|---|---|---|
| `DROP TABLE` | Permanent data loss | Verify the table is unused in code and has no FK references |
| `DROP COLUMN` | Permanent data loss | Confirm all code references removed in a prior deploy |
| `ALTER COLUMN SET NOT NULL` on populated table | Lock + failure if nulls exist | Use three-migration sequence above |
| `TRUNCATE` | Permanent data loss | Only for reference tables; always use `ON CONFLICT` seed instead |
| Renaming a FK column | Breaks queries using the old name | Use the multi-step rename pattern above |
| Changing a column type | May require full table rewrite | Check Postgres type cast compatibility first |

---

## Migration chain integrity

**Never modify a migration that has already been applied** to any environment (development, staging, production). Applied migrations are the historical record. If a migration was wrong, write a new one to correct it.

Before writing a new migration:
1. Run `alembic current` to confirm your local DB is at the expected head.
2. Run `alembic history` to verify the chain is linear (no branches).
3. If the chain is branched, merge with `alembic merge heads`.

```bash
alembic current           # current revision on this DB
alembic history           # full chain
alembic show <revision>   # inspect a specific migration
```

---

## Migration review checklist

Run this before every `alembic upgrade head` in production:

- [ ] Migration was auto-generated, not hand-written
- [ ] Reviewed for correct table, type, nullability, indexes
- [ ] Zero-downtime pattern followed if table has live traffic
- [ ] No unintended tables included in the diff
- [ ] `downgrade()` is correct and reversible
- [ ] Applied to a staging environment without errors before production
- [ ] Backfill CLI command exists if the migration adds a NOT NULL column
- [ ] No `DROP TABLE` or `DROP COLUMN` without team confirmation
