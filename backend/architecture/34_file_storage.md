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

## Domain enums — `domain/files/enums.py`

```python
# domain/files/enums.py
import enum


class PendingUploadStatusEnum(enum.Enum):
    PENDING   = "pending"
    CONFIRMED = "confirmed"
    EXPIRED   = "expired"
```

---

## Presigned URL flow

### Step 1 — Request an upload URL

```python
# routers/api_v1/files.py
@router.post("/upload-url")
async def request_upload_url(
    body: GenerateUploadUrlBody,
    claims: dict = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(incoming_data=body.model_dump(), identity=claims, session=session)
    return run_service(generate_upload_url, ctx)
```

```python
# services/commands/files/generate_upload_url.py
import os
import uuid
from datetime import datetime, timezone, timedelta

from my_app.models import db
from my_app.models.tables.files.pending_upload import PendingUpload
from my_app.domain.files.enums import PendingUploadStatusEnum
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

    with db.session.begin():
        upload = PendingUpload(
            workspace_id=ctx.workspace_id,
            created_by_id=ctx.user_id,
            storage_key=storage_key,
            file_name=request.file_name,
            content_type=request.content_type,
            status=PendingUploadStatusEnum.PENDING,
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
from my_app.domain.files.enums import PendingUploadStatusEnum
from my_app.services.infra.storage import get_storage_client


def confirm_upload(ctx: ServiceContext) -> dict:
    request = parse_confirm_upload_request(ctx.incoming_data)

    with db.session.begin():
        pending = (
            db.session.query(PendingUpload)
            .filter(
                PendingUpload.storage_key == request.storage_key,
                PendingUpload.workspace_id == ctx.workspace_id,
                PendingUpload.status == PendingUploadStatusEnum.PENDING,
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

        pending.status = PendingUploadStatusEnum.CONFIRMED
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
                record_id=record.client_id,
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
from my_app.config import settings


def _build_storage_key(workspace_id: str, file_name: str) -> str:
    env = settings.environment
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
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, BigInteger, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from my_app.models.base.identity import IdentityMixin
from my_app.models.base import db
from my_app.domain.files.enums import PendingUploadStatusEnum


class PendingUpload(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "pu"
    __tablename__    = "pending_uploads"

    workspace_id:  Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True)
    created_by_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id"), nullable=False)
    storage_key:   Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    file_name:     Mapped[str] = mapped_column(String(255), nullable=False)
    content_type:  Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PendingUploadStatusEnum] = mapped_column(
        SAEnum(PendingUploadStatusEnum, name="pending_upload_status_enum", create_type=True),
        nullable=False,
        default=PendingUploadStatusEnum.PENDING,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

`IdentityMixin` provides `client_id` (primary key, ULID, prefixed `pu`). `status` is a typed enum column — never a raw string. `size_bytes` is `None` until the upload is confirmed.

---

## Orphan cleanup

A presigned URL was generated but the client never uploaded or never called confirm. The object may or may not exist in storage. The `PendingUpload` row stays in `PENDING` status past `expires_at`.

A scheduled CLI job cleans these up:

```bash
python scripts/backfill/cleanup_expired_uploads.py --dry-run   # show what would be deleted
python scripts/backfill/cleanup_expired_uploads.py             # delete from storage + mark as expired
```

```python
# scripts/backfill/cleanup_expired_uploads.py
import asyncio
from datetime import datetime, timezone, timedelta
import typer
from sqlalchemy import select
from my_app.domain.files.enums import PendingUploadStatusEnum
from my_app.models.database import _AsyncSessionLocal
from my_app.models.tables.files.pending_upload import PendingUpload
from my_app.services.infra.storage import get_storage_client

app = typer.Typer()


