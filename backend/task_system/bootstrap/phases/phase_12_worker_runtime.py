from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase12_worker_runtime(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 12 - Worker Runtime ---------------------------------------")

    _touch(root / a / "workers" / "__init__.py", force=force)
    _touch(root / a / "queues" / "__init__.py", force=force)
    _touch(root / a / "tasks" / "__init__.py", force=force)

    _write(root / a / "queues" / "registry.py", """\
from __future__ import annotations

QUEUE_NAMES = ["default", "critical", "replay", "dead-letter"]
""", force=force)

    _write(root / a / "queues" / "runtime.py", f"""\
from __future__ import annotations

import redis
from rq import Queue

from {a}.config import settings
from {a}.queues.registry import QUEUE_NAMES


def queue_for(name: str) -> Queue:
    if name not in QUEUE_NAMES:
        raise RuntimeError(f"Unknown queue '{{name}}'. Known queues: {{', '.join(QUEUE_NAMES)}}")
    conn = redis.from_url(settings.redis_url)
    return Queue(name, connection=conn)
""", force=force)

    _write(root / a / "workers" / "logging.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event
from {a}.core.logging.context import bind_execution_context


def bind_worker_context(worker_name: str) -> tuple[str, str]:
    execution_id, worker_id = bind_execution_context(worker_id=worker_name)
    log_event("worker.context.bound", execution_id=execution_id, worker_id=worker_id)
    return execution_id, worker_id
""", force=force)

    _write(root / a / "workers" / "retry.py", """\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryMetadata:
    max_attempts: int = 5
    backoff_seconds: int = 10


DEFAULT_RETRY = RetryMetadata()
""", force=force)

    _write(root / a / "workers" / "dead_letter.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event


def send_to_dead_letter(task_type: str, payload: dict, reason: str) -> None:
    log_event(
        "worker.dead_letter",
        task_type=task_type,
        reason=reason,
        payload_size=len(str(payload)),
    )
""", force=force)

    _write(root / a / "workers" / "health.py", f"""\
from __future__ import annotations

import redis

from {a}.config import settings


def worker_healthcheck() -> dict[str, str]:
    client = redis.from_url(settings.redis_url, decode_responses=True)
    pong = client.ping()
    return {{"redis": "ok" if pong else "error"}}
""", force=force)

    _write(root / a / "workers" / "runtime.py", f"""\
from __future__ import annotations

from rq import Worker

from {a}.config import settings
from {a}.core.logging.config import configure_logging, log_event
from {a}.queues.registry import QUEUE_NAMES
from {a}.services.infra.redis import get_redis_client
from {a}.workers.logging import bind_worker_context


def run_worker(worker_name: str = "worker") -> None:
    configure_logging()
    bind_worker_context(worker_name)
    connection = get_redis_client(settings.redis_url)
    worker = Worker(QUEUE_NAMES, connection=connection, name=worker_name)
    log_event("worker.start", worker_id=worker_name)
    worker.work(with_scheduler=True)
""", force=force)

    _write(root / a / "tasks" / "registry.py", """\
from __future__ import annotations

# Explicit task registration only. No runtime auto-discovery.
REGISTERED_TASKS: dict[str, str] = {}
""", force=force)

    _write(root / "scripts" / "worker.py", f"""\
from {a}.workers.runtime import run_worker


if __name__ == "__main__":
    run_worker("worker-main")
""", force=force)

    _write(root / "scripts" / "worker_healthcheck.py", f"""\
from {a}.workers.health import worker_healthcheck


if __name__ == "__main__":
    health = worker_healthcheck()
    if health.get("redis") != "ok":
        raise SystemExit(1)
""", force=force)

    replace_once(
        root / "docker-compose.yml",
        "    depends_on:\n"
        "      postgres:\n"
        "        condition: service_healthy\n"
        "      redis:\n"
        "        condition: service_healthy\n"
        "    profiles:\n"
        "      - app\n",
        "    depends_on:\n"
        "      postgres:\n"
        "        condition: service_healthy\n"
        "      redis:\n"
        "        condition: service_healthy\n"
        "    healthcheck:\n"
        "      test: [\"CMD\", \"python\", \"scripts/worker_healthcheck.py\"]\n"
        "      interval: 10s\n"
        "      timeout: 5s\n"
        "      retries: 12\n"
        "    profiles:\n"
        "      - app\n",
    )

    append_once(
        root / "Makefile",
        "\n# Worker runtime\n"
        "worker:\n"
        "\tpython worker.py\n\n"
        "worker-dev:\n"
        "\tpython scripts/worker.py\n\n"
        "worker-logs:\n"
        "\tdocker compose logs -f worker\n",
    )
