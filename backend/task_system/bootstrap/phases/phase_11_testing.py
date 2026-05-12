from pathlib import Path

import typer

from bootstrap.writer import append_once, touch_file as _touch, write_file as _write


def _phase11_testing(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 11 - Testing ----------------------------------------------")

    test_dirs = [
        root / "tests" / "unit",
        root / "tests" / "integration",
        root / "tests" / "e2e",
        root / "tests" / "fixtures",
        root / "tests" / "factories",
        root / "tests" / "helpers",
    ]
    for test_dir in test_dirs:
        _touch(test_dir / ".gitkeep", force=force)

    _write(root / "pytest.ini", """\
[pytest]
addopts = -ra --strict-markers --strict-config
python_files = test_*.py
python_classes = Test*
python_functions = test_*
testpaths = tests
asyncio_mode = auto
markers =
    unit: fast deterministic unit tests
    integration: database and service integration tests
    e2e: end-to-end runtime tests
""", force=force)

    _write(root / "tests" / "conftest.py", f"""\
from __future__ import annotations

import os
from collections.abc import Generator
from uuid import uuid4

import pytest
import pytest_asyncio
import redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from {a}.config import settings
from {a}.models.database import get_db


@pytest.fixture(scope="session")
def isolated_redis_prefix() -> Generator[str, None, None]:
    prefix = f"{{settings.redis_key_prefix}}:test:{{uuid4().hex[:8]}}"
    old = os.environ.get("REDIS_KEY_PREFIX")
    os.environ["REDIS_KEY_PREFIX"] = prefix
    try:
        yield prefix
    finally:
        if old is None:
            os.environ.pop("REDIS_KEY_PREFIX", None)
        else:
            os.environ["REDIS_KEY_PREFIX"] = old


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async for session in get_db():
        yield session
        await session.rollback()


@pytest.fixture
def redis_client(isolated_redis_prefix: str):
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        for key in client.scan_iter(f"{{isolated_redis_prefix}}:*"):
            client.delete(key)
""", force=force)

    _write(root / "tests" / "helpers" / "test_settings.py", """\
from __future__ import annotations


def assert_deterministic_environment(env: dict[str, str]) -> None:
    required = ["DATABASE_URL", "REDIS_URL", "ENVIRONMENT"]
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise RuntimeError(f"Missing deterministic test settings: {', '.join(missing)}")
""", force=force)

    _write(root / "tests" / "unit" / "test_domain_smoke.py", """\
def test_unit_smoke() -> None:
    assert 2 + 2 == 4
""", force=force)

    _write(root / "tests" / "integration" / "test_health_smoke.py", """\
import pytest


@pytest.mark.integration
def test_integration_smoke() -> None:
    assert True
""", force=force)

    _write(root / "tests" / "e2e" / "test_e2e_smoke.py", """\
import pytest


@pytest.mark.e2e
def test_e2e_smoke() -> None:
    assert True
""", force=force)

    _write(root / ".env.testing", """\
ENVIRONMENT=testing
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app_test
REDIS_URL=redis://127.0.0.1:6379/1
REDIS_KEY_PREFIX=app_testing
""", force=force)

    append_once(
        root / "requirements-dev.txt",
        "\nruff==0.11.8\n",
    )

    append_once(
        root / "Makefile",
        "\n# Testing\n"
        "test:\n"
        "\tpytest -m 'not e2e'\n\n"
        "test-unit:\n"
        "\tpytest tests/unit -m unit\n\n"
        "test-integration:\n"
        "\tpytest tests/integration -m integration\n\n"
        "test-e2e:\n"
        "\tpytest tests/e2e -m e2e\n",
    )
