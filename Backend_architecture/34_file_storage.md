# 34 — File Storage Contract

## What this covers

File uploads, downloads, and lifecycle management for user-supplied binary content: images, documents, exports, and imports. This contract defines how files move between the client, the application, and object storage — and what happens to files that are never completed or attached.

---

## Architecture

Files are stored in object storage (S3, GCS, or equivalent). The application never stores binary data in the database or on the application server's filesystem.

```
Client
  │
  │  1. Request upload URL
  ▼
Application API
  │
  │  2. Generate presigned upload URL + storage key
  ▼
Object Storage (S3/GCS)
  │
  ◄─ 3. Client uploads directly to storage (no traffic through API server)
  │
  │  4. Client notifies API: "upload complete"
  ▼
Application API
  │
  │  5. Validate the object exists; attach to domain record
  ▼
Database (stores metadata only — key, size, MIME type, status)
```

The application server never proxies file content. This keeps upload latency low and application server memory usage flat regardless of file size.

---

## Presigned URL flow

### Step 1 — Request an upload URL

```python
# routers/api_v1/files.py
@file_bp.route("/upload-url", methods=["POST"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def request_upload_url():
    ctx = build_context(request)
    return run_service(generate_upload_url, ctx)
```

```python
# services/commands/files/generate_upload_url.py
import uuid
from my_app.services.infra.storage import get_storage_client


def generate_upload_url(ctx: ServiceContext) -> dict:
    request = parse_generate_upload_url_request(ctx.incoming_data)

    storage_key = _build_storage_key(
        workspace_id=ctx.workspace_id,
        file_name=request.file_name,
    )

    client = get_storage_client()
    presigned_url = client.generate_presigned_put_url(
        key=storage_key,
        content_type=request.content_type,
        expires_in=300,   # 5 minutes — client must upload within this window
    )

    # Record the pending upload so we can validate and clean up later
    with db.session.begin():
        upload = PendingUpload(
            workspace_id=ctx.workspace_id,
            storage_key=storage_key,
            file_name=request.file_name,
            content_type=request.content_type,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.session.add(upload)

    return {
        "upload_url": presigned_url,
        "storage_key": storage_key,
        "expires_in_seconds": 300,
    }
```

### Step 2 — Client uploads directly to storage

The client receives the presigned URL and PUTs the file directly to object storage. The application server is not involved.

### Step 3 — Confirm the upload

```python
# services/commands/files/confirm_upload.py

def confirm_upload(ctx: ServiceContext) -> dict:
    request = parse_confirm_upload_request(ctx.incoming_data)

    with db.session.begin():
        pending = (
            db.session.query(PendingUpload)
            .filter(
                PendingUpload.storage_key == request.storage_key,
                PendingUpload.workspace_id == ctx.workspace_id,
                PendingUpload.status == "pending",
            )
            .first()
        )
        if pending is None:
            raise NotFound("Upload not found or already confirmed.")

        # Verify the object actually exists in storage
        client = get_storage_client()
        metadata = client.head_object(request.storage_key)
        if metadata is None:
            raise ValidationError("File was not uploaded successfully. Please try again.")

        pending.status = "confirmed"
        pending.size_bytes = metadata["content_length"]

        # Attach to the domain record if an entity ID was provided
        if request.record_client_id:
            record = (
                db.session.query(Record)
                .filter(
                    Record.client_id == request.record_client_id,
                    Record.workspace_id == ctx.workspace_id,
                )
                .first()
            )
            if record is None:
                raise NotFound(f"Record {request.record_client_id} not found.")

            attachment = RecordAttachment(
                record_id=record.id,
                workspace_id=ctx.workspace_id,
                storage_key=request.storage_key,
                file_name=pending.file_name,
                content_type=pending.content_type,
                size_bytes=metadata["content_length"],
            )
            db.session.add(attachment)

    return {"status": "confirmed", "storage_key": request.storage_key}
```

---

## Storage key naming convention

Storage keys must be structured, workspace-scoped, and collision-resistant:

```
{env}/{workspace_id}/{domain}/{uuid4}{extension}
```

Examples:
```
production/7/records/3f1a2b4c-5d6e-7f8a-9b0c-1d2e3f4a5b6c.pdf
production/7/exports/a1b2c3d4-e5f6-7890-abcd-ef1234567890.csv
staging/42/imports/b2c3d4e5-f6a7-8901-bcde-f12345678901.xlsx
```

**Rules:**
- Always prefix with the environment name (`production`, `staging`, `development`). This prevents staging operations from touching production files.
- Always include `workspace_id`. This enables per-workspace access control policies in the storage bucket.
- Always use a UUID4 as the filename. Never use the original filename from the user — it can contain path traversal characters, Unicode, or collide with other files.
- Preserve the original extension for content negotiation but do not trust it for MIME type detection (see validation below).

```python
def _build_storage_key(workspace_id: int, file_name: str) -> str:
    import os, uuid
    env = current_app.config.get("ENV", "development")
    ext = os.path.splitext(file_name)[1].lower()[:10]   # cap extension length
    return f"{env}/{workspace_id}/uploads/{uuid.uuid4()}{ext}"
```

---

## File validation

### What to validate at upload URL generation

| Check | Why |
|---|---|
| `content_type` is in the allowed MIME list | Reject before upload begins |
| `file_name` extension matches allowed list | Belt-and-suspenders check |
| File name length < 255 chars | Prevents storage key issues |

### What to validate at confirmation

| Check | Why |
|---|---|
| Object exists in storage | Confirm the upload actually completed |
| `content_length` is within the allowed max | Enforce size limits server-side |
| MIME type from storage metadata matches declared type | Prevent extension spoofing |