@app.command()
def main(dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    asyncio.run(_run(dry_run=dry_run))


async def _run(dry_run: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    async with _AsyncSessionLocal() as session:
        result = await session.execute(
            select(PendingUpload).where(
                PendingUpload.status == PendingUploadStatusEnum.PENDING,
                PendingUpload.expires_at < cutoff,
            )
        )
        expired = result.scalars().all()
        for upload in expired:
            if not dry_run:
                get_storage_client().delete_object(upload.storage_key)
                upload.status = PendingUploadStatusEnum.EXPIRED
            typer.echo(f"{'[dry-run] ' if dry_run else ''}expired: {upload.storage_key}")

        if not dry_run:
            await session.commit()
```

Run this job daily via a cron task. See [27_cli_scripts.md](27_cli_scripts.md) for CLI conventions.

---

## Download URLs

Never expose raw storage URLs to the client. Generate short-lived presigned GET URLs on demand:

```python
# services/queries/files/get_attachment_url.py
from my_app.services.infra.storage import get_storage_client


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
from my_app.services.infra.storage import get_storage_client


def delete_record_attachments(record_id: str, workspace_id: str) -> None:
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

## Storage orchestrator

The storage layer is a pure infrastructure concern. Commands, queries, and handlers never know which provider is in use — they call `get_storage_client()` and work against the interface. Swapping providers is a config change; zero application code changes.

### Isolation rule

`boto3` and any cloud SDK import is confined to `services/infra/storage/`. No file outside that package may import a provider library directly. This is enforced by convention — a linter rule can be added if the team grows.

### File structure

```
services/infra/storage/
├── __init__.py        ← get_storage_client() — the only import allowed outside this package
├── base.py            ← StorageClient ABC
├── s3_client.py       ← AWS S3 via boto3 (production default)
└── local_client.py    ← local filesystem (development only)
```

Adding a new provider = one new file extending `StorageClient` + one branch in `get_storage_client()`.

---

### `base.py`

```python
# services/infra/storage/base.py
from abc import ABC, abstractmethod


class StorageClient(ABC):

    @abstractmethod
    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str: ...

    @abstractmethod
    def generate_presigned_get_url(self, key: str, expires_in: int) -> str: ...

    @abstractmethod
    def head_object(self, key: str) -> dict | None:
        """Returns {"content_length": int, "content_type": str} or None if the object does not exist."""

    @abstractmethod
    def delete_object(self, key: str) -> None: ...
```

---

### `s3_client.py` — AWS S3 (production default)

```python
# services/infra/storage/s3_client.py
import boto3
from botocore.exceptions import ClientError

from my_app.services.infra.storage.base import StorageClient


class S3Client(StorageClient):

    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint_url: str | None = None,  # override for LocalStack / MinIO
    ):
        self._bucket = bucket
        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._client = session.client("s3", endpoint_url=endpoint_url)

    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      self._bucket,
                "Key":         key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def generate_presigned_get_url(self, key: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def head_object(self, key: str) -> dict | None:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
            return {
                "content_length": resp["ContentLength"],
                "content_type":   resp["ContentType"],
                "last_modified":  resp["LastModified"],
            }
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None
            raise

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
```

For production S3, always derive and pass `endpoint_url` explicitly as `https://s3.{region}.amazonaws.com`. Omitting it causes boto3 to sign against the global endpoint (`s3.amazonaws.com`), which then issues a 307 redirect to the regional endpoint — pre-signed PUT requests fail on redirect because the signature is bound to the original URL and the request body is not forwarded. Pass `endpoint_url` to point the same client at LocalStack or MinIO for local testing with full S3 fidelity.

---

### `local_client.py` — development only

```python
# services/infra/storage/local_client.py
# Development only — no cloud dependency.
# Presigned PUT/GET URLs point to a local FastAPI APIRouter endpoint.
# Register routers/dev/storage_bp in create_app() when ENV == 'development'.
from pathlib import Path

from my_app.services.infra.storage.base import StorageClient


class LocalStorageClient(StorageClient):

    def __init__(self, base_path: str, host: str = "http://localhost:5000"):
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._host = host

    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str:
        return f"{self._host}/dev/storage/put/{key}"

    def generate_presigned_get_url(self, key: str, expires_in: int) -> str:
        return f"{self._host}/dev/storage/get/{key}"

    def head_object(self, key: str) -> dict | None:
        path = self._base / key
        if not path.exists():
            return None
        return {"content_length": path.stat().st_size, "content_type": "application/octet-stream"}

    def delete_object(self, key: str) -> None:
        path = self._base / key
        if path.exists():
            path.unlink()
```

---

### `__init__.py` — factory

```python
# services/infra/storage/__init__.py
from my_app.config import settings

from my_app.services.infra.storage.base import StorageClient
from my_app.services.infra.storage.s3_client import S3Client
from my_app.services.infra.storage.local_client import LocalStorageClient


def get_storage_client() -> StorageClient:
    provider = settings.storage_provider

    if provider == "s3":
        region = settings.storage_region or "us-east-1"
        return S3Client(
            bucket=settings.storage_bucket,
            region=region,
            access_key=settings.aws_access_key_id,
            secret_key=settings.aws_secret_access_key,
            endpoint_url=settings.storage_endpoint_url or f"https://s3.{region}.amazonaws.com",
        )

    # S3-compatible endpoint — works with LocalStack and MinIO out of the box
    if provider == "localstack":
        return S3Client(
            bucket=settings.storage_bucket,
            region=settings.storage_region or "us-east-1",
            endpoint_url=settings.storage_endpoint_url or "http://localhost:4566",
        )

    return LocalStorageClient(base_path=settings.local_storage_path)
```

---

### Configuration

Set `STORAGE_PROVIDER` and the matching keys in your environment:

**Production — AWS S3:**
```
STORAGE_PROVIDER=s3
STORAGE_BUCKET=my-app-files-prod
STORAGE_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

**Local dev with full S3 fidelity — LocalStack:**
```
STORAGE_PROVIDER=localstack
STORAGE_BUCKET=my-app-files
STORAGE_REGION=us-east-1
STORAGE_ENDPOINT_URL=http://localhost:4566
```

**Local dev — filesystem only:**
```
STORAGE_PROVIDER=local
LOCAL_STORAGE_PATH=/tmp/my-app-uploads
```

In `LocalStack` mode the S3 bucket must be created first:
```bash
aws --endpoint-url=http://localhost:4566 s3 mb s3://my-app-files
```

---

## Multipart upload (files > 5 MB)

S3's single `PUT` limit is 5 GB, but for files above 5 MB multipart upload gives better reliability (each part retries independently) and enables parallel part uploads from the client.

**Extend `StorageClient` ABC:**

```python
# services/infra/storage/base.py

@abstractmethod
def initiate_multipart_upload(self, key: str, content_type: str) -> str:
    """Returns the upload_id for the multipart session."""

@abstractmethod
def generate_part_presigned_url(
    self, key: str, upload_id: str, part_number: int, expires_in: int
) -> str:
    """Returns a presigned PUT URL for one part. part_number is 1-indexed."""

@abstractmethod
def complete_multipart_upload(
    self, key: str, upload_id: str, parts: list[dict]
) -> None:
    """parts: [{"PartNumber": 1, "ETag": "abc..."}, ...]"""

@abstractmethod
def abort_multipart_upload(self, key: str, upload_id: str) -> None:
    """Called when the client abandons the upload or a confirm timeout fires."""
```

**`S3Client` implementation:**

```python
# services/infra/storage/s3_client.py

def initiate_multipart_upload(self, key: str, content_type: str) -> str:
    resp = self._client.create_multipart_upload(
        Bucket=self._bucket, Key=key, ContentType=content_type
    )
    return resp["UploadId"]

def generate_part_presigned_url(
    self, key: str, upload_id: str, part_number: int, expires_in: int
) -> str:
    return self._client.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket":     self._bucket,
            "Key":        key,
            "UploadId":   upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=expires_in,
    )

def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
    self._client.complete_multipart_upload(
        Bucket=self._bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )

def abort_multipart_upload(self, key: str, upload_id: str) -> None:
    self._client.abort_multipart_upload(
        Bucket=self._bucket, Key=key, UploadId=upload_id
    )
```

**Flow:**

```
1. Client: POST /files/multipart/initiate  → { upload_id, storage_key, part_urls: [...] }
2. Client: PUT each part URL directly to S3  → collects ETag per part
3. Client: POST /files/multipart/complete   → { storage_key, upload_id, parts: [{PartNumber, ETag}] }
4. API: calls complete_multipart_upload + marks PendingUpload as CONFIRMED
```

**Rules:**
- Minimum part size is 5 MB (S3 requirement) except for the last part. Validate `content_length` of each part on `complete` before calling S3.
- Store `upload_id` in `PendingUpload` alongside `storage_key` — required to `abort_multipart_upload` during orphan cleanup.
- Orphan cleanup (see above) must call `abort_multipart_upload` for `PENDING` rows that have a non-null `upload_id` and are past `expires_at`. Unaborted multipart uploads incur S3 storage charges for each uploaded part.
- Use multipart for files > 5 MB. Use single `PUT` for files ≤ 5 MB — multipart adds round-trips with no benefit.

---

## Security rules

- **Never serve user-uploaded files from the same origin as the API.** Use a separate domain (e.g., `assets.myapp.com`) or a CDN. This prevents cookies from being sent with file requests and eliminates stored XSS via MIME sniffing.
- **Set `Content-Disposition: attachment` on presigned GET URLs for non-image files.** This forces download rather than inline rendering in the browser.
- **Never trust the `Content-Type` declared by the client.** Always verify from the object metadata returned by storage after the upload.
- **Bucket policy must deny public access.** All access goes through presigned URLs generated by the application. No object is publicly readable by URL alone.
- **`boto3` and cloud SDK imports are confined to `services/infra/storage/`.** Commands, queries, and handlers import only `get_storage_client()`. Provider details never leak into application logic.
