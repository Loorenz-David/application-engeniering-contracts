# 27 — CLI & Scripts Contract

## What belongs here

This contract covers:
- Flask CLI commands (`flask <command>`)
- One-time backfill or migration scripts
- Operational scripts (seed data, data repair, report extraction)

Scripts that run in CI pipelines (linting, testing, audit) are not covered — those live in the CI configuration.

---

## Flask CLI commands

Flask's built-in CLI is the preferred mechanism for operational scripts because it automatically provides the app context, config loading, and DB session management.

Every CLI command lives in a dedicated file:

```
my_app/
└── cli/
    ├── __init__.py          # register_cli_commands(app)
    ├── backfill.py          # data repair and one-time backfills
    ├── seed.py              # development/testing seed data
    └── report.py            # read-only report extraction
```

Commands are registered in `create_app`:

```python
# my_app/__init__.py
from .cli import register_cli_commands

def create_app(config_name: str = "development") -> Flask:
    ...
    register_cli_commands(app)
    return app
```

```python
# my_app/cli/__init__.py
from .backfill import register_backfill_commands
from .seed import register_seed_commands
from .report import register_report_commands


def register_cli_commands(app) -> None:
    register_backfill_commands(app)
    register_seed_commands(app)
    register_report_commands(app)
```

---

## Command structure

Every CLI command follows this structure:

```python
# my_app/cli/backfill.py
import click
from flask import current_app
from my_app.models import db


def register_backfill_commands(app) -> None:
    app.cli.add_command(backfill_record_client_ids)


@click.command("backfill-record-client-ids")
@click.option("--dry-run", is_flag=True, default=False, help="Print what would change without committing.")
@click.option("--batch-size", default=100, type=int, help="Number of rows per batch.")
def backfill_record_client_ids(dry_run: bool, batch_size: int) -> None:
    """Backfill client_id on records that were created before the column was added."""
    from my_app.models.tables.<domain>.record import Record
    from uuid import uuid4

    total = 0
    with current_app.app_context():
        records = (
            db.session.query(Record)
            .filter(Record.client_id == None)
            .yield_per(batch_size)
        )
        for record in records:
            if not dry_run:
                record.client_id = str(uuid4())
            total += 1

        if dry_run:
            click.echo(f"[dry-run] Would update {total} records.")
        else:
            db.session.commit()
            click.echo(f"Updated {total} records.")
```

Invoked as:

```bash
flask backfill-record-client-ids --dry-run
flask backfill-record-client-ids --batch-size 500
```

---

## Mandatory rules for all CLI commands

### 1. Always provide `--dry-run`

Every command that writes to the database must have a `--dry-run` flag. When `--dry-run` is set:
- Print what would be changed.
- Do not call `db.session.commit()`.
- Exit with a summary count.

This allows safe verification before a destructive run.

### 2. Commands must be idempotent

Running a backfill twice must produce the same result as running it once. Always filter to only the rows that need the change:

```python
# Correct — only processes rows that are actually missing the value
.filter(Record.client_id == None)

# Wrong — processes all rows, even already-updated ones
.filter(Record.workspace_id == workspace_id)
```

### 3. Process in batches

Never load all rows into memory at once. Use `yield_per()` or explicit batching:

```python
query.yield_per(batch_size)
```

Or for update-in-place:

```python
offset = 0
while True:
    batch = query.limit(batch_size).offset(offset).all()
    if not batch:
        break
    for row in batch:
        row.field = new_value
    db.session.commit()
    offset += batch_size
```

### 4. Commit per batch, not once at the end

For large backfills, commit after each batch. A single commit at the end holds a write lock for the entire duration and makes recovery from failure harder.

### 5. Progress output

Commands must print progress for operations that take more than a few seconds:

```python
click.echo(f"Processing batch starting at offset {offset}...")
click.echo(f"Done. Updated {total} rows.")
```

Use `click.echo` — never `print`. `click.echo` handles encoding correctly across platforms.

### 6. Never hold a transaction open during computation

Compute or fetch external data before opening the transaction. Transactions must be as short as possible.

```python
# Correct
external_data = [fetch_external(row.ref) for row in rows]    # external call outside transaction
with db.session.begin():
    for row, result in zip(rows, external_data):
        row.field = result.value

# Wrong — HTTP call inside the transaction
with db.session.begin():
    for row in rows:
        result = fetch_external(row.ref)    # holds transaction open during HTTP
        row.field = result.value
```

---

## Seed commands (development only)

Seed commands populate reference data for local development and testing. They must:

1. Live in `cli/seed.py`.
2. Be guarded to prevent accidental execution in production:

```python
@click.command("seed-dev-data")
def seed_dev_data() -> None:
    """Populate development seed data. DO NOT run in production."""
    if current_app.config.get("ENV") == "production":
        click.echo("ERROR: seed commands are not allowed in production.", err=True)
        raise SystemExit(1)
    ...
```

3. Be idempotent — running twice must not create duplicate rows. Use `ON CONFLICT DO NOTHING` or query before inserting.

---

## Report commands (read-only)

Report commands extract data without writing. They follow the same batch rules but never call `db.session.commit()`.

Output is written to stdout or a file — never returned as a return value:

```python
@click.command("export-active-records")
@click.option("--output", default="-", type=click.Path(), help="Output file path. '-' for stdout.")
def export_active_records(output: str) -> None:
    """Export active records as CSV."""
    import csv, sys

    records = db.session.query(Record).filter(Record.is_deleted == False).yield_per(200)

    out = open(output, "w", newline="") if output != "-" else sys.stdout
    writer = csv.writer(out)
    writer.writerow(["id", "client_id", "workspace_id", "created_at"])
    for record in records:
        writer.writerow([record.id, record.client_id, record.workspace_id, record.created_at.isoformat()])

    if output != "-":
        out.close()
        click.echo(f"Exported to {output}")
```

---

## One-time migration scripts

Scripts that are written to fix a specific past incident are:
1. Named `cli/backfill.py` if they fit the backfill pattern.
2. Committed with a comment at the top of the function explaining the incident and the date.
3. Not deleted after use — they remain as a record of what was fixed and why.

```python
@click.command("repair-duplicate-client-ids-2025-03")
def repair_duplicate_client_ids() -> None:
    """
    Repair: duplicate client_ids created by the March 2025 race condition in create_record.
    Safe to re-run. Only affects records where client_id appears more than once per workspace.
    """
    ...
```

---

## What must NOT be in CLI commands

| Forbidden | Reason |
|---|---|
| Business logic duplicated from a service command | Logic diverges — use the service command instead |
| Direct `psycopg2` or raw SQL | Use SQLAlchemy ORM or core |
| `sys.exit()` inside logic (only at guards) | Use `raise SystemExit(1)` for guard exits; let normal flow finish |
| Hardcoded workspace IDs or user IDs | Pass as `--option` parameters |
| Reading from `.env` manually | Config is loaded by `create_app` — use `current_app.config` |
