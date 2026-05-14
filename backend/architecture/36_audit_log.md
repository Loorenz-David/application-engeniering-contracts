# 36 — Audit Log Contract

## What the audit log is

The audit log is a tamper-evident, append-only record of significant actions taken in the system. It answers: "Who did what, to which resource, at what time, from where?"

It is not an application log (see [17_logging.md](17_logging.md)). Application logs are for debugging. The audit log is for compliance, security investigation, and accountability. It must be readable by non-engineers.

---

## When to write an audit entry

Write an audit entry for every action that is:
- A write to a security-sensitive resource (user accounts, roles, permissions)
- A destructive action (delete, archive, bulk operation)
- An authorization decision (failed permission check on a sensitive resource)
- A high-value business event (payment, contract creation, approval)
- A configuration change (workspace settings, integration credentials)
- A privacy action (erasure request, data export)
- An administrative action taken on behalf of another user

Do NOT write audit entries for:
- Read operations (they pollute the log and make it unreadable)
- Routine background jobs (use application logs instead)
- Health check or monitoring requests
- Failed authentication attempts (these go to the security event log in `17_logging.md`)

---

## Audit log model

```python
# models/tables/audit/audit_log.py
class AuditLog(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "aud"
    __tablename__ = "audit_logs"

    # What happened
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Who did it
    actor_user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id"), nullable=True, index=True)
    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Where (workspace isolation)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True)
    # What resource was affected
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Detail payload — structured context for the event
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Metadata
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
```

**Design rules:**
- `BigInteger` primary key — audit logs grow without bound.
- No `updated_at`, no `is_deleted`. Audit entries are immutable. Never update or delete them.
- `actor_label` stores a human-readable identifier (email or name) captured at write time, so the entry remains readable even if the user account is later anonymized or deleted.
- `detail` is a free-form JSON dict for event-specific context. Keep it flat and readable — not nested objects.

---

## Writing an audit entry

Audit entries are written via a single shared function. Never write to `audit_logs` directly from commands or domain functions:

```python
# services/infra/audit/write_audit.py
from datetime import datetime, timezone
from fastapi import Request

from my_app.models import db
from my_app.models.tables.audit.audit_log import AuditLog


def write_audit(
    request: Request,
    event: str,
    workspace_id: str,
    actor_user_id: str | None = None,
    actor_label: str | None = None,
    resource_type: str | None = None,
    resource_client_id: str | None = None,
    detail: dict | None = None,
) -> None:
    """
    Write one audit log entry. Call this inside a db.session.begin() block,
    after the main write has been flushed but before commit.
    """
    entry = AuditLog(
        event=event,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        workspace_id=workspace_id,
        resource_type=resource_type,
        resource_client_id=resource_client_id,
        detail=detail or {},
        ip_address=_get_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:512],
        request_id=getattr(request.state, "request_id", None),
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(entry)


def _get_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
```

### Calling from a command

The audit entry is written **inside the same transaction** as the main write. This guarantees the audit entry is committed atomically with the action it records — an action with no audit entry cannot exist.

```python
# services/commands/workspace/delete_workspace_member.py

def delete_workspace_member(ctx: ServiceContext) -> dict:
    request = parse_delete_member_request(ctx.incoming_data)
    ctx.require_permission(Permission.MANAGE_MEMBERS)

    with db.session.begin():
        membership = (
            db.session.query(WorkspaceMembership)
            .filter(
                WorkspaceMembership.workspace_id == ctx.workspace_id,
                WorkspaceMembership.user_id == request.user_client_id,
            )
            .first()
        )
        if membership is None:
            raise NotFound("Workspace member not found.")

        db.session.delete(membership)

        # Audit entry written in the same transaction — atomic
        write_audit(
            event="workspace.member.removed",
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            actor_label=request.actor_email,   # captured from JWT claims
            resource_type="workspace_member",
            resource_client_id=request.user_client_id,
            detail={"removed_workspace_role_id": membership.workspace_role_id},
        )

    return {}
```

---

## Event naming convention

Audit event names follow the same `<domain>:<verb>` convention as domain events (see [11_infra_events.md](11_infra_events.md)):

```
<domain>:<verb>
```

Examples:
```
auth:signed-in
auth:signed-out
auth:password-changed
workspace:member-invited
workspace:member-removed
workspace:role-changed
case:state-changed
case:deleted
case:participant-removed
message:deleted
user:erased
data:export-requested
```

**Rules:**
- Use a colon separator — not dots. `workspace:member-removed`, not `workspace.member.removed`.
- Use past-tense or gerund verbs (`signed-in`, `deleted`, `state-changed`) — the event already happened.
- Be specific: `workspace:member-removed` not `workspace:updated`.
- Keep the event string under 128 characters.
- Define new events in `docs/audit/event_catalog.md` before shipping.

---

## Audited event registry

Which events are audited is controlled by a central registry, not hardcoded inside the handler. This keeps policy out of handler internals and makes expansion cheap.

