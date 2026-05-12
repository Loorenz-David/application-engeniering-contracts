# 05 — Error Contract

## Philosophy

The error system has two layers:

1. **Domain errors** — expected failures that are part of the business domain. They have names, codes, and messages that are safe to return to the client.
2. **Unexpected exceptions** — bugs, infrastructure failures, third-party SDK crashes. They are logged and wrapped into a generic `DomainError` by `run_service`. They never surface raw stack traces to the client.

---

## Error hierarchy

```python
# errors/base.py
class DomainError(Exception):
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# errors/not_found.py
from .base import DomainError

class NotFound(DomainError):
    code = "not_found"


# errors/permissions.py
from .base import DomainError

class PermissionDenied(DomainError):
    code = "forbidden"


# errors/validation.py
from .base import DomainError

class ValidationFailed(DomainError):
    code = "bad_request"


# errors/conflict.py
from .base import DomainError

class Conflict(DomainError):
    code = "conflict"
```

Add new subclasses when a new semantic category is needed. Do not reuse `DomainError` directly for domain-specific errors — always subclass.

---

## `errors/__init__.py`

```python
from .base import DomainError
from .not_found import NotFound
from .permissions import PermissionDenied
from .validation import ValidationFailed
from .conflict import Conflict
```

Import errors from `my_app.errors`, never from the submodule directly.

---

## HTTP status mapping

The response builder maps error types to HTTP status codes:

```python
# routers/http/response.py
STATUS_MAP: dict[type[DomainError], int] = {
    NotFound: 404,
    PermissionDenied: 403,
    ValidationFailed: 400,
    Conflict: 409,
    DomainError: 500,
}
```

**Note on infrastructure overrides:** Some deployment environments (e.g., CloudFront) intercept standard 4xx codes. If your gateway requires non-standard codes, apply the offset as a transport-level concern in the response builder only — never in the domain or command layer. Document the offset clearly in `response.py`.

---

## `run_service` — the error boundary

```python
# services/run_service.py
import logging
from typing import Callable, TypeVar

from my_app.errors import DomainError
from .outcome import StatusOutcome
from .context import ServiceContext

logger = logging.getLogger(__name__)
T = TypeVar("T")


def run_service(
    service_fn: Callable[[ServiceContext], T],
    ctx: ServiceContext,
) -> StatusOutcome:
    try:
        data = service_fn(ctx)
        return StatusOutcome(data=data)
    except DomainError as e:
        return StatusOutcome(error=e)
    except Exception:
        logger.exception(
            "Unexpected error in %s | workspace=%s | user=%s",
            service_fn.__name__,
            ctx.workspace_id,
            ctx.user_id,
        )
        return StatusOutcome(error=DomainError("Unexpected internal error."))
```

**Rules:**
- `run_service` is the only place where bare `except Exception` is permitted.
- It logs the full traceback via `logger.exception`. Never swallow it.
- It always returns a `StatusOutcome`. The router checks `outcome.success` and branches.
- Pass the function directly: `run_service(create_record, ctx)`. Do not wrap it in a lambda.

---

## `StatusOutcome`

```python
# services/outcome.py
from my_app.errors import DomainError


class StatusOutcome:

    def __init__(
        self,
        data: object = None,
        error: DomainError | None = None,
    ) -> None:
        self.data = data
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None
```

---

## Raising errors in commands and domain functions

Always raise a specific subclass:

```python
from my_app.errors import NotFound, ValidationFailed, PermissionDenied

# Correct
raise NotFound(f"Order {order_id} not found.")
raise ValidationFailed("Delivery window must start before it ends.")
raise PermissionDenied("Only admin roles can delete orders.")

# Wrong — too generic
raise DomainError("Something went wrong.")
```

Never raise Python built-ins (`ValueError`, `KeyError`, `TypeError`) across layer boundaries. Convert them to `ValidationFailed` at the earliest point in the command where the bad input is detected.

---

## What errors must NOT contain

- Stack traces or internal system details in the `message` field
- Database IDs or sensitive data
- Implementation references ("SQLAlchemy error", "Redis key expired")

The `message` is sent to the client. Write it accordingly.
