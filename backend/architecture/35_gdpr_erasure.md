# 35 — GDPR & Data Erasure Contract

## What this covers

The right to erasure ("right to be forgotten") allows a user to request that their personal data be deleted. This contract defines what erasure means in this architecture, what gets deleted vs. anonymized, how the workflow is executed, and what guarantees the system must provide.

This contract applies to any application that stores personally identifiable information (PII) for users in the EU or for any jurisdiction with equivalent privacy law.

---

## The two erasure strategies

Not all data can or should be hard-deleted. For each data category, choose one of two strategies:

| Strategy | When to use | Example |
|---|---|---|
| **Hard delete** | The record has no referential value beyond the user. Deleting it leaves no orphaned references. | User profile, authentication credentials, session tokens |
| **Anonymize** | The record must be preserved for business integrity (financial records, audit logs, order history) but the PII fields can be scrubbed. | Invoice records, transaction history, support tickets |

**Never mix the two strategies in the same table.** Decide at design time — document it in `docs/domains/<domain>/privacy.md`.

---

## PII field inventory

Every model that stores PII must have a `# PII` comment on each sensitive column:

```python
class User(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "usr"
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)        # PII
    full_name: Mapped[str | None] = mapped_column(String(255))             # PII
    phone: Mapped[str | None] = mapped_column(String(32))                  # PII
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # credential
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set on erasure
```

Maintain a privacy inventory in `docs/privacy/pii_inventory.md` listing every table and column that holds PII. This document is updated in the same PR as any model change that adds or removes PII fields.

---

## Erasure request workflow

Erasure is not instantaneous. It follows a structured workflow to allow for verification and to satisfy legal retention requirements (e.g., financial records must be kept for 7 years in many jurisdictions).

```
1. User submits erasure request (via API endpoint)
2. System creates an ErasureRequest record (status=pending)
3. System sends a confirmation email to the user's address
4. User confirms via link (status=confirmed)
5. Background job executes the erasure (status=processing → completed)
6. System sends a completion email to the (now-scrubbed) address backup
```

### ErasureRequest model

```python
# models/tables/privacy/erasure_request.py
class ErasureRequest(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "ers"
    __tablename__ = "erasure_requests"

    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # status: pending | confirmed | processing | completed | cancelled
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_token: Mapped[str] = mapped_column(String(128), nullable=False)
    # Store the requester's email before erasure so we can send the completion notice
    requester_email_backup: Mapped[str] = mapped_column(String(255), nullable=False)
```

---

## The erasure command

```python
# services/commands/privacy/execute_erasure.py

def execute_erasure(erasure_request: ErasureRequest) -> None:
    """
    Executes the erasure for a confirmed request.
    Called by the background job — not by a router.
    """
    user_id = erasure_request.user_id
    workspace_id = erasure_request.workspace_id

    with db.session.begin():
        erasure_request.status = "processing"

    try:
        with db.session.begin():
            _hard_delete_user_credentials(user_id)
            _anonymize_user_profile(user_id)
            _anonymize_workspace_records(user_id, workspace_id)
            _revoke_all_sessions(user_id)

            erasure_request.status = "completed"
            erasure_request.completed_at = datetime.now(timezone.utc)

    except Exception:
        with db.session.begin():
            erasure_request.status = "failed"
        logger.exception("Erasure failed | erasure_request_id=%s user_id=%s", erasure_request.client_id, user_id)
        raise
```

### Hard-delete functions

```python
def _hard_delete_user_credentials(user_id: str) -> None:
    # Delete auth tokens, OAuth connections, API keys, MFA secrets
    db.session.query(UserSession).filter(UserSession.user_id == user_id).delete()
    db.session.query(OAuthConnection).filter(OAuthConnection.user_id == user_id).delete()
    db.session.flush()


def _anonymize_user_profile(user_id: str) -> None:
    user = db.session.query(User).filter(User.client_id == user_id).first()
    if user is None:
        return

    user.email = f"erased_{user.client_id}@erased.invalid"
    user.full_name = None
    user.phone = None
    user.password_hash = "ERASED"
    user.is_deleted = True
    user.deleted_at = datetime.now(timezone.utc)
    user.erased_at = datetime.now(timezone.utc)
```

