# 17 — Logging & Observability Contract

## Logger setup

Every module that logs gets its own logger named after the module. Never use the root logger directly:

```python
import logging

logger = logging.getLogger(__name__)
```

`__name__` resolves to the full module path (e.g., `my_app.services.commands.<domain>.create_record`). This makes log lines trivially traceable to the source file.

---

## Log levels

| Level | When to use |
|---|---|
| `DEBUG` | Internal state useful during development. Disabled in production. |
| `INFO` | Normal operations that are worth recording: job enqueued, event dispatched, integration connected. |
| `WARNING` | Degraded but recoverable: Redis unavailable (falling back to in-process), missing optional config, non-fatal validation issue. |
| `ERROR` | A specific operation failed and the failure was handled. The system continues. |
| `CRITICAL` | The application cannot continue. Reserved for startup failures. |

`logger.exception(...)` is for the `run_service` error boundary and any `except` block where you want the full traceback. It logs at `ERROR` and appends the exception traceback automatically.

---

## Request correlation ID

Every inbound HTTP request gets a unique `request_id`. This ID is threaded through every log line produced during that request, so you can reconstruct the full story of a request from logs alone.

### Generating the ID

Generate and attach the ID in FastAPI middleware:

```python
# routers/middleware/request_id.py
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

- Accept `X-Request-ID` from the caller when present (load balancers and API gateways often set this).
- Generate a new UUID when not provided.
- Echo the ID back in the response header so clients can correlate errors.

Register in `create_app()`:

```python
from my_app.routers.middleware.request_id import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)
```

### Threading the ID through log lines

Add `request_id` to every structured log call inside the request context:

```python
# When request context is available, pass request.state.request_id via ctx or as a parameter.
# Outside request context (background jobs), generate a job-scoped correlation ID:
import uuid
job_id = str(uuid.uuid4())

logger.info(
    "Record created | request_id=%s workspace=%s record_id=%s",
    ctx.request_id if hasattr(ctx, "request_id") else "-",
    ctx.workspace_id,
    record.client_id,
)
```

Background jobs should generate their own correlation ID at job entry:

```python
# services/tasks/notifications.py
import uuid

def send_record_created_notification(record_id: str, workspace_id: str) -> None:
    job_id = str(uuid.uuid4())
    logger.info(
        "Job started | job_id=%s record_id=%s workspace_id=%s",
        job_id, record_id, workspace_id,
    )
    # pass job_id to all subsequent log calls within this job
```

### `run_service` logs the request ID

```python
def run_service(service_fn, ctx):
    try:
        data = service_fn(ctx)
        return StatusOutcome(data=data)
    except DomainError as e:
        return StatusOutcome(error=e)
    except Exception:
        logger.exception(
            "Unexpected error in %s | request_id=%s workspace=%s user=%s",
            service_fn.__name__,
            getattr(ctx, "request_id", "-"),
            ctx.workspace_id,
            ctx.user_id,
        )
        return StatusOutcome(error=DomainError("Unexpected internal error."))
```

---

## Structured log fields

Every log statement in a command, query, or job must include the identity fields relevant to that context:

```python
# Correct
logger.info(
    "Record created | workspace=%s record_id=%s",
    ctx.workspace_id,
    record.client_id,
)

# Wrong — no context, untraceable in production
logger.info("Record created successfully.")
```

Standard fields by layer:

| Layer | Fields to include |
|---|---|
| Commands | `request_id`, `workspace_id`, `user_id`, entity ID |
| Queries | `request_id`, `workspace_id`, query param summary |
| Jobs | `job_id`, `workspace_id`, entity ID, job function name |
| Event handlers | `event_type`, `workspace_id`, entity ID |
| Infra (Redis, events) | operation, key or channel name |
| Error boundary (`run_service`) | `request_id`, `workspace_id`, `user_id`, service function name |

---

## What must NEVER be logged

These items must never appear in any log output at any level:

| Forbidden | Why |
|---|---|
| Passwords or password hashes | Security — credential exposure |
| JWT tokens or refresh tokens | Security — session hijacking |
| API keys or webhook secrets | Security — third-party access |
| Full credit card or bank data | PCI/legal compliance |
| Full PII (SSN, passport numbers) | Privacy/legal compliance |
| Raw exception messages from the ORM that contain SQL | May expose schema or data |
| Raw third-party API responses | May contain credentials or customer PII |

Logging `ctx.incoming_data` verbatim is **forbidden**. Extract only the specific fields you need:

```python
# Wrong
logger.error("Failed | incoming_data=%s", ctx.incoming_data)

