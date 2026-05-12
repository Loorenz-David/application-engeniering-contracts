from pathlib import Path

import typer

from bootstrap.writer import append_once, touch_file as _touch, write_file as _write


def _phase14_replayability(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 14 - Replayability ----------------------------------------")

    _touch(root / a / "replay" / "__init__.py", force=force)
    _touch(root / a / "event_store" / "__init__.py", force=force)

    _write(root / a / "replay" / "metadata.py", """\
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ReplayMetadata:
    replay_id: str
    source: str
    started_at: str
    dry_run: bool


@dataclass(frozen=True)
class ReplayResult:
    replay_id: str
    processed: int
    skipped: int
    failed: int


def new_replay_metadata(replay_id: str, source: str, dry_run: bool) -> ReplayMetadata:
    return ReplayMetadata(
        replay_id=replay_id,
        source=source,
        started_at=datetime.now(timezone.utc).isoformat(),
        dry_run=dry_run,
    )
""", force=force)

    _write(root / a / "replay" / "interfaces.py", """\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayEnvelope:
    source_id: str
    payload: dict
    replay_key: str


class ReplayHandlerProtocol:
    async def run(self, envelope: ReplayEnvelope, *, dry_run: bool) -> None:
        raise NotImplementedError
""", force=force)

    _write(root / a / "replay" / "safety.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event


async def execute_replay(handler, envelope, *, dry_run: bool) -> bool:
    try:
        await handler.run(envelope, dry_run=dry_run)
        log_event("replay.item.success", replay_key=envelope.replay_key, dry_run=dry_run)
        return True
    except Exception as exc:
        log_event("replay.item.failed", replay_key=envelope.replay_key, error=str(exc), dry_run=dry_run)
        return False
""", force=force)

    _write(root / a / "event_store" / "interfaces.py", """\
from __future__ import annotations


class EventStore:
    async def list_failed_events(self, limit: int = 100) -> list[dict]:
        raise NotImplementedError

    async def list_failed_jobs(self, limit: int = 100) -> list[dict]:
        raise NotImplementedError

    async def list_failed_webhooks(self, limit: int = 100) -> list[dict]:
        raise NotImplementedError
""", force=force)

    _write(root / a / "event_store" / "memory_store.py", """\
from __future__ import annotations

from .interfaces import EventStore


class InMemoryEventStore(EventStore):
    async def list_failed_events(self, limit: int = 100) -> list[dict]:
        return []

    async def list_failed_jobs(self, limit: int = 100) -> list[dict]:
        return []

    async def list_failed_webhooks(self, limit: int = 100) -> list[dict]:
        return []
""", force=force)

    _write(root / "scripts" / "replay.py", f"""\
from __future__ import annotations

import typer

from {a}.core.logging.config import configure_logging, log_event

cli = typer.Typer(help="Operational replay hooks")


@cli.command("events")
def replay_events(limit: int = 50, dry_run: bool = True) -> None:
    configure_logging()
    log_event("replay.events.start", limit=limit, dry_run=dry_run)


@cli.command("jobs")
def replay_jobs(limit: int = 50, dry_run: bool = True) -> None:
    configure_logging()
    log_event("replay.jobs.start", limit=limit, dry_run=dry_run)


@cli.command("webhooks")
def replay_webhooks(limit: int = 50, dry_run: bool = True) -> None:
    configure_logging()
    log_event("replay.webhooks.start", limit=limit, dry_run=dry_run)


if __name__ == "__main__":
    cli()
""", force=force)

    append_once(
        root / "Makefile",
        "\n# Replayability\n"
        "replay:\n"
        "\tpython scripts/replay.py events --dry-run\n",
    )
