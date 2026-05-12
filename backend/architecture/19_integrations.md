# 19 — External Integrations Contract

## What an integration is

An integration is any communication with a system outside this application: email providers, SMS gateways, payment processors, storage services, analytics platforms, external APIs. They share two traits: they are slow and they fail unpredictably.

Every integration follows the same adapter pattern so that the rest of the application never depends on a specific provider's SDK.

---

## Adapter pattern

```
services/infra/
└── messaging/
    ├── base.py             # Abstract provider interface
    ├── provider_a/
    │   ├── client.py       # Provider SDK wrapper
    │   └── mapper.py       # Raw SDK response → domain types
    ├── provider_b/
    │   ├── client.py
    │   └── mapper.py
    └── orchestrator.py     # Selects provider, calls it, handles fallback
```

The application calls `orchestrator.send_sms(...)`. It never imports a provider SDK directly.

---

## Provider base interface

```python
# services/infra/messaging/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SmsResult:
    success: bool
    provider_message_id: str | None
    error: str | None


class SmsProvider(ABC):

    @abstractmethod
    def send(self, to: str, body: str) -> SmsResult:
        ...
```

Every concrete provider implements the base interface. Switching providers requires changing the orchestrator only.

---

## Credential injection

Provider clients receive credentials from config — never from `os.environ` directly inside the client:

```python
# Correct — credentials come from caller (via app context)
class SmsClient:
    def __init__(self, api_key: str, from_number: str) -> None:
        self._api_key = api_key
        self._from = from_number

    def send(self, to: str, body: str) -> SmsResult:
        ...


# In the orchestrator or factory
def get_sms_client() -> SmsClient:
    from my_app.config import settings
    return SmsClient(
        api_key=settings.sms_provider_api_key,
        from_number=settings.sms_from_number,
    )
```

Providers must not call `os.environ.get(...)` internally. They receive what they need from the factory function.

---

## Retry and timeout rules

Every external HTTP call must have an explicit timeout. Never let an external call hang indefinitely:

```python
import httpx

def call_external_api(endpoint: str, payload: dict) -> dict:
    with httpx.Client(timeout=10.0) as client:  # 10s hard limit
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        return response.json()
```

Timeout values come from config:

```python
# config/default.py
EXTERNAL_API_TIMEOUT_SECONDS = int(os.environ.get("EXTERNAL_API_TIMEOUT_SECONDS", "10"))
EXTERNAL_API_RETRY_MAX = int(os.environ.get("EXTERNAL_API_RETRY_MAX", "3"))
```

---

## Background job delegation

All external API calls happen in background jobs, not in the HTTP request cycle:

```python
# Wrong — blocking the web worker
def handle_record_created_send_sms(event: dict) -> None:
    sms_client.send(to=phone, body=message)   # blocks here

# Correct — enqueue and return immediately
def handle_record_created_send_sms(event: dict) -> None:
    enqueue_job(
        queue_key=QUEUE_IO,
        fn=_send_sms_job,
        kwargs={"phone": phone, "body": message, "record_id": record_id},
        job_id=f"sms-record-created-{record_id}",
        retry_policy=MESSAGING_RETRY_POLICY,
    )
```

Exception: calls during a write operation that must complete synchronously (e.g., real-time validation against an external service) may be called inline but must have a timeout and a graceful fallback.

---

## Graceful degradation

External dependencies fail. Define the fallback before writing the integration:

| Integration | Behavior when unavailable |
|---|---|
| Email provider | Log warning, add to retry queue, do not fail the primary operation |
| SMS gateway | Log warning, add to retry queue, do not fail the primary operation |
| Geocoding API | Save without coordinates, add `ctx.add_warning(...)`, retry async |
| External data API | Return empty result, surface error to frontend, allow manual entry |
| Inbound webhooks | Accept the webhook, queue for retry, return 200 immediately |

Never fail a primary operation because a secondary integration is down. Degrade gracefully and retry.

---

## Webhook receipt contract

Inbound webhooks follow this sequence:

1. Verify signature — reject immediately if invalid (return `401`)
2. Parse the raw body into a known schema — reject if malformed (return `400`)
3. **Return `200` immediately** — do not block on processing
4. Enqueue a background job that performs the actual work
5. Use a deterministic job ID based on the webhook event ID to prevent duplicate processing

```python
from fastapi import Request
from fastapi.responses import JSONResponse


@router.post("/provider/record-created")
async def provider_record_created(request: Request):
    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers):
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)

    event = _parse_record_created_webhook(raw_body)
    if event is None:
        return JSONResponse(content={"error": "Malformed payload"}, status_code=400)

    enqueue_job(
        queue_key=QUEUE_IO,
        fn=process_provider_record_created,
        kwargs={"event": event.model_dump()},
        job_id=f"webhook-record-{event.provider_event_id}",
    )

    return {"received": True}
```

Most providers retry webhook delivery if they receive a non-2xx. Returning `200` immediately prevents duplicate deliveries from retries.

---

## Mapper pattern

The raw SDK response or HTTP response body is never passed into the application layer directly. A mapper converts it to a domain type:

```python
# services/infra/<integration>/providers/<provider>/mapper.py
from my_app.services.infra.<integration>.domain.models import IntegrationResult


def map_provider_response(raw: dict) -> IntegrationResult | None:
    results = raw.get("results", [])
    if not results:
        return None

    return IntegrationResult(
        external_id=results[0]["id"],
        status=results[0]["status"],
        data=results[0].get("data"),
    )
```

The rest of the application imports `IntegrationResult`, never the raw provider dict.

---

## Integration test isolation

Integration tests must never hit real external APIs. Use `monkeypatch` to replace the provider client:

```python
def test_create_record_adds_warning_on_integration_failure(db, admin_ctx, monkeypatch):
    monkeypatch.setattr(
        "my_app.services.infra.<integration>.orchestrator.call_provider",
        lambda payload: None,  # simulate failure
    )
    result = create_record(admin_ctx)
    assert any("integration" in w.lower() for w in admin_ctx.warnings)
```