# Correct
logger.error(
    "Unexpected error in create_record | workspace=%s user=%s",
    ctx.workspace_id, ctx.user_id,
)
```

---

## Error boundary logging

`run_service` is the single place where unexpected exceptions are logged with a full traceback:

```python
def run_service(service_fn, ctx):
    try:
        data = service_fn(ctx)
        return StatusOutcome(data=data)
    except DomainError as e:
        return StatusOutcome(error=e)
    except Exception:
        logger.exception(
            "Unexpected error in %s | workspace=%s user=%s",
            service_fn.__name__,
            ctx.workspace_id,
            ctx.user_id,
        )
        return StatusOutcome(error=DomainError("Unexpected internal error."))
```

Do not add `logger.exception` calls in individual commands or queries to re-log errors that `run_service` already handles. That creates duplicate log lines.

---

## External API call logging

Log every external call at `INFO` on success and `WARNING` / `ERROR` on failure. Log latency:

```python
import time

def call_external_sms_provider(to: str, body: str) -> None:
    start = time.monotonic()
    try:
        provider_client.send(to=to, body=body)
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("SMS sent | to=%s latency_ms=%d", to, elapsed)
    except Exception:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.exception("SMS failed | to=%s latency_ms=%d", to, elapsed)
        raise
```

Latency logging on external calls is **required**. It is the primary signal for detecting degraded third-party services.

---

## Slow query detection

Queries that exceed a threshold must log a warning:

```python
import time

SLOW_QUERY_MS = 500

def list_records(ctx: ServiceContext) -> dict:
    start = time.monotonic()
    results = query.all()
    elapsed = int((time.monotonic() - start) * 1000)

    if elapsed > SLOW_QUERY_MS:
        logger.warning(
            "Slow query in list_records | workspace=%s elapsed_ms=%d",
            ctx.workspace_id,
            elapsed,
        )
    ...
```

`SLOW_QUERY_MS` is a config value, not a hardcoded constant.

---

## Security event logging

The following events must always be logged at `WARNING` or above:

| Event | Level | Fields |
|---|---|---|
| Login success | `INFO` | `user_id`, `app_scope` |
| Login failure | `WARNING` | `email` (not password), `app_scope` |
| Permission denied (401/403) | `WARNING` | `user_id`, `workspace_id`, `endpoint` |
| JWT decode failure | `WARNING` | `endpoint`, reason |
| Webhook signature verification failure | `WARNING` | `source` (provider name), `endpoint` |
| AI tool access denied | `WARNING` | `user_id`, `tool_name`, `operation` |

Security events are a first-class observability concern. They enable audit trails and intrusion detection.

---

## Log format in production

Production logs emit JSON. The log format is configured in the WSGI entry point or gunicorn config, not in the application code. Application code must never format log strings as JSON manually — the logging handler is responsible for serialization.

---

## What NOT to do

```python
# Wrong — root logger, no context
import logging
logging.info("Something happened")

# Wrong — print statements in production code
print(f"Created record {record.client_id}")

# Wrong — logging PII
logger.info("User logged in | email=%s password=%s", email, password)

# Wrong — logging full payload
logger.debug("Incoming data: %s", ctx.incoming_data)

# Wrong — no context fields
logger.error("create_record failed")
```
