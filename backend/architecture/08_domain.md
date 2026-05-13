# 08 — Domain Contract (Pure Business Logic)

## What the domain layer is

The domain layer contains pure Python functions that encode business rules. It has no I/O: no database access, no HTTP calls, no Redis, no file system. Given the same inputs, a domain function always returns the same output.

This makes domain functions:
- **Trivially testable** — no mocking required
- **Safe to call anywhere** — no side effects
- **The authoritative source** for business invariants

---

## Location

```
domain/
└── <resource>/
    ├── <resource>_states.py        # Valid states and transition rules
    ├── <resource>_guards.py        # Boolean guards (can_be_deleted, is_terminal)
    ├── <resource>_validators.py    # Invariant validators (raise on violation)
    └── <resource>_calculations.py  # In-memory computations
```

**Important:** Domain lives at the top level of the application package, not inside `services/`. It is its own layer.

```
my_app/
├── domain/          # <-- top-level, not services/domain/
├── services/
├── models/
└── routers/
```

---

## What belongs in the domain layer

| Type | Pattern | Example |
|---|---|---|
| State machine | Valid states, valid transitions | `VALID_TRANSITIONS`, `TERMINAL_STATES` |
| Guard function | Returns `bool` | `can_record_be_deleted(record) -> bool` |
| Invariant validator | Raises on violation | `validate_date_range(start, end) -> None` |
| In-memory calculation | Mutates the ORM instance in-memory only | `recompute_totals(entity) -> None` |
| Normalization | Returns cleaned value | `normalize_phone_number(raw: str) -> str` |
| Classification | Returns a typed enum or constant | `resolve_category(entity) -> Category` |

---

## What does NOT belong in the domain layer

| Anti-pattern | Correct location |
|---|---|
| `db.session.query(...)` | `services/queries/` |
| `db.session.add(...)` | `services/commands/` |
| `requests.get(...)` | `services/infra/` |
| `redis_client.set(...)` | `services/infra/` |
| Flask `current_app` access | `services/infra/` |
| ORM model queries | Domain functions receive instances — they read fields, never query |

The domain layer **may** receive ORM model instances as arguments (to read their fields), but it must not call any database methods on them.

Domain functions stay pure. If a complex command needs to track touched entities, pending events, or cascading response data, that tracking belongs in service-layer orchestration using `WorkContext`, not in the pure domain layer. See [39_work_context.md](39_work_context.md).

---

## Domain function patterns

### Guard function

Returns `True` or `False` based on the entity's current state. Never raises. Named `can_<action>` or `is_<state>`.

```python
# domain/<resource>/<resource>_guards.py

TERMINAL_STATE_IDS: set[int] = {3, 4}


def can_record_be_deleted(record) -> bool:
    return record.state_id not in TERMINAL_STATE_IDS


def is_record_in_terminal_state(record) -> bool:
    return record.state_id in TERMINAL_STATE_IDS
```

### Invariant validator

Raises `ValidationFailed` when a business rule is violated. Returns `None` on success. Named `validate_<concern>` or `assert_<condition>`.

```python
# domain/<resource>/<resource>_validators.py
from datetime import datetime
from my_app.errors import ValidationFailed


def validate_date_range(start: datetime, end: datetime) -> None:
    if start >= end:
        raise ValidationFailed("Start date must be before end date.")

    if (end - start).total_seconds() < 3600:
        raise ValidationFailed("The date range must span at least one hour.")


def validate_required_fields(name: str, category_id: str | None) -> None:
    if not name or not name.strip():
        raise ValidationFailed("Name is required and cannot be blank.")
    if category_id is not None and not category_id.strip():
        raise ValidationFailed("Category ID cannot be blank.")
```

### In-memory calculation

Mutates the ORM instance in-memory before the session is committed. No database calls. Named `recompute_<noun>`.

```python
# domain/<resource>/<resource>_calculations.py


def recompute_record_totals(record) -> None:
    """Update aggregate fields on the record from its in-memory children."""
    record.total_items = len(record.line_items)
    record.total_value = sum((item.unit_price * item.quantity) for item in record.line_items)
    record.total_weight = sum((item.weight or 0) for item in record.line_items)
```

### State machine

Defines the valid states and which transitions are permitted. The state machine is data, not logic — the assertion function is the only behavior.

```python
# domain/<resource>/<resource>_states.py
from my_app.errors import ValidationFailed


# Define state IDs as named constants so code is readable without looking up the DB
STATE_DRAFT     = 1
STATE_ACTIVE    = 2
STATE_CLOSED    = 3
STATE_CANCELLED = 4

TERMINAL_STATE_IDS: set[int] = {STATE_CLOSED, STATE_CANCELLED}

VALID_TRANSITIONS: dict[int, set[int]] = {
    STATE_DRAFT:     {STATE_ACTIVE, STATE_CANCELLED},
    STATE_ACTIVE:    {STATE_CLOSED, STATE_CANCELLED},
    STATE_CLOSED:    set(),    # terminal
    STATE_CANCELLED: set(),    # terminal
}


def assert_valid_transition(current_state_id: int, target_state_id: int) -> None:
    allowed = VALID_TRANSITIONS.get(current_state_id, set())
    if target_state_id not in allowed:
        raise ValidationFailed(
            f"Cannot transition from state {current_state_id} to {target_state_id}."
        )
```

Called from the command after loading the entity:

```python
# services/commands/<resource>/update_record_state.py
from my_app.domain.<resource>.<resource>_states import assert_valid_transition
from my_app.services.identity.records import resolve_record


def update_record_state(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.MANAGE_RECORDS)
    request = parse_update_record_state_request(ctx.incoming_data)

    # resolve_record enforces workspace_id and is_deleted filtering — see 38_identity_resolution.md
    record = resolve_record(ctx, request.ref)
    assert_valid_transition(record.state_id, request.target_state_id)

    with db.session.begin():
        record.state_id = request.target_state_id
    ...
```

---

## Type hints

All domain functions have fully annotated signatures. No `Any`, no bare collections without type parameters:

```python
# Correct
def validate_date_range(start: datetime, end: datetime) -> None: ...
def resolve_category(code: str | None, default: int) -> int: ...
def recompute_record_totals(record: "Record") -> None: ...

# Wrong
def validate_date_range(start, end): ...
```

---

## Testing the domain layer

Domain functions are tested with plain `pytest` — no Flask app context, no database, no mocks:

```python
# tests/unit/domain/<resource>/test_<resource>_states.py
import pytest
from my_app.domain.<resource>.<resource>_states import assert_valid_transition
from my_app.errors import ValidationFailed


def test_valid_transition_does_not_raise():
    assert_valid_transition(current_state_id=1, target_state_id=2)


def test_invalid_transition_raises():
    with pytest.raises(ValidationFailed):
        assert_valid_transition(current_state_id=3, target_state_id=1)  # terminal → any


def test_terminal_state_has_no_outgoing_transitions():
    with pytest.raises(ValidationFailed):
        assert_valid_transition(current_state_id=3, target_state_id=3)
```

Domain tests need no fixtures. No app context. No DB. Just `import` and call.
