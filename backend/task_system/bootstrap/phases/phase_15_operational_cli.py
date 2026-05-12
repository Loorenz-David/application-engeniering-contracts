from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase15_operational_cli(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 15 - Operational CLI --------------------------------------")

    _touch(root / a / "operations" / "__init__.py", force=force)
    _touch(root / a / "cli" / "__init__.py", force=force)

    _write(root / a / "operations" / "seed.py", f"""\
from {a}.core.logging.config import log_event


def run_seed(dry_run: bool = False) -> None:
    log_event("ops.seed", dry_run=dry_run)
""", force=force)

    _write(root / a / "operations" / "inspect.py", f"""\
from {a}.core.logging.config import log_event


def run_inspect() -> None:
    log_event("ops.inspect")
""", force=force)

    _write(root / a / "operations" / "backfill.py", f"""\
from {a}.core.logging.config import log_event


def run_backfill(target: str, dry_run: bool = True) -> None:
    log_event("ops.backfill", target=target, dry_run=dry_run)
""", force=force)

    _write(root / a / "operations" / "cleanup.py", f"""\
from {a}.core.logging.config import log_event


def run_cleanup(target: str, dry_run: bool = True) -> None:
    log_event("ops.cleanup", target=target, dry_run=dry_run)
""", force=force)

    _write(root / a / "operations" / "replay.py", f"""\
from {a}.core.logging.config import log_event


def run_replay(source: str, dry_run: bool = True) -> None:
    log_event("ops.replay", source=source, dry_run=dry_run)
""", force=force)

    _write(root / a / "operations" / "worker_control.py", f"""\
from {a}.core.logging.config import log_event


def run_worker_control(action: str) -> None:
    log_event("ops.worker_control", action=action)
""", force=force)

    _write(root / a / "operations" / "db.py", f"""\
from {a}.core.logging.config import log_event


def run_reset_db(confirm: bool, dry_run: bool = True) -> None:
    if not confirm and not dry_run:
        raise RuntimeError("Refusing destructive reset without confirm=True")
    log_event("ops.reset_db", confirm=confirm, dry_run=dry_run)
""", force=force)

    _write(root / a / "operations" / "diagnostics.py", f"""\
from {a}.core.logging.config import log_event


def run_diagnostics() -> None:
    log_event("ops.diagnostics")
""", force=force)

    _write(root / a / "cli" / "main.py", f"""\
from __future__ import annotations

import typer

from {a}.operations.backfill import run_backfill
from {a}.operations.cleanup import run_cleanup
from {a}.operations.db import run_reset_db
from {a}.operations.diagnostics import run_diagnostics
from {a}.operations.inspect import run_inspect
from {a}.operations.replay import run_replay
from {a}.operations.seed import run_seed
from {a}.operations.worker_control import run_worker_control

cli = typer.Typer(help="Operational CLI")


@cli.command("seed")
def seed(dry_run: bool = False) -> None:
    run_seed(dry_run=dry_run)


@cli.command("inspect")
def inspect_runtime() -> None:
    run_inspect()


@cli.command("backfill")
def backfill(target: str, dry_run: bool = True) -> None:
    run_backfill(target=target, dry_run=dry_run)


@cli.command("cleanup")
def cleanup(target: str, dry_run: bool = True) -> None:
    run_cleanup(target=target, dry_run=dry_run)


@cli.command("replay")
def replay(source: str = "events", dry_run: bool = True) -> None:
    run_replay(source=source, dry_run=dry_run)


@cli.command("worker")
def worker_control(action: str) -> None:
    run_worker_control(action=action)


@cli.command("reset-db")
def reset_db(confirm: bool = False, dry_run: bool = True) -> None:
    run_reset_db(confirm=confirm, dry_run=dry_run)


@cli.command("diagnostics")
def diagnostics() -> None:
    run_diagnostics()


if __name__ == "__main__":
    cli()
""", force=force)

    _write(root / "scripts" / "ops.py", f"""\
from {a}.cli.main import cli


if __name__ == "__main__":
    cli()
""", force=force)

    _write(root / ".env.local", """\
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app_local
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=app_local
""", force=force)

    _write(root / ".env.production", """\
ENVIRONMENT=production
DATABASE_URL=
REDIS_URL=
REDIS_KEY_PREFIX=app_production
SECRET_KEY=
JWT_SECRET_KEY=
""", force=force)

    replace_once(
        root / ".gitignore",
        ".env\n",
        ".env\n.env.local\n.env.testing\n.env.validation\n.env.production\n",
    )

    append_once(
        root / "Makefile",
        "\n# Operational CLI\n"
        "seed:\n"
        "\tpython scripts/ops.py seed\n\n"
        "logs:\n"
        "\tdocker compose logs -f\n\n"
        "inspect:\n"
        "\tpython scripts/ops.py inspect\n\n"
        "reset-db:\n"
        "\tpython scripts/ops.py reset-db --dry-run True\n",
    )

    append_once(
        root / "Makefile",
        "\n.PHONY: dev-up dev-down test lint format validate worker seed logs replay inspect reset-db\n",
    )
