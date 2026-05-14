# 15 — Testing Contract

## Philosophy

Tests are the proof that a contract works. They are not optional and not an afterthought. Every command, query, and domain function ships with tests. Without them, the architecture contract is unenforceable.

The test suite has three tiers. Each tier tests a different concern and runs at a different cost:

```
Unit tests          Fast, no I/O, no DB, no app context
Integration tests   Real DB, real app context, no external APIs
E2E tests           Full HTTP request → DB → response (optional, CI-only)
```

---

## Folder structure

Mirror the application structure exactly:

```
tests/
├── conftest.py                         # App factory, db fixtures, seed helpers
├── unit/
│   ├── domain/
│   │   └── <domain>/
│   │       └── test_<resource>_states.py
│   ├── services/
│   │   ├── commands/
│   │   │   └── <domain>/
│   │   │       └── test_create_record.py
│   │   └── queries/
│   │       └── <domain>/
│   │           └── test_list_records.py
│   └── routers/
│       └── api_v1/
│           └── test_record_router.py
└── integration/
    ├── commands/
    │   └── <domain>/
    │       └── test_create_record_integration.py
    └── queries/
        └── <domain>/
            └── test_list_records_integration.py
```

Test files mirror the module they test: `services/commands/<domain>/create_record.py` → `tests/unit/services/commands/<domain>/test_create_record.py`.

---

## `TestingConfig`

The testing environment requires its own config class. It must use an in-memory or dedicated test database, disable external services, and suppress event bus activity:

```python
# config/testing.py
import os

class TestingConfig:
    TESTING = True
    DEBUG = False

    SECRET_KEY = "test-secret"
    JWT_SECRET_KEY = "test-jwt-secret"

    # Use a separate test database — never the development or production database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://localhost/my_app_test",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"options": "-c timezone=UTC"}
    }

    # Disable Redis in unit tests — integration tests that need Redis set this explicitly
    REDIS_URI = None

    # Disable external services
    DISABLE_EXTERNAL_HTTP = True

    # Suppress event bus activity by default in tests
    SUPPRESS_EVENT_BUS = True

    FRONTEND_ORIGINS = "http://localhost:5173"
    WTF_CSRF_ENABLED = False
```

**Rules:**
- In test mode, override the `get_jwt_claims` dependency using FastAPI's `dependency_overrides`:

```python
from my_app.routers.utils.jwt_dep import get_jwt_claims

app.dependency_overrides[get_jwt_claims] = lambda: {
    "user_id": "usr_test",
    "workspace_id": "ws_test",
    "role_name": "admin",
    "backend_permissions": [],
}
```

Clear `dependency_overrides` in test teardown to avoid state leaking between tests.
- `TEST_DATABASE_URL` is read from the environment so CI can point to a dedicated test database. Local dev falls back to `my_app_test`.
- Never set `SQLALCHEMY_DATABASE_URI` to the same value as development or production.
- `SUPPRESS_EVENT_BUS = True` prevents test runs from publishing events to Redis or triggering external calls. Commands in integration tests call emitters, but the bus no-ops.

---

## `conftest.py` — the fixture backbone

```python
# tests/conftest.py
import pytest
from my_app import create_app
from my_app.models import db as _db


@pytest.fixture(scope="session")
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope="function")
def db(app):
    with app.app_context():
        yield _db
        _db.session.rollback()
        # truncate all tables for isolation
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture(scope="function")
def client(app):
    return app.test_client()


@pytest.fixture
def workspace(db):
    from my_app.models.tables.workspace.workspace import Workspace
    w = Workspace(name="Test Workspace")
    db.session.add(w)
    db.session.commit()
    return w


@pytest.fixture
def admin_user(db, workspace):
    from my_app.models.tables.users.user import User
    u = User(email="admin@test.com")
    u.set_password("secret")
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def admin_identity(workspace, admin_user):
    return {
        "user_id": admin_user.client_id,
        "workspace_id": workspace.client_id,
        "workspace_role_id": "wsr_test",
        "role_name": "admin",
        "permissions": [],
        "app_scope": "admin",
        "time_zone": "UTC",
    }


@pytest.fixture
def admin_ctx(admin_identity):
    from my_app.services.context import ServiceContext
    return ServiceContext(identity=admin_identity)
```