```python
# services/infra/audit/audited_events.py

# Base defaults — high-risk actions audited by every generated app.
_BASE_AUDITED_EVENTS: frozenset[str] = frozenset({
    "auth:signed-in", "auth:signed-out", "auth:password-changed",
    "workspace:member-invited", "workspace:member-removed", "workspace:role-changed",
    "case:state-changed", "case:deleted", "case:participant-removed",
    "message:deleted",
})

_EXTENSIONS: set[str] = set()


def register_audited_events(events: set[str] | list[str]) -> None:
    """Register additional audited events from a domain module.
    Call during application startup before the first request.
    """
    _EXTENSIONS.update(events)


def get_audited_events() -> frozenset[str]:
    """Return the merged set: base defaults + registered extensions + AUDITED_EVENTS env override."""
    combined = set(_BASE_AUDITED_EVENTS) | _EXTENSIONS
    env_override = os.environ.get("AUDITED_EVENTS", "")
    if env_override.strip():
        combined |= {e.strip() for e in env_override.split(",") if e.strip()}
    return frozenset(combined)
```

### Adding audited events for a new domain

Add a registration call during application startup — never edit the base registry or the handler:

```python
# my_app/__init__.py — inside lifespan, after init_db()
from my_app.services.infra.audit.audited_events import register_audited_events

register_audited_events({
    "invoice:created",
    "invoice:deleted",
    "contract:signed",
})
```

### Environment override

Set `AUDITED_EVENTS` to a comma-separated list to add events without a code deploy — useful for temporary compliance requirements:

```
AUDITED_EVENTS=integration:credentials-updated,data:export-requested
```

The env override is additive — it never removes base defaults or registered extensions.

### Rules

- **Never put event names directly inside `audit_handler.py`.** The handler calls `get_audited_events()` on every event; policy lives in the registry.
- **New domains add events via `register_audited_events()` at startup**, not by importing or mutating `_BASE_AUDITED_EVENTS`.
- **`get_audited_events()` is called per event**, so startup registrations are always reflected without restart.

---

## Querying the audit log

The audit log is queryable by admins via a dedicated endpoint. Never expose raw audit log queries to MEMBER or FIELD roles.

```python
# services/queries/audit/list_audit_events.py

def list_audit_events(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.VIEW_AUDIT_LOG)

    params = parse_audit_query_params(ctx.query_params)

    query = (
        db.session.query(AuditLog)
        .filter(AuditLog.workspace_id == ctx.workspace_id)   # mandatory scope
    )

    if params.event:
        query = query.filter(AuditLog.event == params.event)
    if params.actor_user_id:
        query = query.filter(AuditLog.actor_user_id == params.actor_user_id)
    if params.resource_client_id:
        query = query.filter(AuditLog.resource_client_id == params.resource_client_id)
    if params.since:
        query = query.filter(AuditLog.created_at >= params.since)
    if params.until:
        query = query.filter(AuditLog.created_at <= params.until)

    results = query.order_by(AuditLog.created_at.desc()).limit(params.limit + 1).all()

    has_more = len(results) > params.limit
    return {
        "events": [_serialize_audit_event(e) for e in results[:params.limit]],
        "has_more": has_more,
    }
```

---

## Serializing audit entries

```python
def _serialize_audit_event(entry: AuditLog) -> dict:
    return {
        "event": entry.event,
        "actor": entry.actor_label or f"user:{entry.actor_user_id}",
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_client_id,
        "detail": entry.detail,
        "ip_address": entry.ip_address,
        "occurred_at": entry.created_at.isoformat(),
    }
```

Never include `actor_user_id` as a raw integer in API responses — use `actor_label` instead. Internal IDs are not meaningful to users reading an audit trail.

---

## Tamper-evidence rules

The audit log's value as evidence depends on it being unmodified. Enforce these at the database level:

1. **No UPDATE permissions** on `audit_logs` for the application user. The application DB role should have `INSERT` and `SELECT` only.
2. **No DELETE permissions** on `audit_logs`. Erasure anonymizes the `actor_label` field but never deletes the row.
3. **No soft-delete columns** (`is_deleted`, `deleted_at`) on `audit_logs`. Soft delete implies eventual hard delete.
4. **Partition or archive old rows** rather than deleting: use Postgres table partitioning by `created_at` year, move partitions older than your retention policy to cold storage.

```sql
-- Grant in migrations/versions/...create_audit_logs_table.py
-- (use op.execute for raw SQL grants)
REVOKE UPDATE, DELETE ON audit_logs FROM app_user;
```

---

## Retention policy

Audit logs must be retained for the legally required period for your jurisdiction and industry. Common requirements:

| Jurisdiction / industry | Minimum retention |
|---|---|
| GDPR (EU) general | Not specified, but "as long as necessary" — typically 1–3 years |
| PCI DSS (payment) | 1 year (3 months immediately accessible) |
| SOC 2 | 1 year |
| Healthcare (HIPAA) | 6 years |

Set the retention period in `docs/privacy/retention_policy.md` before going live. Do not default to "forever" — unbounded growth has storage and GDPR implications.

---

## Audit checklist for new commands

Before shipping a new write command, ask:

- [ ] Does this command change a security-sensitive resource (user, role, permission)?
- [ ] Does this command perform a destructive or irreversible action?
- [ ] Is this a high-value business event that an admin would want to see in a timeline?
- [ ] Is this a configuration or integration change?

If yes to any of these, the command must call `write_audit()` inside its transaction.