---

## What must be deleted vs. anonymized

Document this decision per domain at design time:

| Domain | Strategy | Rationale |
|---|---|---|
| `users` profile fields | Anonymize | `user_id` FK is referenced by many tables |
| `user_sessions` | Hard delete | No downstream reference value |
| `workspace_memberships` | Hard delete | Access record — no value after user is gone |
| Transaction/billing records | Anonymize | Legal retention requirement |
| Audit log entries | Anonymize | Tamper-evident record cannot be deleted |
| `ErasureRequest` itself | Retain | Evidence the request was processed |

Fill in this table for every domain in `docs/privacy/erasure_scope.md`.

---

## Legal retention hold

Some data cannot be deleted regardless of the user's request due to legal obligations:

```python
# services/commands/privacy/execute_erasure.py

def _can_immediately_erase(user_id: str) -> bool:
    # Example: cannot erase if there are unpaid invoices or active legal holds
    has_open_invoices = db.session.query(Invoice).filter(
        Invoice.user_id == user_id,
        Invoice.status.in_(["open", "disputed"]),
    ).count() > 0

    return not has_open_invoices
```

If a retention hold exists, the erasure is deferred. The system must:
1. Notify the user that the erasure is pending a hold
2. Set the request status to `held`
3. Automatically re-attempt after the hold expires (via a scheduled job)
4. Complete erasure when the hold is lifted

---

## Storage erasure

User-uploaded files (see [34_file_storage.md](34_file_storage.md)) must be deleted from object storage as part of the erasure:

```python
def _delete_user_files(user_id: str, workspace_id: str) -> None:
    attachments = (
        db.session.query(RecordAttachment)
        .filter(
            RecordAttachment.uploaded_by_user_id == user_id,
            RecordAttachment.workspace_id == workspace_id,
        )
        .all()
    )
    client = get_storage_client()
    for attachment in attachments:
        client.delete_object(attachment.storage_key)
    # DB rows are cleaned up by the anonymization step or cascade
```

---

## Audit trail of the erasure

The erasure itself must be logged in the audit log (see [36_audit_log.md](36_audit_log.md)) before the user's identity is scrubbed:

```python
# Inside execute_erasure, before anonymizing the user:
_write_audit_entry(
    event="user.erased",
    actor_id=user_id,         # the user being erased, before scrubbing
    workspace_id=workspace_id,
    detail={"erasure_request_id": erasure_request.client_id},
)
```

The audit entry records that erasure occurred. The user's identity in the audit log entry is then anonymized by `_anonymize_user_profile`.

---

## Response to erasure requests (API)

```python
@privacy_bp.route("/erasure-request", methods=["POST"])
@jwt_required()
def create_erasure_request():
    ctx = ServiceContext(
        incoming_data=request.get_json(silent=True) or {},
        identity=get_jwt(),
    )
    outcome = run_service(create_erasure_request_command, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    # Always return 202 Accepted — erasure is asynchronous
    return build_ok(outcome.data, status=202)
```

Erasure is always asynchronous. Never attempt to execute erasure synchronously in a request handler — it may take seconds and will time out.

---

## Compliance checklist

- [ ] PII inventory document exists at `docs/privacy/pii_inventory.md`
- [ ] Erasure scope document exists at `docs/privacy/erasure_scope.md`
- [ ] `ErasureRequest` model and migration exist
- [ ] Confirmation email is sent before erasure executes
- [ ] Erasure job anonymizes all PII fields (does not skip any table)
- [ ] Storage files are deleted as part of erasure
- [ ] Audit log records the erasure event before anonymization
- [ ] Legal retention holds are checked before executing
- [ ] Erasure request is retained as evidence of completion
- [ ] `erased_at` is set on the user row after completion