**Rules:**
- `app` fixture is `session`-scoped — created once per test run.
- `db` fixture is `function`-scoped — rolls back and truncates between every test.
- Never use `session`-scoped `db`. Tests must be fully isolated.
- Seed fixtures (`workspace`, `admin_user`) are `function`-scoped and rebuild per test.

---

## Unit tests

Unit tests test one function in isolation. They mock all I/O using `monkeypatch` or `SimpleNamespace`.

### Domain functions — zero mocking required

```python
# tests/unit/domain/<domain>/test_<resource>_states.py
import pytest
from my_app.domain.<domain>.<resource>_states import assert_valid_transition
from my_app.errors import ValidationFailed


def test_valid_transition_does_not_raise():
    assert_valid_transition(current_state_id=1, target_state_id=2)


def test_invalid_transition_raises_validation_failed():
    with pytest.raises(ValidationFailed, match="Cannot transition"):
        assert_valid_transition(current_state_id=3, target_state_id=1)


def test_terminal_state_has_no_outgoing_transitions():
    with pytest.raises(ValidationFailed):
        assert_valid_transition(current_state_id=3, target_state_id=3)
```

Domain tests need no fixtures. No app context. No DB. Just `import` and call.

### Command unit tests — mock the DB session

```python
# tests/unit/services/commands/auth/test_login_user.py
from types import SimpleNamespace
import pytest
from my_app.services.commands.auth import login_user as module


def test_login_passes_timezone_to_token_builder(monkeypatch):
    user = SimpleNamespace(check_password=lambda pw: pw == "secret")

    monkeypatch.setattr(module, "parse_login_request", lambda raw: SimpleNamespace(
        email="user@test.com",
        password="secret",
        app_scope="admin",
        time_zone="America/New_York",
    ))
    monkeypatch.setattr(module.db.session, "query", lambda _: _FakeQuery(user))
    monkeypatch.setattr(module.db.session, "commit", lambda: None)

    captured = {}
    def _fake_build_tokens(user_instance, *, app_scope, time_zone):
        captured.update(app_scope=app_scope, time_zone=time_zone)
        return {"access_token": "tok"}

    monkeypatch.setattr(module, "build_user_tokens", _fake_build_tokens)

    result = module.login_user(SimpleNamespace(incoming_data={}))

    assert result["access_token"] == "tok"
    assert captured["time_zone"] == "America/New_York"


class _FakeQuery:
    def __init__(self, result): self._result = result
    def filter(self, *_): return self
    def first(self): return self._result
```

### When to use `monkeypatch` vs `SimpleNamespace`

| Concern | Tool |
|---|---|
| Replacing a module-level function | `monkeypatch.setattr(module, "fn_name", replacement)` |
| Faking an ORM query chain | `SimpleNamespace` or a `_FakeQuery` class |
| Faking an ORM instance | `SimpleNamespace(client_id="rec_01...", workspace_id="ws_01...", ...)` |
| Replacing external SDK calls | `monkeypatch.setattr(sdk_module, "method", lambda *a, **k: ...)` |

Never patch `db.session` globally — patch it on the specific module under test.

---

## Integration tests

Integration tests use the real database and the real app context. They test a full command or query end-to-end, including ORM writes and reads.

```python
# tests/integration/commands/<domain>/test_create_record_integration.py
import pytest
from my_app.services.commands.<domain>.create_record import create_record
from my_app.services.context import ServiceContext
from my_app.models import db, Record


def test_create_record_persists_to_database(db, workspace, admin_identity):
    ctx = ServiceContext(
        incoming_data={
            "name": "Test Record",
            "category_id": None,
        },
        identity=admin_identity,
    )

    result = create_record(ctx)

    assert "record" in result
    record = db.session.query(Record).filter_by(client_id=result["record"]["client_id"]).first()
    assert record is not None
    assert record.workspace_id == workspace.client_id


def test_create_record_raises_not_found_for_missing_category(db, workspace, admin_identity):
    from my_app.errors import NotFound
    ctx = ServiceContext(
        incoming_data={
            "name": "Test Record",
            "category_id": 99999,
        },
        identity=admin_identity,
    )

    with pytest.raises(NotFound):
        create_record(ctx)
```

**Rules:**
- Integration tests must use the `db` fixture (function-scoped, auto-rolls back).
- Integration tests must never call external APIs. Mock third-party HTTP clients.
- Integration tests are slower — run them in a separate CI step from unit tests.
- Every integration test asserts the DB state, not just the return value.

