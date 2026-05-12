# 27 — CLI & Scripts Contract

## What belongs here

This contract covers:
- Typer CLI scripts for backfills, seed data, and operational reports
- One-time data repair scripts

Scripts run in CI pipelines (linting, testing, audit) are not covered — those live in the CI configuration.

---

## Script location and structure

Every script lives in a dedicated file under `scripts/`:

```
scripts/
├── backfill/
│   └── backfill_record_client_ids.py
├── seed/
│   └── seed_dev.py
└── report/
    └── export_active_records.py
```

Scripts are standalone Python files — they import from the application but do not require a running server. Each script uses `typer` for CLI parsing and sets up an async event loop to use the same `AsyncSession` pattern as the rest of the codebase.

---

## Script structure

Every script follows this pattern:

```python
# scripts/backfill/backfill_record_client_ids.py
import asyncio
import typer
from my_app.models.database import init_db, close_db, _AsyncSessionLocal
from my_app.models.tables.<domain>.record import Record
from sqlalchemy import select

app = typer.Typer()


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change without committing."),
    batch_size: int = typer.Option(100, "--batch-size", help="Number of rows per batch."),
) -> None:
    """Backfill client_id on records that were created before the column was added."""
    asyncio.run(_run(dry_run=dry_run, batch_size=batch_size))


async def _run(dry_run: bool, batch_size: int) -> None:
    await init_db()
    total = 0
    offset = 0

    try:
        while True:
            async with _AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Record)
                    .where(Record.client_id.is_(None))
                    .limit(batch_size)
                    .offset(offset)
                )
                batch = result.scalars().all()
                if not batch:
                    break

                for record in batch:
                    if not dry_run:
                        from my_app.services.infra.identity import generate_id
                        record.client_id = generate_id("rec")

                if not dry_run:
                    await session.commit()

                total += len(batch)
                offset += batch_size
                typer.echo(f"Processed {total} rows...")
    finally:
        await close_db()

    if dry_run:
        typer.echo(f"[dry-run] Would update {total} records.")
    else:
        typer.echo(f"Done. Updated {total} records.")


if __name__ == "__main__":
    app()
```

Invoked as:

```bash
python scripts/backfill/backfill_record_client_ids.py --dry-run
python scripts/backfill/backfill_record_client_ids.py --batch-size 500
```

---

## Mandatory rules for all scripts

### 1. Always provide `--dry-run`

Every script that writes to the database must have a `--dry-run` option. When set:
- Print what would be changed.
- Do not call `session.commit()`.
- Exit with a summary count.

### 2. Scripts must be idempotent

Running a backfill twice must produce the same result as running it once. Always filter to only rows that need the change:

```python
# Correct — only processes rows that are actually missing the value
.where(Record.client_id.is_(None))

# Wrong — processes all rows, even already-updated ones
.where(Record.workspace_id == workspace_id)
```

### 3. Process in batches

Never load all rows into memory at once. Use `limit/offset` batching with a new session per batch:

```python
while True:
    async with _AsyncSessionLocal() as session:
        batch = (await session.execute(query.limit(batch_size).offset(offset))).scalars().all()
        if not batch:
            break
        ...
        await session.commit()
    offset += batch_size
```

### 4. Commit per batch, not once at the end

For large backfills, commit after each batch. A single commit at the end holds a write lock for the entire duration and makes recovery from failure harder.

### 5. Progress output

Scripts must print progress for operations that take more than a few seconds:

```python
typer.echo(f"Processing batch at offset {offset}...")
typer.echo(f"Done. Updated {total} rows.")
```

Use `typer.echo` — never `print`. `typer.echo` handles encoding correctly across platforms.

### 6. Never hold a session open during external calls

Fetch external data before opening the session. Sessions must be as short as possible.

```python
# Correct
external_data = [await fetch_external(row.ref) for row in rows]   # outside session
async with _AsyncSessionLocal() as session:
    for row, result in zip(rows, external_data):
        row.field = result.value
    await session.commit()

# Wrong — external call inside the session
async with _AsyncSessionLocal() as session:
    for row in rows:
        result = await fetch_external(row.ref)   # holds session open during external call
        row.field = result.value
```

---

## Seed scripts (development only)

Seed scripts populate reference data for local development. They must:

1. Live in `scripts/seed/`.
2. Be guarded against production execution:

```python
@app.command()
def main() -> None:
    """Populate development seed data. DO NOT run in production."""
    from my_app.config import settings
    if settings.environment == "production":
        typer.echo("ERROR: seed scripts are not allowed in production.", err=True)
        raise typer.Exit(1)
    asyncio.run(_run())
```

3. Be idempotent — use `ON CONFLICT DO NOTHING` or query before inserting.

---

## Report scripts (read-only)

Report scripts extract data without writing. They follow the same batch rules but never call `session.commit()`.

Output is written to stdout or a file:

```python
@app.command()
def main(output: str = typer.Option("-", "--output", help="Output file path. '-' for stdout.")) -> None:
    """Export active records as CSV."""
    asyncio.run(_run(output=output))


async def _run(output: str) -> None:
    import csv, sys
    await init_db()
    try:
        async with _AsyncSessionLocal() as session:
            result = await session.execute(
                select(Record).where(Record.is_deleted == False)
            )
            records = result.scalars().all()

        out = open(output, "w", newline="") if output != "-" else sys.stdout
        writer = csv.writer(out)
        writer.writerow(["client_id", "workspace_id", "created_at"])
        for record in records:
            writer.writerow([record.client_id, record.workspace_id, record.created_at.isoformat()])
        if output != "-":
            out.close()
            typer.echo(f"Exported to {output}")
    finally:
        await close_db()
```

---

## One-time repair scripts

Scripts written to fix a specific past incident:
1. Live in `scripts/backfill/` with the date in the filename: `repair_duplicate_client_ids_2025_03.py`.
2. Include a comment at the top of the `_run` function explaining the incident and the date.
3. Are not deleted after use — they remain as a record of what was fixed and why.

---

## What must NOT be in scripts

| Forbidden | Reason |
|---|---|
| Business logic duplicated from a service command | Logic diverges — use the service command instead |
| Direct `asyncpg` or raw SQL | Use SQLAlchemy ORM or core expressions |
| Hardcoded workspace IDs or user IDs | Pass as `--option` parameters |
| Reading `.env` manually | Use `from my_app.config import settings` |
