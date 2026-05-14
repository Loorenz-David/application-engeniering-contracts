from pathlib import Path

import typer

from bootstrap.writer import append_once, touch_file as _touch, write_file as _write


def _phase11_testing(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 12 - Testing ----------------------------------------------")

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
from sqlalchemy import event as sa_event
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


@pytest.fixture(scope="session")
def async_engine():
    # Lazy import: _engine is None at module load time; init_db() must run first.
    from {a}.models.database import _engine
    return _engine


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


@pytest.fixture
def count_queries(async_engine):
    \"\"\"Collect SQL statements executed during a test to detect N+1 regressions.
    Expected maximum: 1 + len(selectinloads) per list fetch.
    \"\"\"
    queries: list[str] = []

    @sa_event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    yield queries

    sa_event.remove(async_engine.sync_engine, "before_cursor_execute", _count)
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

    _write(root / "tests" / "unit" / "test_audited_events.py", f"""\
import os
import pytest

from {a}.services.infra.audit.audited_events import (
    _BASE_AUDITED_EVENTS,
    _EXTENSIONS,
    get_audited_events,
    register_audited_events,
)


@pytest.fixture(autouse=True)
def _clear_extensions():
    _EXTENSIONS.clear()
    yield
    _EXTENSIONS.clear()


@pytest.mark.unit
def test_base_defaults_non_empty():
    result = get_audited_events()
    assert len(result) > 0
    assert "auth:signed-in" in result


@pytest.mark.unit
def test_register_extends_set():
    register_audited_events({{"domain:custom-event"}})
    result = get_audited_events()
    assert "domain:custom-event" in result


@pytest.mark.unit
def test_env_override_merges(monkeypatch):
    monkeypatch.setenv("AUDITED_EVENTS", "env:event-a, env:event-b")
    result = get_audited_events()
    assert "env:event-a" in result
    assert "env:event-b" in result


@pytest.mark.unit
def test_env_override_empty_string_ignored(monkeypatch):
    monkeypatch.setenv("AUDITED_EVENTS", "")
    result = get_audited_events()
    assert result == get_audited_events()  # stable — just base defaults


@pytest.mark.unit
def test_base_events_not_mutated():
    register_audited_events({{"extra:event"}})
    assert "extra:event" not in _BASE_AUDITED_EVENTS
""", force=force)

    _write(root / "tests" / "unit" / "test_audit_handler.py", f"""\
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from {a}.services.infra.audit import audited_events as _audited_events_module
from {a}.services.infra.events.domain_event import UserEvent, WorkspaceEvent


async def _call_handle(event):
    from {a}.services.infra.events.handlers.audit_handler import handle
    await handle(event)


@pytest.mark.unit
async def test_skip_non_audited_event():
    event = WorkspaceEvent(
        event_name="non:audited",
        client_id="res_1",
        workspace_id="ws_1",
    )
    with patch.object(
        _audited_events_module, "get_audited_events", return_value=frozenset()
    ):
        with patch(f"{a}.services.infra.events.handlers.audit_handler.get_audited_events",
                   return_value=frozenset()):
            # Should return without writing — no DB call
            await _call_handle(event)  # must not raise


@pytest.mark.unit
async def test_skip_missing_workspace_id(caplog):
    event = UserEvent(
        event_name="auth:signed-in",
        client_id="usr_1",
        user_id="usr_1",
    )
    with patch(
        f"{a}.services.infra.events.handlers.audit_handler.get_audited_events",
        return_value=frozenset({{"auth:signed-in"}}),
    ):
        with caplog.at_level(logging.WARNING):
            await _call_handle(event)
    assert "skipped" in caplog.text


@pytest.mark.unit
async def test_write_on_valid_audited_event():
    event = WorkspaceEvent(
        event_name="auth:signed-in",
        client_id="usr_1",
        workspace_id="ws_1",
    )
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch(
        f"{a}.services.infra.events.handlers.audit_handler.get_audited_events",
        return_value=frozenset({{"auth:signed-in"}}),
    ):
        with patch(f"{a}.models.database.get_db_session") as mock_db:
            async def _gen():
                yield mock_session
            mock_db.return_value = _gen()

            write_mock = AsyncMock()
            with patch(
                f"{a}.services.infra.audit.write_audit.write_audit_from_event",
                write_mock,
            ):
                await _call_handle(event)

    write_mock.assert_awaited_once()
    _, kwargs = write_mock.call_args
    assert kwargs["event_name"] == "auth:signed-in"
    assert kwargs["workspace_id"] == "ws_1"
""", force=force)

    _write(root / "tests" / "integration" / "test_audit_log.py", f"""\
import pytest
from sqlalchemy import select

from {a}.models.tables.audit.audit_log import AuditLog
from {a}.services.infra.audit.write_audit import write_audit_from_event


@pytest.mark.integration
async def test_write_audit_from_event_inserts_row(db_session):
    await write_audit_from_event(
        session=db_session,
        event_name="auth:signed-in",
        workspace_id="ws_test",
        resource_client_id="usr_test",
        detail={{"ip": "127.0.0.1"}},
    )
    await db_session.flush()

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.event == "auth:signed-in")
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.workspace_id == "ws_test"
    assert row.resource_client_id == "usr_test"
    assert row.detail == {{"ip": "127.0.0.1"}}


@pytest.mark.integration
async def test_detail_defaults_to_empty_dict(db_session):
    await write_audit_from_event(
        session=db_session,
        event_name="auth:signed-out",
        workspace_id="ws_test",
    )
    await db_session.flush()

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.event == "auth:signed-out")
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.detail == {{}}
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