---

## Testing event emission

Integration tests must verify that a command emits the expected events without actually publishing to the bus. Use `monkeypatch` to capture what the emitter receives:

```python
# tests/integration/commands/<domain>/test_create_record_integration.py
def test_create_record_emits_created_event(db, workspace, admin_identity, monkeypatch):
    emitted: list[dict] = []

    import my_app.services.commands.<domain>.create_record as cmd_module

    monkeypatch.setattr(
        cmd_module,
        "emit_record_events",
        lambda ctx, events: emitted.extend(events),
    )

    ctx = ServiceContext(
        incoming_data={"name": "Emit Test Record"},
        identity=admin_identity,
    )
    create_record(ctx)

    assert len(emitted) == 1
    assert emitted[0]["event_type"] == "record.created"
    assert emitted[0]["workspace_id"] == workspace.client_id
    assert "client_id" in emitted[0]["payload"]
```

**Rules:**
- Patch the emitter function on the command module, not on the event bus itself. This keeps the test isolated to the command's behavior.
- Assert on `event_type`, `workspace_id`, and key payload fields. Do not assert on `meta.timestamp` — it is time-dependent.
- Unit tests for event builders are separate and call the builder function directly with a `SimpleNamespace` instance.

```python
# tests/unit/services/infra/events/test_record_events.py
from types import SimpleNamespace
from my_app.services.infra.events.builders.<domain>.record_events import build_record_created_event


def test_build_record_created_event_includes_client_id():
    record = SimpleNamespace(client_id="abc-123", workspace_id="ws_123", name="Test")
    event = build_record_created_event(record)

    assert event["event_type"] == "record.created"
    assert event["payload"]["client_id"] == "abc-123"
    assert "timestamp" in event["meta"]
```

---

## What must have tests

| Layer | Required |
|---|---|
| Domain functions | Every guard, every validator, every state machine transition |
| Commands | At least one integration test per command (success path + primary failure path) |
| Commands | At least one integration test verifying the correct event is emitted |
| Queries | At least one integration test per query (returns expected data, respects workspace scope) |
| Routers | Unit test that the correct service is called and the response shape is correct |
| Auth decorators | Unit test that unauthorized roles are rejected |
| Infra jobs | Unit test that `enqueue_job` is called with correct arguments |
| Event builders | Unit test per builder verifying payload fields |

---

## pytest configuration

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v --tb=short
```

CI runs unit tests first (fast gate), then integration tests (slower gate). A failing unit test blocks integration tests from running.

---

## N+1 detection in integration tests

Every list query that serializes relationships must have at least one integration test that asserts the query count. Use a SQLAlchemy event listener fixture to count SQL statements:

```python
# tests/conftest.py
import pytest
from sqlalchemy import event as sa_event


@pytest.fixture
def count_queries(async_engine):
    """Collects every SQL statement issued during the test. Use to assert no N+1."""
    queries: list[str] = []

    @sa_event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    yield queries

    sa_event.remove(async_engine.sync_engine, "before_cursor_execute", _count)
```

**Usage in an integration test:**

```python
def test_list_records_no_n_plus_one(db, workspace, admin_ctx, count_queries):
    # Seed 5 records, each with 3 line items
    for _ in range(5):
        _create_record_with_items(db, workspace, item_count=3)

    count_queries.clear()
    list_records(admin_ctx)

    # Expect: 1 query for records + 1 selectinload for line_items = 2 total
    assert len(count_queries) <= 2, (
        f"N+1 detected: {len(count_queries)} queries for 5 records. "
        f"Queries issued: {count_queries}"
    )
```

**Rule:** The expected maximum is `1 + len(selectinloads)` per list fetch. If a list query has two `selectinload` calls, the ceiling is 3 queries regardless of how many rows are returned. A test that seeds N rows and observes N+1 or 2N+1 queries is catching an N+1 bug — add the missing `selectinload` to the query.

---

## What tests must NOT do

- Hit production or staging databases
- Make real HTTP calls to external APIs
- Use `time.sleep()` — use `freezegun.freeze_time` for time-dependent logic
- Assert on exact error message strings (assert on error type and `code` attribute)
- Share state between tests via module-level variables
