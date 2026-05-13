# 21 — Naming Conventions Reference

This is the single source of truth for all naming decisions. When in doubt, look here first.

---

## Python files and modules

| Type | Pattern | Example |
|---|---|---|
| Command file | `<verb>_<noun>.py` | `create_record.py`, `delete_record.py` |
| Query file | `<verb>_<noun>s.py` or `get_<noun>.py` | `list_records.py`, `get_record.py` |
| Domain file | `<noun>_<concern>.py` | `record_states.py`, `record_guards.py` |
| Model table file | `<table_name_singular>.py` | `record.py`, `category.py` |
| Serializer file | `serialize_<noun>.py` | `serialize_record.py` |
| Filter builder file | `find_<noun>s.py` | `find_records.py` |
| Request parser file | `<noun>_request.py` | `create_record_request.py` |
| Event builder file | `<noun>_events.py` | `record_events.py` |
| Event handler file | `<noun>_<integration>.py` | `record_notification.py`, `record_webhook.py` |
| Job task file | `<domain>.py` (inside `tasks/`) | `tasks/notifications.py`, `tasks/analytics.py` |
| Private helper | `_<description>.py` | `_resolve_dependency.py`, `_timing_helpers.py` |

**Never:** `utils.py`, `helpers.py`, `misc.py`, `common.py`. Give files meaningful names.

---

## Python functions

| Type | Pattern | Example |
|---|---|---|
| Command function | `<verb>_<noun>(ctx) -> dict` | `create_record(ctx)` |
| Query function | `<verb>_<noun>s(ctx) -> dict` | `list_records(ctx)` |
| Domain guard | `can_<action>(entity) -> bool` | `can_record_be_deleted(record)` |
| Domain check | `is_<state>(entity) -> bool` | `is_record_in_terminal_state(record)` |
| Domain validator | `validate_<concern>(…) -> None` | `validate_date_range(start, end)` |
| Domain calculator | `recompute_<noun>(entity) -> None` | `recompute_record_totals(record)` |
| Event builder | `build_<noun>_<event>(entity) -> dict` | `build_record_created_event(record)` |
| Event emitter | `emit_<noun>_events(ctx, events) -> None` | `emit_record_events(ctx, events)` |
| Serializer | `serialize_<noun>(instance) -> dict` | `serialize_record(record)` |
| Serializer (list) | `serialize_<noun>s(instances, ctx) -> list` | `serialize_records(records, ctx)` |
| Request parser | `parse_<noun>_request(data) -> Model` | `parse_create_record_request(data)` |
| Private helper | `_<description>(…)` | `_load_categories_by_id(ctx, ids)` |
| Job task | `<verb>_<noun>_<channel>(…) -> None` | `send_record_created_notification(record_id, workspace_id)` |

---

## Python classes

| Type | Pattern | Example |
|---|---|---|
| ORM model | `PascalCase` singular | `Record`, `Category`, `RecordState` |
| Pydantic request model | `<Noun><Operation>Request` | `RecordCreateRequest`, `RecordUpdateRequest` |
| Error class | descriptive `PascalCase` | `NotFound`, `ValidationFailed`, `PermissionDenied` |
| Domain dataclass | `<Noun><Concept>` | `DateRange`, `GeocodingResult` |
| Retry policy instance | `<CONCERN>_RETRY_POLICY` | `MESSAGING_RETRY_POLICY` |
| Provider class | `<Provider><Service>Client` | `SendgridEmailClient`, `StripePaymentClient` |

---

## Database

| Type | Pattern | Example |
|---|---|---|
| Table name | `snake_case` plural | `records`, `categories`, `record_states` |
| Column name | `snake_case` | `workspace_id`, `created_at`, `client_id` |
| Foreign key | `<referenced_table_singular>_id`, `String(64)`, FK to `<table>.client_id` | `record_id`, `workspace_id`, `category_id` |
| Primary/client-facing ID | `client_id` | Prefixed ULID string, stable across DB relations and APIs |
| Timestamp (event) | `<event>_at` | `created_at`, `dispatched_at`, `deleted_at` |
| Timestamp (date) | `<context>_date` | `scheduled_date`, `effective_date` |
| Boolean flag | `is_<state>` or `has_<noun>` | `is_active`, `is_deleted`, `has_attachment` |
| Index name | `ix_<table>_<columns>` | `ix_records_workspace_id_state` |

---

## API routes

| Pattern | Example |
|---|---|
| Collection | `/api/v1/records/` |
| Single resource | `/api/v1/records/<string:record_client_id>` |
| Sub-resource collection | `/api/v1/records/<string:record_client_id>/items/` |
| State transition | `/api/v1/records/<string:record_client_id>/state/<string:state_client_id>` |
| Named action | `/api/v1/records/archive`, `/api/v1/records/import` |
| Webhook | `/webhooks/<provider>/record-created` |

Use kebab-case for multi-word path segments: `/event-history`, `/state-changes`. Never camelCase in URLs.

Public routes use `client_id` path parameters. Do not introduce internal integer IDs for addressable entities; joins and jobs use the same prefixed ULID identity. See [38_identity_resolution.md](38_identity_resolution.md).

---

## Redis keys

```
{KEY_PREFIX}:{domain}:{entity_type}:{identifier}
```

| Key | Example |
|---|---|
| Entity state cache | `myapp:entity:state:42` |
| Rate limit | `myapp:ratelimit:login:user_99` |
| Idempotency | `myapp:idempotency:create_record:abc-123` |
| Dispatcher lease | `myapp:dispatch:lease:event_abc` |
| Notification store | `myapp:notification:pending:workspace_7` |
| Token blocklist | `myapp:auth:blocklist:{jti}` |

---

## Domain events

```
<domain>.<verb>
```

| Event | Meaning |
|---|---|
| `record.created` | A new record was created |
| `record.state_changed` | A record's state transitioned |
| `record.updated` | A record's fields were updated |
| `record.deleted` | A record was permanently deleted |
| `resource.published` | A resource was published |

Use past tense. Events describe things that already happened.

---

## Blueprint names

Blueprint names are unique across the application and follow this pattern:

```
api_v{version}_{domain}_{sub_domain?}
```

| Blueprint | Name |
|---|---|
| v1 records | `api_v1_record` |
| v2 categories | `api_v2_category` |
| v1 integration | `api_v1_integration_<provider>` |
| webhooks | `webhooks_<provider>` |

---

## Environment variables

All uppercase, underscore-separated, with a consistent prefix per concern:

| Prefix | Concern | Example |
|---|---|---|
| `REDIS_` | Redis configuration | `REDIS_URI`, `REDIS_KEY_PREFIX` |
| `AI_` | AI operator flags | `AI_SESSIONS_ENABLED` |
| `EMAIL_` | Email provider | `EMAIL_PROVIDER_API_KEY`, `EMAIL_FROM_ADDRESS` |
| `SMS_` | SMS provider | `SMS_PROVIDER_API_KEY`, `SMS_FROM_NUMBER` |
| `WEBHOOK_` | Inbound webhook secrets | `WEBHOOK_SECRET_<PROVIDER>` |
| `SQLALCHEMY_` | Database | `SQLALCHEMY_DATABASE_URI` |

No prefix is acceptable only for globally standard keys: `SECRET_KEY`, `JWT_SECRET_KEY`, `FRONTEND_ORIGINS`.