### Allowed file types per use case

Define allowed MIME types per use case — do not use a global allow-list:

```python
# services/commands/files/generate_upload_url.py

ALLOWED_MIME_TYPES = {
    "record_attachment": [
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "text/plain",
    ],
    "import": [
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    ],
}

MAX_FILE_SIZE_BYTES = {
    "record_attachment": 10 * 1024 * 1024,    # 10 MB
    "import": 50 * 1024 * 1024,               # 50 MB
}
```

---

## PendingUpload model

Track every requested upload so orphaned files can be detected and cleaned up:

```python
# models/tables/files/pending_upload.py
class PendingUpload(db.Model):
    __tablename__ = "pending_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # status values: pending | confirmed | expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

---

## Orphan cleanup

A presigned URL was generated but the client never uploaded or never called confirm. The object may or may not exist in storage. The `PendingUpload` row stays in `pending` status past `expires_at`.

A scheduled CLI job cleans these up:

```bash
flask cleanup-expired-uploads --dry-run   # show what would be deleted
flask cleanup-expired-uploads             # delete from storage + mark as expired
```

```python
# cli/cleanup_uploads.py

@app.cli.command("cleanup-expired-uploads")
@click.option("--dry-run", is_flag=True)
def cleanup_expired_uploads(dry_run: bool):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = (
        db.session.query(PendingUpload)
        .filter(PendingUpload.status == "pending", PendingUpload.expires_at < cutoff)
        .all()
    )
    for upload in expired:
        if not dry_run:
            get_storage_client().delete_object(upload.storage_key)
            upload.status = "expired"
        click.echo(f"{'[dry-run] ' if dry_run else ''}expired: {upload.storage_key}")

    if not dry_run:
        db.session.commit()
```

Run this job daily via a cron task. See [27_cli_scripts.md](27_cli_scripts.md) for CLI conventions.

---

## Download URLs

Never expose raw storage URLs to the client. Generate short-lived presigned GET URLs on demand:

```python
# services/queries/files/get_attachment_url.py

def get_attachment_url(ctx: ServiceContext) -> dict:
    request = parse_get_attachment_url_request(ctx.query_params)

    attachment = (
        db.session.query(RecordAttachment)
        .filter(
            RecordAttachment.client_id == request.attachment_client_id,
            RecordAttachment.workspace_id == ctx.workspace_id,
        )
        .first()
    )
    if attachment is None:
        raise NotFound(f"Attachment {request.attachment_client_id} not found.")

    url = get_storage_client().generate_presigned_get_url(
        key=attachment.storage_key,
        expires_in=900,   # 15 minutes
    )
    return {"url": url, "expires_in_seconds": 900}
```

**Rules:**
- Presigned GET URLs expire in 15 minutes by default. Adjust for export files (longer) or sensitive documents (shorter).
- Never store presigned URLs in the database — they expire and become stale.
- Never return the raw storage key in API responses — it reveals the storage structure.

---

## File deletion

When a domain record is soft-deleted, its attachments are NOT deleted from storage immediately. Storage deletion is deferred to the hard-delete lifecycle (if implemented) or a scheduled cleanup job.

When a domain record is hard-deleted:

```python
# services/commands/files/delete_record_attachments.py

def delete_record_attachments(record_id: int, workspace_id: int) -> None:
    attachments = (
        db.session.query(RecordAttachment)
        .filter(
            RecordAttachment.record_id == record_id,
            RecordAttachment.workspace_id == workspace_id,
        )
        .all()
    )
    client = get_storage_client()
    for attachment in attachments:
        client.delete_object(attachment.storage_key)
    # The RecordAttachment rows are deleted by the parent record's cascade
```

---

## Storage client adapter

Abstract the storage provider behind an interface so the underlying provider can change without touching command or query code:

```python
# services/infra/storage/base.py
from abc import ABC, abstractmethod


class StorageClient(ABC):

    @abstractmethod
    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str: ...

    @abstractmethod
    def generate_presigned_get_url(self, key: str, expires_in: int) -> str: ...

    @abstractmethod
    def head_object(self, key: str) -> dict | None: ...

    @abstractmethod
    def delete_object(self, key: str) -> None: ...
```

Implementations: `services/infra/storage/s3_client.py`, `services/infra/storage/gcs_client.py`, `services/infra/storage/local_client.py` (development only — stores to disk).

The factory reads from config:

```python
# services/infra/storage/__init__.py

def get_storage_client() -> StorageClient:
    provider = current_app.config.get("STORAGE_PROVIDER", "local")
    if provider == "s3":
        return S3Client(
            bucket=current_app.config["STORAGE_BUCKET"],
            region=current_app.config["STORAGE_REGION"],
        )
    if provider == "gcs":
        return GCSClient(bucket=current_app.config["STORAGE_BUCKET"])
    return LocalStorageClient(base_path=current_app.config["LOCAL_STORAGE_PATH"])
```

---

## Security rules

- **Never serve user-uploaded files from the same origin as the API.** Use a separate domain (e.g., `assets.myapp.com`) or a CDN. This prevents cookies from being sent with file requests and eliminates stored XSS via MIME sniffing.
- **Set `Content-Disposition: attachment` on presigned GET URLs for non-image files.** This forces download rather than inline rendering in the browser.
- **Never trust the `Content-Type` declared by the client.** Always verify from the object metadata returned by storage after the upload.
- **Bucket policy must deny public access.** All access goes through presigned URLs generated by the application. No object is publicly readable by URL alone.
