from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _write_file_storage_foundation(root: Path, a: str, force: bool) -> None:
    _write(root / a / "domain" / "files" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "files" / "enums.py", """\
from enum import StrEnum


class PendingUploadStatusEnum(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
""", force=force)

    _touch(root / a / "models" / "tables" / "files" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "files" / "pending_upload.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class PendingUpload(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "pu"
    __tablename__ = "pending_uploads"

    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id", deferrable=True), nullable=False, index=True)
    created_by_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PendingUploadStatusEnum] = mapped_column(
        SAEnum(PendingUploadStatusEnum, name="pending_upload_status_enum", create_type=True),
        nullable=False,
        default=PendingUploadStatusEnum.PENDING,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    upload_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    workspace: Mapped["Workspace"] = relationship("Workspace", foreign_keys=[workspace_id])
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
""", force=force)

    _write(root / a / "services" / "infra" / "storage" / "base.py", """\
from abc import ABC, abstractmethod


class StorageClient(ABC):
    @abstractmethod
    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str: ...

    @abstractmethod
    def generate_presigned_get_url(self, key: str, expires_in: int) -> str: ...

    @abstractmethod
    def head_object(self, key: str) -> dict | None:
        \"\"\"Return object metadata or None when the object does not exist.\"\"\"

    @abstractmethod
    def delete_object(self, key: str) -> None: ...

    @abstractmethod
    def initiate_multipart_upload(self, key: str, content_type: str) -> str:
        \"\"\"Returns upload_id.\"\"\"

    @abstractmethod
    def generate_part_presigned_url(self, key: str, upload_id: str, part_number: int, expires_in: int) -> str:
        \"\"\"Returns presigned PUT URL for one part. part_number is 1-indexed.\"\"\"

    @abstractmethod
    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        \"\"\"parts: [{\"PartNumber\": 1, \"ETag\": \"...\"}]\"\"\"

    @abstractmethod
    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        \"\"\"Called on timeout or orphan cleanup.\"\"\"
""", force=force)
    _write(root / a / "services" / "infra" / "storage" / "local_client.py", f"""\
from pathlib import Path

from {a}.services.infra.storage.base import StorageClient


class LocalStorageClient(StorageClient):
    def __init__(self, base_path: str, host: str = "http://localhost:5000"):
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._host = host.rstrip("/")

    def _path(self, key: str) -> Path:
        return self._base / key

    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int) -> str:
        return f"{{self._host}}/dev/storage/put/{{key}}"

    def generate_presigned_get_url(self, key: str, expires_in: int) -> str:
        return f"{{self._host}}/dev/storage/get/{{key}}"

    def head_object(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        return {{"content_length": path.stat().st_size, "content_type": None}}

    def delete_object(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def initiate_multipart_upload(self, key: str, content_type: str) -> str:
        return f"local-upload-{{key}}"

    def generate_part_presigned_url(self, key: str, upload_id: str, part_number: int, expires_in: int) -> str:
        return f"{{self._host}}/dev/storage/multipart/{{key}}/part/{{part_number}}"

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        pass  # no-op for local dev

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        pass  # no-op for local dev
""", force=force)
    _write(root / a / "services" / "infra" / "storage" / "s3_client.py", f"""\
from {a}.services.infra.storage.base import StorageClient


class S3Client(StorageClient):
    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint_url: str | None = None,
    ):
        import boto3

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
            Params={{"Bucket": self._bucket, "Key": key, "ContentType": content_type}},
            ExpiresIn=expires_in,
        )

    def generate_presigned_get_url(self, key: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={{"Bucket": self._bucket, "Key": key}},
            ExpiresIn=expires_in,
        )

    def head_object(self, key: str) -> dict | None:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None
            raise
        return {{
            "content_length": resp["ContentLength"],
            "content_type": resp.get("ContentType"),
            "last_modified": resp.get("LastModified"),
        }}

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def initiate_multipart_upload(self, key: str, content_type: str) -> str:
        resp = self._client.create_multipart_upload(
            Bucket=self._bucket, Key=key, ContentType=content_type
        )
        return resp["UploadId"]

    def generate_part_presigned_url(self, key: str, upload_id: str, part_number: int, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "upload_part",
            Params={{
                "Bucket": self._bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            }},
            ExpiresIn=expires_in,
        )

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={{"Parts": parts}},
        )

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(
            Bucket=self._bucket, Key=key, UploadId=upload_id
        )
""", force=force)
    _write(root / a / "services" / "infra" / "storage" / "__init__.py", f"""\
from {a}.config import settings
from {a}.services.infra.storage.base import StorageClient
from {a}.services.infra.storage.local_client import LocalStorageClient
from {a}.services.infra.storage.s3_client import S3Client


def get_storage_client() -> StorageClient:
    provider = settings.storage_provider
    if provider == "s3":
        if not settings.storage_bucket:
            raise RuntimeError("STORAGE_BUCKET must be set when STORAGE_PROVIDER=s3")
        region = settings.storage_region or "us-east-1"
        return S3Client(
            bucket=settings.storage_bucket,
            region=region,
            access_key=settings.aws_access_key_id,
            secret_key=settings.aws_secret_access_key,
            endpoint_url=settings.storage_endpoint_url or f"https://s3.{{region}}.amazonaws.com",
        )
    if provider == "localstack":
        if not settings.storage_bucket:
            raise RuntimeError("STORAGE_BUCKET must be set when STORAGE_PROVIDER=localstack")
        return S3Client(
            bucket=settings.storage_bucket,
            region=settings.storage_region or "us-east-1",
            endpoint_url=settings.storage_endpoint_url or "http://localhost:4566",
        )
    return LocalStorageClient(base_path=settings.local_storage_path, host=settings.local_storage_host)
""", force=force)

    _touch(root / a / "services" / "commands" / "files" / "__init__.py", force=force)
    _touch(root / a / "services" / "queries" / "files" / "__init__.py", force=force)
    _write(root / a / "services" / "commands" / "files" / "generate_upload_url.py", f"""\
import os
import uuid
from datetime import datetime, timedelta, timezone

from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.errors.validation import ValidationError
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client

ALLOWED_MIME_TYPES = {{
    "record_attachment": ["image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"],
    "case_attachment":   ["image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"],
    "import": ["text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
}}
MAX_FILE_SIZE_BYTES = {{
    "record_attachment": 10 * 1024 * 1024,
    "case_attachment":   10 * 1024 * 1024,
    "import":            50 * 1024 * 1024,
}}
_PRESIGN_TTL = 300


def _build_storage_key(environment: str, workspace_id: str, use_case: str, file_name: str) -> str:
    ext = os.path.splitext(file_name)[1].lower()[:10]
    return f"{{environment}}/{{workspace_id}}/{{use_case}}/{{uuid.uuid4()}}{{ext}}"


async def generate_upload_url(ctx: ServiceContext) -> dict:
    from {a}.config import settings

    data = ctx.incoming_data or {{}}
    use_case = data.get("use_case", "record_attachment")
    file_name = data.get("file_name", "")
    content_type = data.get("content_type", "")
    size_bytes = data.get("file_size_bytes")

    if content_type not in ALLOWED_MIME_TYPES.get(use_case, []):
        raise ValidationError(f"content_type '{{content_type}}' is not allowed for {{use_case}}")
    if len(file_name) > 255:
        raise ValidationError("file_name must be 255 characters or fewer")
    if size_bytes and size_bytes > MAX_FILE_SIZE_BYTES.get(use_case, 10 * 1024 * 1024):
        raise ValidationError("file exceeds maximum allowed size")

    storage_key = _build_storage_key(settings.environment, ctx.workspace_id, use_case, file_name)
    upload_url = get_storage_client().generate_presigned_put_url(storage_key, content_type, _PRESIGN_TTL)
    upload = PendingUpload(
        workspace_id=ctx.workspace_id,
        created_by_id=ctx.user_id,
        storage_key=storage_key,
        file_name=file_name,
        content_type=content_type,
        status=PendingUploadStatusEnum.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        size_bytes=size_bytes,
    )
    ctx.session.add(upload)
    await ctx.session.commit()
    return {{"upload_url": upload_url, "pending_upload_client_id": upload.client_id, "storage_key": storage_key, "expires_in_seconds": _PRESIGN_TTL}}
""", force=force)
    _write(root / a / "services" / "commands" / "files" / "confirm_upload.py", f"""\
from sqlalchemy import select

from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client


async def confirm_upload(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    storage_key = data.get("storage_key")
    upload = (await ctx.session.execute(
        select(PendingUpload).where(
            PendingUpload.storage_key == storage_key,
            PendingUpload.workspace_id == ctx.workspace_id,
            PendingUpload.status == PendingUploadStatusEnum.PENDING,
        )
    )).scalar_one_or_none()
    if upload is None:
        raise NotFound("Upload not found or already confirmed.")

    metadata = get_storage_client().head_object(upload.storage_key)
    if metadata is None:
        raise ValidationError("File was not uploaded successfully. Please try again.")
    if metadata.get("content_type") and metadata["content_type"] != upload.content_type:
        raise ValidationError("Uploaded file content type does not match the requested content type.")

    upload.status = PendingUploadStatusEnum.CONFIRMED
    upload.size_bytes = metadata["content_length"]
    await ctx.session.commit()
    return {{"status": "confirmed", "storage_key": upload.storage_key, "size_bytes": upload.size_bytes}}
""", force=force)
    _write(root / a / "services" / "queries" / "files" / "get_pending_upload_download_url.py", f"""\
from {a}.errors.not_found import NotFound
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client

_GET_TTL = 900


async def get_pending_upload_download_url(ctx: ServiceContext) -> dict:
    upload = await ctx.session.get(PendingUpload, (ctx.incoming_data or {{}}).get("pending_upload_client_id"))
    if upload is None or upload.workspace_id != ctx.workspace_id:
        raise NotFound("PendingUpload not found")
    return {{"download_url": get_storage_client().generate_presigned_get_url(upload.storage_key, _GET_TTL), "expires_in_seconds": _GET_TTL}}
""", force=force)
    _write(root / a / "routers" / "api_v1" / "files.py", f"""\
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.commands.files.confirm_upload import confirm_upload
from {a}.services.commands.files.generate_upload_url import generate_upload_url
from {a}.services.context import ServiceContext
from {a}.services.queries.files.get_pending_upload_download_url import get_pending_upload_download_url
from {a}.services.run_service import run_service

router = APIRouter()


class GenerateUploadUrlBody(BaseModel):
    file_name: str
    content_type: str
    use_case: str = "record_attachment"
    file_size_bytes: int | None = None


class ConfirmUploadBody(BaseModel):
    storage_key: str


class PendingUploadDownloadBody(BaseModel):
    pending_upload_client_id: str


async def _run(command, data: dict, claims: dict, session: AsyncSession):
    outcome = await run_service(command, ServiceContext(identity=claims, incoming_data=data, session=session))
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


@router.post("/upload-url")
async def request_upload_url(body: GenerateUploadUrlBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(generate_upload_url, body.model_dump(), claims, session)


@router.post("/confirm-upload")
async def confirm_upload_route(body: ConfirmUploadBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(confirm_upload, body.model_dump(), claims, session)


@router.post("/download-url")
async def pending_upload_download_url(body: PendingUploadDownloadBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(get_pending_upload_download_url, body.model_dump(), claims, session)
""", force=force)
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import audit, auth, health, notifications\n",
        f"from {a}.routers.api_v1 import audit, auth, files, health, notifications\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import audit, auth, health\n",
        f"from {a}.routers.api_v1 import audit, auth, files, health\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n',
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n'
        '    app.include_router(files.router, prefix="/api/v1/files", tags=["files"])\n',
    )


def _write_content_foundation(root: Path, a: str, force: bool) -> None:
    _write(root / a / "domain" / "content" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "content" / "enums.py", """\
from enum import StrEnum


class InputContentTypeEnum(StrEnum):
    TEXT = "text"
    MENTION = "mention"
    LABEL = "label"
    LINK = "link"


class ContentMentionLinkEntityTypeEnum(StrEnum):
    CASE_CONVERSATION_MESSAGE = "case_conversation_message"
    TASK_DETAILS_MENTION = "task_details_mention"
    TASK_NOTE_MENTION = "task_note_mention"
""", force=force)
    _write(root / a / "domain" / "content" / "schemas.py", """\
from dataclasses import dataclass


@dataclass
class InputContentBlock:
    type: str
    text: str
    mention: dict | None = None
    label_value: str | None = None
    link: str | None = None
""", force=force)

    _touch(root / a / "models" / "tables" / "content" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "content" / "content_mention.py", f"""\
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ContentMention(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "cmt"
    __tablename__ = "content_mentions"
    __table_args__ = (
        UniqueConstraint("mention_table", "mention_id", name="uq_content_mention"),
    )

    mention_table: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    mention_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    links: Mapped[list["ContentMentionLink"]] = relationship(
        "ContentMentionLink",
        foreign_keys="[ContentMentionLink.content_mention_id]",
        back_populates="content_mention",
    )
""", force=force)
    _write(root / a / "models" / "tables" / "content" / "content_mention_link.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ContentMentionLink(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "cml"
    __tablename__ = "content_mention_links"

    content_mention_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("content_mentions.client_id", deferrable=True), nullable=False, index=True
    )
    entity_type: Mapped[ContentMentionLinkEntityTypeEnum] = mapped_column(
        SAEnum(ContentMentionLinkEntityTypeEnum, name="content_mention_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )
    entity_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)

    content_mention: Mapped["ContentMention"] = relationship("ContentMention", foreign_keys=[content_mention_id], back_populates="links")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[ContentMentionLink.created_by_id]")
""", force=force)

    _write(root / a / "services" / "infra" / "content.py", f"""\
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum, InputContentTypeEnum
from {a}.domain.content.schemas import InputContentBlock
from {a}.errors.validation import ValidationError
from {a}.models.tables.content.content_mention import ContentMention
from {a}.models.tables.content.content_mention_link import ContentMentionLink


def validate_content_block(block: dict) -> InputContentBlock:
    if not isinstance(block, dict):
        raise ValidationError("Each content block must be a dict")

    block_type = block.get("type")
    if not block_type:
        raise ValidationError("Content block missing 'type'")

    try:
        type_enum = InputContentTypeEnum(block_type)
    except ValueError as exc:
        raise ValidationError(f"Invalid content block type: {{block_type!r}}") from exc

    text = block.get("text")
    if text is None:
        raise ValidationError("Content block missing 'text'")

    mention = None
    if type_enum == InputContentTypeEnum.MENTION:
        mention = block.get("mention")
        if not isinstance(mention, dict):
            raise ValidationError("MENTION block requires a 'mention' object")
        for key in ("mention_table", "mention_id", "client_id"):
            if key not in mention:
                raise ValidationError(f"MENTION block missing '{{key}}' in mention dict")

    label_value = None
    if type_enum == InputContentTypeEnum.LABEL:
        label_value = block.get("label_value")
        if label_value is None:
            raise ValidationError("LABEL block missing 'label_value'")

    link = None
    if type_enum == InputContentTypeEnum.LINK:
        link = block.get("link")
        if link is None:
            raise ValidationError("LINK block missing 'link'")

    return InputContentBlock(
        type=type_enum.value,
        text=text,
        mention=mention,
        label_value=label_value,
        link=link,
    )


def validate_content(content) -> list[InputContentBlock]:
    if not isinstance(content, list):
        raise ValidationError("content must be a list of blocks")
    return [validate_content_block(block) for block in content]


async def process_content_mentions(
    session: AsyncSession,
    content: list,
    entity_type: ContentMentionLinkEntityTypeEnum,
    entity_client_id: str,
    created_by_id: str,
    replace: bool = False,
) -> None:
    if replace:
        await session.execute(
            delete(ContentMentionLink).where(
                ContentMentionLink.entity_type == entity_type,
                ContentMentionLink.entity_client_id == entity_client_id,
            )
        )
        await session.flush()

    for block in content or []:
        if block.get("type") != InputContentTypeEnum.MENTION.value:
            continue
        mention_data = block.get("mention") or {{}}
        mention_table = mention_data.get("mention_table")
        mention_client_id = mention_data.get("client_id")
        if not mention_table or not mention_client_id:
            continue

        result = await session.execute(
            select(ContentMention).where(
                ContentMention.mention_table == mention_table,
                ContentMention.mention_id == mention_client_id,
            )
        )
        mention = result.scalar_one_or_none()
        if mention is None:
            mention = ContentMention(mention_table=mention_table, mention_id=mention_client_id)
            session.add(mention)
            await session.flush()

        existing = await session.execute(
            select(ContentMentionLink).where(
                ContentMentionLink.content_mention_id == mention.client_id,
                ContentMentionLink.entity_type == entity_type,
                ContentMentionLink.entity_client_id == entity_client_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(ContentMentionLink(
                content_mention_id=mention.client_id,
                entity_type=entity_type,
                entity_client_id=entity_client_id,
                created_by_id=created_by_id,
            ))
""", force=force)


def _write_case_image_services(root: Path, a: str, force: bool) -> None:
    _write(root / a / "services" / "commands" / "images" / "generate_upload_url.py", f"""\
import os
import uuid
from datetime import datetime, timedelta, timezone

from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.errors.validation import ValidationError
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client

_ALLOWED_CONTENT_TYPES = {{"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}}
_MAX_SIZE_BYTES = 20 * 1024 * 1024
_PRESIGN_TTL = 900


def _build_storage_key(workspace_id: str, entity_type: str, entity_client_id: str, file_name: str) -> str:
    ext = os.path.splitext(file_name)[1].lower()[:10]
    return f"images/{{workspace_id}}/{{entity_type}}/{{entity_client_id}}/{{uuid.uuid4()}}{{ext}}"


async def generate_upload_url(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    entity_type = data.get("entity_type")
    entity_client_id = data.get("entity_client_id")
    file_name = data.get("file_name", "")
    content_type = data.get("content_type", "")
    size_bytes = data.get("file_size_bytes")

    if not entity_type or not entity_client_id:
        raise ValidationError("entity_type and entity_client_id are required")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValidationError(f"content_type '{{content_type}}' is not allowed")
    if size_bytes and size_bytes > _MAX_SIZE_BYTES:
        raise ValidationError("file exceeds maximum allowed size")

    storage_key = _build_storage_key(ctx.workspace_id, entity_type, entity_client_id, file_name)
    upload_url = get_storage_client().generate_presigned_put_url(storage_key, content_type, _PRESIGN_TTL)
    upload = PendingUpload(
        workspace_id=ctx.workspace_id,
        created_by_id=ctx.user_id,
        storage_key=storage_key,
        file_name=file_name,
        content_type=content_type,
        status=PendingUploadStatusEnum.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=_PRESIGN_TTL),
        size_bytes=size_bytes,
    )
    ctx.session.add(upload)
    await ctx.session.flush()
    await ctx.session.commit()
    return {{"upload_url": upload_url, "pending_upload_client_id": upload.client_id, "storage_key": storage_key, "expires_in": _PRESIGN_TTL}}
""", force=force)
    _write(root / a / "services" / "commands" / "images" / "confirm_upload.py", f"""\
from sqlalchemy import func, select

from {a}.config import settings
from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.domain.images.enums import ImageEventTypeEnum, ImageLinkEntityTypeEnum, ImageSourceReferenceEnum, ImageSourceTypeEnum, ImageStorageProviderEnum
from {a}.domain.images.serializers import serialize_image
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.models.tables.images.image import Image
from {a}.models.tables.images.image_event import ImageEvent
from {a}.models.tables.images.image_link import ImageLink
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client

_ENTITY_EVENT_MAP = {{
    ImageLinkEntityTypeEnum.ITEM: ImageEventTypeEnum.UPLOAD_ITEM_IMAGE,
    ImageLinkEntityTypeEnum.CASE: ImageEventTypeEnum.UPLOAD_CASE_IMAGE,
    ImageLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE: ImageEventTypeEnum.UPLOAD_MESSAGE_IMAGE,
}}


async def confirm_upload(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    pending_upload_client_id = data.get("pending_upload_client_id")
    entity_type = ImageLinkEntityTypeEnum(data.get("entity_type"))
    entity_client_id = data.get("entity_client_id")

    upload = await ctx.session.get(PendingUpload, pending_upload_client_id)
    if upload is None:
        raise NotFound("PendingUpload not found")
    if upload.status != PendingUploadStatusEnum.PENDING:
        raise ValidationError("upload already confirmed or expired")

    metadata = get_storage_client().head_object(upload.storage_key)
    if not metadata:
        raise ValidationError("file has not been uploaded yet")

    try:
        provider = ImageStorageProviderEnum(settings.storage_provider)
    except ValueError:
        provider = ImageStorageProviderEnum.S3
    source_ref = ImageSourceReferenceEnum.S3_IMAGE_URL if provider == ImageStorageProviderEnum.S3 else None
    image = Image(
        image_url=upload.storage_key,
        storage_provider=provider,
        source_type=ImageSourceTypeEnum.UPLOADED,
        source_reference=source_ref,
        file_size_bytes=metadata["content_length"],
        created_by_id=ctx.user_id,
    )
    ctx.session.add(image)
    await ctx.session.flush()

    next_order = (await ctx.session.execute(
        select(func.count(ImageLink.client_id)).where(
            ImageLink.entity_type == entity_type,
            ImageLink.entity_client_id == entity_client_id,
        )
    )).scalar_one()
    ctx.session.add(ImageLink(image_id=image.client_id, entity_type=entity_type, entity_client_id=entity_client_id, display_order=next_order))

    event = ImageEvent(image_id=image.client_id, type=_ENTITY_EVENT_MAP[entity_type], created_by_id=ctx.user_id)
    ctx.session.add(event)
    await ctx.session.flush()
    image.last_event_id = event.client_id
    upload.status = PendingUploadStatusEnum.CONFIRMED
    upload.size_bytes = metadata["content_length"]
    await ctx.session.commit()
    return {{"image": serialize_image(image)}}
""", force=force)
    _write(root / a / "services" / "commands" / "images" / "soft_delete_image.py", f"""\
from datetime import datetime, timezone

from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.images.image import Image
from {a}.services.context import ServiceContext


async def soft_delete_image(ctx: ServiceContext) -> dict:
    image = await ctx.session.get(Image, (ctx.incoming_data or {{}}).get("image_client_id"))
    if image is None:
        raise NotFound("Image not found")
    if image.deleted_at is not None:
        raise ValidationError("image is already deleted")
    image.deleted_at = datetime.now(timezone.utc)
    image.deleted_by_id = ctx.user_id
    await ctx.session.commit()
    return {{"client_id": image.client_id}}
""", force=force)
    _write(root / a / "services" / "commands" / "images" / "unlink_image.py", f"""\
from sqlalchemy import select

from {a}.domain.images.enums import ImageLinkEntityTypeEnum
from {a}.errors.not_found import NotFound
from {a}.models.tables.images.image import Image
from {a}.models.tables.images.image_link import ImageLink
from {a}.services.context import ServiceContext


async def unlink_image(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    image = await ctx.session.get(Image, data.get("image_client_id"))
    if image is None:
        raise NotFound("Image not found")
    entity_type = ImageLinkEntityTypeEnum(data.get("entity_type"))
    link = (await ctx.session.execute(select(ImageLink).where(
        ImageLink.image_id == image.client_id,
        ImageLink.entity_type == entity_type,
        ImageLink.entity_client_id == data.get("entity_client_id"),
    ))).scalar_one_or_none()
    if link is None:
        raise NotFound("ImageLink not found")
    await ctx.session.delete(link)
    await ctx.session.commit()
    return {{"unlinked": True}}
""", force=force)
    _write(root / a / "services" / "commands" / "images" / "create_annotation.py", f"""\
from {a}.domain.images.enums import ImageAnnotationTypeEnum
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.images.image import Image
from {a}.models.tables.images.image_annotation import ImageAnnotation
from {a}.services.context import ServiceContext

_REQUIRED_KEYS = {{
    ImageAnnotationTypeEnum.DRAW: {{"points", "color"}},
    ImageAnnotationTypeEnum.ARROW: {{"from", "to"}},
    ImageAnnotationTypeEnum.CIRCLE: {{"cx", "cy", "r"}},
    ImageAnnotationTypeEnum.RECTANGLE: {{"x", "y", "w", "h"}},
    ImageAnnotationTypeEnum.TEXT: {{"x", "y", "text"}},
    ImageAnnotationTypeEnum.MEASUREMENT: {{"from", "to", "unit", "value"}},
    ImageAnnotationTypeEnum.HIGHLIGHT: {{"x", "y", "w", "h"}},
}}


async def create_annotation(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    ann_type = ImageAnnotationTypeEnum(data.get("annotation_type"))
    payload = data.get("data") or {{}}
    accuracy = data.get("accuracy")
    if accuracy is not None and not 0 <= accuracy <= 100:
        raise ValidationError("accuracy must be 0-100")
    missing = _REQUIRED_KEYS.get(ann_type, set()) - payload.keys()
    if missing:
        raise ValidationError(f"missing required keys for {{ann_type.value}}: {{sorted(missing)}}")
    image = await ctx.session.get(Image, data.get("image_client_id"))
    if image is None or image.deleted_at is not None:
        raise NotFound("Image not found")
    annotation = ImageAnnotation(image_id=image.client_id, annotation_type=ann_type, data=payload, accuracy=accuracy, created_by_id=ctx.user_id)
    ctx.session.add(annotation)
    await ctx.session.commit()
    return {{"client_id": annotation.client_id}}
""", force=force)
    _write(root / a / "services" / "commands" / "images" / "reorder_links.py", f"""\
from sqlalchemy import select

from {a}.domain.images.enums import ImageLinkEntityTypeEnum
from {a}.errors.validation import ValidationError
from {a}.models.tables.images.image import Image
from {a}.models.tables.images.image_link import ImageLink
from {a}.services.context import ServiceContext


async def reorder_links(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    entity_type = ImageLinkEntityTypeEnum(data.get("entity_type"))
    entity_client_id = data.get("entity_client_id")
    ordered_client_ids = data.get("ordered_image_client_ids", [])
    images = {{img.client_id: img for img in (await ctx.session.execute(select(Image).where(Image.client_id.in_(ordered_client_ids)))).scalars().all()}}
    links = {{link.image_id: link for link in (await ctx.session.execute(select(ImageLink).where(ImageLink.entity_type == entity_type, ImageLink.entity_client_id == entity_client_id))).scalars().all()}}
    for position, client_id in enumerate(ordered_client_ids):
        if client_id not in images:
            raise ValidationError(f"image '{{client_id}}' not found")
        link = links.get(client_id)
        if link is None:
            raise ValidationError(f"image '{{client_id}}' is not linked to this entity")
        link.display_order = position
    await ctx.session.commit()
    return {{"reordered": len(ordered_client_ids)}}
""", force=force)

    _write(root / a / "services" / "queries" / "images" / "get_download_url.py", f"""\
from {a}.errors.not_found import NotFound
from {a}.models.tables.images.image import Image
from {a}.services.context import ServiceContext
from {a}.services.infra.storage import get_storage_client

_GET_TTL = 3600


async def get_download_url(ctx: ServiceContext) -> dict:
    image = await ctx.session.get(Image, (ctx.incoming_data or {{}}).get("image_client_id"))
    if image is None or image.deleted_at is not None:
        raise NotFound("Image not found")
    return {{"download_url": get_storage_client().generate_presigned_get_url(image.image_url, _GET_TTL), "expires_in": _GET_TTL}}
""", force=force)
    _write(root / a / "services" / "queries" / "images" / "get_image.py", f"""\
from sqlalchemy.orm import selectinload

from {a}.domain.images.serializers import serialize_image
from {a}.errors.not_found import NotFound
from {a}.models.tables.images.image import Image
from {a}.services.context import ServiceContext


async def get_image(ctx: ServiceContext) -> dict:
    image = await ctx.session.get(
        Image,
        (ctx.incoming_data or {{}}).get("image_client_id"),
        options=[selectinload(Image.last_event), selectinload(Image.events), selectinload(Image.image_annotations)],
    )
    if image is None or image.deleted_at is not None:
        raise NotFound("Image not found")
    return {{"image": serialize_image(image, include_events=True, include_annotations=True)}}
""", force=force)
    _write(root / a / "services" / "queries" / "images" / "list_images_for_entity.py", f"""\
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from {a}.domain.images.enums import ImageLinkEntityTypeEnum
from {a}.domain.images.serializers import serialize_image_link
from {a}.models.tables.images.image import Image
from {a}.models.tables.images.image_link import ImageLink
from {a}.services.context import ServiceContext


async def list_images_for_entity(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    entity_type = ImageLinkEntityTypeEnum(data.get("entity_type"))
    rows = (await ctx.session.execute(
        select(ImageLink)
        .join(Image, Image.client_id == ImageLink.image_id)
        .options(selectinload(ImageLink.image).selectinload(Image.last_event))
        .where(ImageLink.entity_type == entity_type, ImageLink.entity_client_id == data.get("entity_client_id"), Image.deleted_at.is_(None))
        .order_by(ImageLink.display_order)
    )).scalars().all()
    return {{"images": [serialize_image_link(link) for link in rows]}}
""", force=force)

    _write(root / a / "services" / "commands" / "cases" / "create_case.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.serializers import serialize_case
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_type import CaseType
from {a}.services.context import ServiceContext


async def create_case(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case_type_id = data.get("case_type_id")
    type_label = data.get("type_label")
    if case_type_id:
        case_type = await ctx.session.get(CaseType, case_type_id)
        if case_type and type_label is None:
            type_label = case_type.name
    case = Case(created_by_id=ctx.user_id, updated_by_id=ctx.user_id, state=CaseStateEnum.OPEN, case_type_id=case_type_id, type_label=type_label)
    ctx.session.add(case)
    await ctx.session.commit()
    return {{"case": serialize_case(case)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "update_case.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.serializers import serialize_case
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_type import CaseType
from {a}.services.context import ServiceContext


async def update_case(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    if "case_type_id" not in data and "type_label" not in data:
        raise ValidationError("case_type_id or type_label is required")
    if "case_type_id" in data:
        case.case_type_id = data.get("case_type_id")
        if case.case_type_id and "type_label" not in data:
            case_type = await ctx.session.get(CaseType, case.case_type_id)
            case.type_label = case_type.name if case_type else case.type_label
    if "type_label" in data:
        case.type_label = data.get("type_label")
    case.updated_by_id = ctx.user_id
    case.updated_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    return {{"case": serialize_case(case)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "update_case_state.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.serializers import serialize_case
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.services.context import ServiceContext


async def update_case_state(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    case.state = CaseStateEnum(data.get("new_state"))
    case.updated_by_id = ctx.user_id
    case.updated_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    return {{"case": serialize_case(case)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "link_entity.py", f"""\
from {a}.domain.cases.enums import CaseLinkEntityTypeEnum, CaseLinkRoleEnum
from {a}.domain.cases.serializers import serialize_case_link
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_link import CaseLink
from {a}.services.context import ServiceContext


async def link_entity(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    link = CaseLink(
        case_id=case.client_id,
        entity_type=CaseLinkEntityTypeEnum(data.get("entity_type")),
        entity_client_id=data.get("entity_client_id"),
        role=CaseLinkRoleEnum(data.get("role")),
    )
    ctx.session.add(link)
    await ctx.session.commit()
    return {{"link": serialize_case_link(link)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "unlink_entity.py", f"""\
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case_link import CaseLink
from {a}.services.context import ServiceContext


async def unlink_entity(ctx: ServiceContext) -> dict:
    link = await ctx.session.get(CaseLink, (ctx.incoming_data or {{}}).get("case_link_client_id"))
    if link is None:
        raise NotFound("CaseLink not found")
    await ctx.session.delete(link)
    await ctx.session.commit()
    return {{"deleted": True}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "add_participant.py", f"""\
from sqlalchemy import select, update

from {a}.domain.cases.serializers import serialize_participant
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext


async def add_participant(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    user_ids = set(data.get("user_ids") or [])
    existing = set((await ctx.session.execute(select(CaseParticipant.user_id).where(CaseParticipant.case_id == case.client_id, CaseParticipant.user_id.in_(user_ids)))).scalars().all())
    added = [CaseParticipant(case_id=case.client_id, user_id=user_id) for user_id in user_ids - existing]
    ctx.session.add_all(added)
    if added:
        await ctx.session.execute(update(Case).where(Case.client_id == case.client_id).values(participants_count=Case.participants_count + len(added)))
    await ctx.session.commit()
    return {{"added": [serialize_participant(participant) for participant in added]}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "remove_participant.py", f"""\
from sqlalchemy import func, update

from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext


async def remove_participant(ctx: ServiceContext) -> dict:
    participant = await ctx.session.get(CaseParticipant, (ctx.incoming_data or {{}}).get("case_participant_client_id"))
    if participant is None:
        raise NotFound("CaseParticipant not found")
    case_id = participant.case_id
    await ctx.session.delete(participant)
    await ctx.session.execute(update(Case).where(Case.client_id == case_id).values(participants_count=func.greatest(Case.participants_count - 1, 0)))
    await ctx.session.commit()
    return {{"deleted": True}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "create_conversation.py", f"""\
from sqlalchemy import update

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.serializers import serialize_conversation
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.services.context import ServiceContext


async def create_conversation(ctx: ServiceContext) -> dict:
    case = await ctx.session.get(Case, (ctx.incoming_data or {{}}).get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    conversation = CaseConversation(case_id=case.client_id, created_by_id=ctx.user_id, state=CaseStateEnum.OPEN)
    ctx.session.add(conversation)
    await ctx.session.execute(update(Case).where(Case.client_id == case.client_id).values(conversations_count=Case.conversations_count + 1))
    await ctx.session.commit()
    return {{"conversation": serialize_conversation(conversation)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "send_message.py", f"""\
from sqlalchemy import select, update

from {a}.domain.cases.serializers import serialize_message
from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext
from {a}.services.infra.content import process_content_mentions, validate_content


async def _next_message_seq(ctx: ServiceContext, conversation_id: str) -> int:
    result = await ctx.session.execute(
        update(CaseConversation)
        .where(CaseConversation.client_id == conversation_id)
        .values(last_message_seq=CaseConversation.last_message_seq + 1)
        .returning(CaseConversation.last_message_seq)
    )
    return result.scalar_one()


async def send_message(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    conversation = await ctx.session.get(CaseConversation, data.get("conversation_client_id"))
    if conversation is None:
        raise NotFound("Conversation not found")
    blocks = validate_content(data.get("content"))
    content = [block.__dict__ for block in blocks]
    seq = await _next_message_seq(ctx, conversation.client_id)
    message = CaseConversationMessage(
        case_conversation_id=conversation.client_id,
        message_seq=seq,
        created_by_id=ctx.user_id,
        content=content,
        plain_text=data.get("plain_text", ""),
    )
    ctx.session.add(message)
    await ctx.session.flush()
    await process_content_mentions(ctx.session, content, ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE, message.client_id, ctx.user_id)
    await ctx.session.execute(update(CaseConversation).where(CaseConversation.client_id == conversation.client_id).values(messages_count=CaseConversation.messages_count + 1))
    await ctx.session.execute(update(Case).where(Case.client_id == conversation.case_id).values(messages_count=Case.messages_count + 1))
    await ctx.session.commit()
    return {{"message": serialize_message(message)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "edit_message.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.serializers import serialize_message
from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext
from {a}.services.infra.content import process_content_mentions, validate_content


async def edit_message(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    message = await ctx.session.get(CaseConversationMessage, data.get("message_client_id"))
    if message is None:
        raise NotFound("Message not found")
    if message.has_been_deleted:
        raise ValidationError("deleted messages cannot be edited")
    blocks = validate_content(data.get("content"))
    content = [block.__dict__ for block in blocks]
    message.content = content
    message.plain_text = data.get("plain_text", "")
    message.has_been_edited = True
    message.updated_at = datetime.now(timezone.utc)
    await process_content_mentions(ctx.session, content, ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE, message.client_id, ctx.user_id, replace=True)
    await ctx.session.commit()
    return {{"message": serialize_message(message)}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "soft_delete_message.py", f"""\
from sqlalchemy import func, select, update

from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext


async def soft_delete_message(ctx: ServiceContext) -> dict:
    message = await ctx.session.get(CaseConversationMessage, (ctx.incoming_data or {{}}).get("message_client_id"))
    if message is None:
        raise NotFound("Message not found")
    if not message.has_been_deleted:
        message.has_been_deleted = True
        conversation = await ctx.session.get(CaseConversation, message.case_conversation_id)
        await ctx.session.execute(update(CaseConversation).where(CaseConversation.client_id == message.case_conversation_id).values(messages_count=func.greatest(CaseConversation.messages_count - 1, 0)))
        if conversation:
            await ctx.session.execute(update(Case).where(Case.client_id == conversation.case_id).values(messages_count=func.greatest(Case.messages_count - 1, 0)))
    await ctx.session.commit()
    return {{"deleted": True}}
""", force=force)
    _write(root / a / "services" / "commands" / "cases" / "mark_read.py", f"""\
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext


async def mark_read(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    participant = await ctx.session.get(CaseParticipant, data.get("case_participant_client_id"))
    if participant is None:
        raise NotFound("CaseParticipant not found")
    participant.last_read_message_seq = max(participant.last_read_message_seq, int(data.get("up_to_message_seq", 0)))
    await ctx.session.commit()
    return {{"last_read_message_seq": participant.last_read_message_seq}}
""", force=force)

    _write(root / a / "services" / "queries" / "cases" / "get_case.py", f"""\
from {a}.domain.cases.serializers import serialize_case
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.services.context import ServiceContext


async def get_case(ctx: ServiceContext) -> dict:
    case = await ctx.session.get(Case, (ctx.incoming_data or {{}}).get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    return {{"case": serialize_case(case)}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "list_cases.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.enums import CaseLinkEntityTypeEnum, CaseStateEnum
from {a}.domain.cases.serializers import serialize_case
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_link import CaseLink
from {a}.services.context import ServiceContext


async def list_cases(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    stmt = select(Case)
    if data.get("state"):
        stmt = stmt.where(Case.state == CaseStateEnum(data["state"]))
    if data.get("created_by_id"):
        stmt = stmt.where(Case.created_by_id == data["created_by_id"])
    if data.get("entity_type") and data.get("entity_client_id"):
        stmt = stmt.join(CaseLink, CaseLink.case_id == Case.client_id).where(
            CaseLink.entity_type == CaseLinkEntityTypeEnum(data["entity_type"]),
            CaseLink.entity_client_id == data["entity_client_id"],
        )
    stmt = stmt.offset(int(data.get("offset", 0))).limit(int(data.get("limit", 50)))
    cases = (await ctx.session.execute(stmt)).scalars().all()
    return {{"cases": [serialize_case(case) for case in cases]}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "get_conversation.py", f"""\
from {a}.domain.cases.serializers import serialize_conversation
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.services.context import ServiceContext


async def get_conversation(ctx: ServiceContext) -> dict:
    conversation = await ctx.session.get(CaseConversation, (ctx.incoming_data or {{}}).get("conversation_client_id"))
    if conversation is None:
        raise NotFound("Conversation not found")
    return {{"conversation": serialize_conversation(conversation)}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "list_messages.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.serializers import serialize_message
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext


async def list_messages(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    stmt = select(CaseConversationMessage).where(CaseConversationMessage.case_conversation_id == data.get("conversation_client_id"))
    if data.get("before_seq") is not None:
        stmt = stmt.where(CaseConversationMessage.message_seq < int(data["before_seq"]))
    stmt = stmt.order_by(CaseConversationMessage.message_seq.desc()).limit(int(data.get("limit", 50)))
    messages = (await ctx.session.execute(stmt)).scalars().all()
    return {{"messages": [serialize_message(message) for message in messages]}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "list_participants.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.serializers import serialize_participant
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext


async def list_participants(ctx: ServiceContext) -> dict:
    participants = (await ctx.session.execute(select(CaseParticipant).where(CaseParticipant.case_id == (ctx.incoming_data or {{}}).get("case_client_id")))).scalars().all()
    return {{"participants": [serialize_participant(participant) for participant in participants]}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "list_linked_entities.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.enums import CaseLinkEntityTypeEnum, CaseLinkRoleEnum
from {a}.domain.cases.serializers import serialize_case_link
from {a}.models.tables.cases.case_link import CaseLink
from {a}.services.context import ServiceContext


async def list_linked_entities(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    stmt = select(CaseLink).where(CaseLink.case_id == data.get("case_client_id"))
    if data.get("entity_type"):
        stmt = stmt.where(CaseLink.entity_type == CaseLinkEntityTypeEnum(data["entity_type"]))
    if data.get("role"):
        stmt = stmt.where(CaseLink.role == CaseLinkRoleEnum(data["role"]))
    links = (await ctx.session.execute(stmt)).scalars().all()
    return {{"links": [serialize_case_link(link) for link in links]}}
""", force=force)
    _write(root / a / "services" / "queries" / "cases" / "get_unread_counts.py", f"""\
from sqlalchemy import select

from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext


async def get_unread_counts(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    user_id = data.get("user_id") or ctx.user_id
    stmt = select(CaseConversation.client_id, CaseConversation.last_message_seq, CaseParticipant.last_read_message_seq).join(
        CaseParticipant, CaseParticipant.case_id == CaseConversation.case_id
    ).where(CaseParticipant.user_id == user_id)
    if data.get("conversation_client_ids"):
        stmt = stmt.where(CaseConversation.client_id.in_(data["conversation_client_ids"]))
    rows = (await ctx.session.execute(stmt)).all()
    counts = {{conversation_id: max(last_seq - read_seq, 0) for conversation_id, last_seq, read_seq in rows}}
    if not data.get("conversation_client_ids"):
        counts = {{key: value for key, value in counts.items() if value > 0}}
    return {{"unread_counts": counts}}
""", force=force)


def _phase10_foundation_records(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 10 - Case & Image Foundation Records -----------------------")

    _write_file_storage_foundation(root, a, force)
    _write_content_foundation(root, a, force)

    _write(root / a / "domain" / "cases" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "cases" / "enums.py", """\
from enum import StrEnum


class CaseLinkEntityTypeEnum(StrEnum):
    TASK = "task"
    CUSTOMER = "customer"


class CaseLinkRoleEnum(StrEnum):
    ORIGIN = "origin"
    SUBJECT = "subject"
    CONTEXT = "context"
    ACTOR = "actor"
    RESOLUTION = "resolution"


class CaseStateEnum(StrEnum):
    OPEN = "open"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
""", force=force)
    _write(root / a / "domain" / "cases" / "events.py", f"""\
from enum import StrEnum

from {a}.domain.cases.enums import CaseStateEnum


class CaseEvent(StrEnum):
    CREATED = "case:created"
    UPDATED = "case:updated"
    DELETED = "case:deleted"
    STATE_CHANGED = "case:state-changed"
    PARTICIPANT_ADDED = "case:participant-added"
    PARTICIPANT_REMOVED = "case:participant-removed"
    CONVERSATION_CREATED = "case:conversation-created"


class ConversationMessageEvent(StrEnum):
    CREATED = "conversation:message-created"
    EDITED = "conversation:message-edited"
    DELETED = "conversation:message-deleted"


def case_state_extra(new_state: CaseStateEnum) -> dict:
    return {{"new_state": new_state.value}}


def conversation_message_extra(message_seq: int) -> dict:
    return {{"message_seq": message_seq}}
""", force=force)
    _write(root / a / "domain" / "cases" / "results.py", """\
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseResult:
    client_id: str
    state: str
    type_label: str | None
    participants_count: int
    conversations_count: int
    messages_count: int
    created_at: str
    created_by_id: str


@dataclass
class CaseLinkResult:
    client_id: str
    entity_type: str
    entity_client_id: str
    role: str
    created_at: str


@dataclass
class CaseParticipantResult:
    client_id: str
    user_id: str
    last_read_message_seq: int
    joined_at: str


@dataclass
class CaseConversationResult:
    client_id: str
    state: str
    messages_count: int
    last_message_seq: int
    created_at: str
    last_messages: list = field(default_factory=list)


@dataclass
class CaseConversationMessageResult:
    client_id: str
    message_seq: int
    content: list[Any] | dict[str, Any] | None
    plain_text: str
    has_been_edited: bool
    has_been_deleted: bool
    created_at: str
    created_by: dict | None = None
""", force=force)
    _write(root / a / "domain" / "cases" / "serializers.py", """\
def _value(value):
    return value.value if hasattr(value, "value") else value


def serialize_case(case) -> dict:
    return {
        "client_id": case.client_id,
        "state": _value(case.state),
        "type_label": case.type_label,
        "participants_count": case.participants_count,
        "conversations_count": case.conversations_count,
        "messages_count": case.messages_count,
        "created_at": case.created_at.isoformat(),
        "created_by_id": case.created_by_id,
    }


def serialize_case_link(link) -> dict:
    return {
        "client_id": link.client_id,
        "entity_type": _value(link.entity_type),
        "entity_client_id": link.entity_client_id,
        "role": _value(link.role),
        "created_at": link.created_at.isoformat(),
    }


def serialize_participant(participant) -> dict:
    return {
        "client_id": participant.client_id,
        "user_id": participant.user_id,
        "last_read_message_seq": participant.last_read_message_seq,
        "joined_at": participant.joined_at.isoformat(),
    }


def serialize_conversation(conversation, *, last_messages: list | None = None) -> dict:
    return {
        "client_id": conversation.client_id,
        "state": _value(conversation.state),
        "messages_count": conversation.messages_count,
        "last_message_seq": conversation.last_message_seq,
        "created_at": conversation.created_at.isoformat(),
        "last_messages": last_messages or [],
    }


def serialize_message(message) -> dict:
    return {
        "client_id": message.client_id,
        "message_seq": message.message_seq,
        "content": None if message.has_been_deleted else message.content,
        "plain_text": "" if message.has_been_deleted else message.plain_text,
        "has_been_edited": message.has_been_edited,
        "has_been_deleted": message.has_been_deleted,
        "created_at": message.created_at.isoformat(),
    }
""", force=force)

    _touch(root / a / "models" / "tables" / "cases" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case_type.py", f"""\
from sqlalchemy import String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.cases.enums import CaseLinkEntityTypeEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class CaseType(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "cty"
    __tablename__ = "case_types"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    entity_type: Mapped[CaseLinkEntityTypeEnum] = mapped_column(
        SAEnum(CaseLinkEntityTypeEnum, name="case_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )

    cases: Mapped[list["Case"]] = relationship("Case", foreign_keys="[Case.case_type_id]", back_populates="case_type")
""", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from {a}.domain.cases.enums import CaseStateEnum
from {a}.models.base.base import Base
from {a}.models.base.history_record import HistoryRecord
from {a}.models.base.identity import IdentityMixin


class Case(IdentityMixin, HistoryRecord, Base):
    CLIENT_ID_PREFIX = "ca"
    __tablename__ = "cases"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)

    state: Mapped[CaseStateEnum] = mapped_column(
        SAEnum(CaseStateEnum, name="case_state_enum", create_type=True),
        nullable=False,
        default=CaseStateEnum.OPEN,
        index=True,
    )
    case_type_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("case_types.client_id", deferrable=True), nullable=True, index=True)
    type_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    participants_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    case_type: Mapped["CaseType | None"] = relationship("CaseType", foreign_keys=[case_type_id], back_populates="cases")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[Case.created_by_id]")
    updated_by: Mapped["User"] = relationship("User", foreign_keys="[Case.updated_by_id]")
    participants: Mapped[list["CaseParticipant"]] = relationship("CaseParticipant", foreign_keys="[CaseParticipant.case_id]", back_populates="case")
    conversations: Mapped[list["CaseConversation"]] = relationship("CaseConversation", foreign_keys="[CaseConversation.case_id]", back_populates="case")
    links: Mapped[list["CaseLink"]] = relationship("CaseLink", foreign_keys="[CaseLink.case_id]", back_populates="case")
""", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case_link.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.cases.enums import CaseLinkEntityTypeEnum, CaseLinkRoleEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class CaseLink(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "clk"
    __tablename__ = "case_links"
    __table_args__ = (
        UniqueConstraint("case_id", "entity_type", "entity_client_id", name="uq_case_link_case_entity"),
    )

    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.client_id", deferrable=True), nullable=False, index=True)
    entity_type: Mapped[CaseLinkEntityTypeEnum] = mapped_column(
        SAEnum(CaseLinkEntityTypeEnum, name="case_link_entity_type_enum", create_type=False),
        nullable=False,
        index=True,
    )
    entity_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[CaseLinkRoleEnum] = mapped_column(
        SAEnum(CaseLinkRoleEnum, name="case_link_role_enum", create_type=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id], back_populates="links")
""", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case_participant.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class CaseParticipant(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "cpa"
    __tablename__ = "case_participants"
    __table_args__ = (
        UniqueConstraint("case_id", "user_id", name="uq_case_participant"),
    )

    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.client_id", deferrable=True), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)
    last_read_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id], back_populates="participants")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
""", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case_conversation.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from {a}.domain.cases.enums import CaseStateEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class CaseConversation(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ccv"
    __tablename__ = "case_conversations"

    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.client_id", deferrable=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)

    state: Mapped[CaseStateEnum] = mapped_column(
        SAEnum(CaseStateEnum, name="case_state_enum", create_type=False),
        nullable=False,
        default=CaseStateEnum.OPEN,
        index=True,
    )
    last_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id], back_populates="conversations")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[CaseConversation.created_by_id]")
    messages: Mapped[list["CaseConversationMessage"]] = relationship(
        "CaseConversationMessage",
        foreign_keys="[CaseConversationMessage.case_conversation_id]",
        back_populates="conversation",
        order_by="CaseConversationMessage.message_seq",
    )
""", force=force)
    _write(root / a / "models" / "tables" / "cases" / "case_conversation_message.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class CaseConversationMessage(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ccm"
    __tablename__ = "case_conversation_messages"
    __table_args__ = (
        UniqueConstraint("case_conversation_id", "message_seq", name="uq_message_seq"),
    )

    case_conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("case_conversations.client_id", deferrable=True), nullable=False, index=True)
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)

    content: Mapped[list | dict] = mapped_column(JSONB, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    has_been_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_been_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    conversation: Mapped["CaseConversation"] = relationship("CaseConversation", foreign_keys=[case_conversation_id], back_populates="messages")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[CaseConversationMessage.created_by_id]")
""", force=force)

    _write(root / a / "domain" / "images" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "images" / "enums.py", """\
from enum import StrEnum


class ImageStorageProviderEnum(StrEnum):
    S3 = "s3"
    SHOPIFY = "shopify"
    EXTERNAL = "external"


class ImageSourceTypeEnum(StrEnum):
    UPLOADED = "uploaded"
    SHOPIFY_SYNC = "shopify_sync"
    GENERATED = "generated"


class ImageSourceReferenceEnum(StrEnum):
    S3_IMAGE_URL = "s3_image_url"
    SHOPIFY_IMAGE_URL = "shopify_image_url"


class ImageLinkEntityTypeEnum(StrEnum):
    ITEM = "item"
    CASE = "case"
    CASE_CONVERSATION_MESSAGE = "case_conversation_message"


class ImageAnnotationTypeEnum(StrEnum):
    DRAW = "draw"
    ARROW = "arrow"
    CIRCLE = "circle"
    RECTANGLE = "rectangle"
    TEXT = "text"
    MEASUREMENT = "measurement"
    HIGHLIGHT = "highlight"


class ImageEventTypeEnum(StrEnum):
    UPLOAD_ITEM_IMAGE = "upload_item_image"
    UPLOAD_CASE_IMAGE = "upload_case_image"
    UPLOAD_MESSAGE_IMAGE = "upload_message_image"


class ImageEventErrorEnum(StrEnum):
    UPLOAD_FAILED = "upload_failed"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    FILE_TOO_LARGE = "file_too_large"
    VIRUS_DETECTED = "virus_detected"
""", force=force)
    _write(root / a / "domain" / "images" / "results.py", """\
from dataclasses import dataclass, field


@dataclass
class UploadUrlResult:
    upload_url: str
    pending_upload_client_id: str
    storage_key: str
    expires_in: int


@dataclass
class ImageEventResult:
    client_id: str
    event_type: str
    state: str
    created_at: str
    created_by: dict | None = None
    last_error: str | None = None


@dataclass
class ImageAnnotationResult:
    client_id: str
    annotation_type: str
    data: dict | None = None
    accuracy: int | None = None
    created_at: str = ""
    created_by: dict | None = None


@dataclass
class ImageResult:
    client_id: str
    image_url: str
    storage_provider: str
    source_type: str
    source_reference: str | None
    width_px: int | None
    height_px: int | None
    file_size_bytes: int | None
    created_at: str
    created_by: dict | None = None
    last_event: ImageEventResult | None = None
    events: list = field(default_factory=list)
    image_annotation: ImageAnnotationResult | None = None


@dataclass
class ImageLinkResult:
    link_client_id: str
    image: ImageResult
    entity_type: str
    entity_client_id: str
    display_order: int


@dataclass
class DownloadUrlResult:
    download_url: str
    expires_in: int
""", force=force)
    _write(root / a / "domain" / "images" / "serializers.py", """\
def _value(value):
    return value.value if hasattr(value, "value") else value


def serialize_image_event(event) -> dict:
    return {
        "client_id": event.client_id,
        "event_type": _value(event.type),
        "state": _value(event.state),
        "created_at": event.created_at.isoformat(),
        "last_error": _value(event.last_error),
    }


def serialize_annotation(annotation) -> dict:
    return {
        "client_id": annotation.client_id,
        "annotation_type": _value(annotation.annotation_type),
        "data": annotation.data,
        "accuracy": annotation.accuracy,
        "created_at": annotation.created_at.isoformat(),
    }


def serialize_image(image, *, include_events: bool = False, include_annotations: bool = False) -> dict:
    events = [serialize_image_event(event) for event in getattr(image, "events", [])] if include_events else []
    annotations = getattr(image, "image_annotations", []) if include_annotations else []
    return {
        "client_id": image.client_id,
        "image_url": image.image_url,
        "storage_provider": _value(image.storage_provider),
        "source_type": _value(image.source_type),
        "source_reference": _value(image.source_reference),
        "width_px": image.width_px,
        "height_px": image.height_px,
        "file_size_bytes": image.file_size_bytes,
        "created_at": image.created_at.isoformat(),
        "last_event": serialize_image_event(image.last_event) if getattr(image, "last_event", None) else None,
        "events": events,
        "image_annotation": serialize_annotation(annotations[0]) if annotations else None,
    }


def serialize_image_link(link) -> dict:
    return {
        "link_client_id": link.client_id,
        "image": serialize_image(link.image),
        "entity_type": _value(link.entity_type),
        "entity_client_id": link.entity_client_id,
        "display_order": link.display_order,
    }
""", force=force)

    _touch(root / a / "models" / "tables" / "images" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "images" / "image.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.images.enums import ImageSourceReferenceEnum, ImageSourceTypeEnum, ImageStorageProviderEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class Image(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "img"
    __tablename__ = "images"

    image_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    storage_provider: Mapped[ImageStorageProviderEnum] = mapped_column(
        SAEnum(ImageStorageProviderEnum, name="image_storage_provider_enum", create_type=True),
        nullable=False,
        index=True,
    )
    source_type: Mapped[ImageSourceTypeEnum] = mapped_column(
        SAEnum(ImageSourceTypeEnum, name="image_source_type_enum", create_type=True),
        nullable=False,
    )
    source_reference: Mapped[ImageSourceReferenceEnum | None] = mapped_column(
        SAEnum(ImageSourceReferenceEnum, name="image_source_reference_enum", create_type=True),
        nullable=True,
    )
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)
    updated_by_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=True)
    deleted_by_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_event_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("image_events.client_id", use_alter=True, name="fk_image_last_event_id", deferrable=True),
        nullable=True,
    )

    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    updated_by: Mapped["User | None"] = relationship("User", foreign_keys=[updated_by_id])
    deleted_by: Mapped["User | None"] = relationship("User", foreign_keys=[deleted_by_id])
    image_links: Mapped[list["ImageLink"]] = relationship("ImageLink", back_populates="image")
    image_annotations: Mapped[list["ImageAnnotation"]] = relationship("ImageAnnotation", back_populates="image")
    events: Mapped[list["ImageEvent"]] = relationship("ImageEvent", foreign_keys="[ImageEvent.image_id]", back_populates="image")
    last_event: Mapped["ImageEvent | None"] = relationship("ImageEvent", foreign_keys=[last_event_id])
""", force=force)
    _write(root / a / "models" / "tables" / "images" / "image_link.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.images.enums import ImageLinkEntityTypeEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ImageLink(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "iml"
    __tablename__ = "image_links"
    __table_args__ = (
        UniqueConstraint("image_id", "entity_type", "entity_client_id", name="uq_image_link_image_entity"),
    )

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id", deferrable=True), nullable=False, index=True)
    entity_type: Mapped[ImageLinkEntityTypeEnum] = mapped_column(
        SAEnum(ImageLinkEntityTypeEnum, name="image_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )
    entity_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    image: Mapped["Image"] = relationship("Image", back_populates="image_links")
""", force=force)
    _write(root / a / "models" / "tables" / "images" / "image_annotation.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.images.enums import ImageAnnotationTypeEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ImageAnnotation(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ian"
    __tablename__ = "image_annotations"

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id", deferrable=True), nullable=False, index=True)
    annotation_type: Mapped[ImageAnnotationTypeEnum] = mapped_column(
        SAEnum(ImageAnnotationTypeEnum, name="image_annotation_type_enum", create_type=True),
        nullable=False,
    )
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    accuracy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    image: Mapped["Image"] = relationship("Image", back_populates="image_annotations")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
""", force=force)
    _write(root / a / "models" / "tables" / "images" / "image_event.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.images.enums import ImageEventErrorEnum, ImageEventTypeEnum
from {a}.models.base.base import Base
from {a}.models.base.event import Event
from {a}.models.base.identity import IdentityMixin


class ImageEvent(IdentityMixin, Event, Base):
    CLIENT_ID_PREFIX = "iev"
    EVENT_TYPE_ENUM = ImageEventTypeEnum
    EVENT_ERROR_ENUM = ImageEventErrorEnum
    __tablename__ = "image_events"

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id", deferrable=True), nullable=False, index=True)

    image: Mapped["Image"] = relationship("Image", foreign_keys=[image_id], back_populates="events")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[ImageEvent.created_by_id]")
""", force=force)

    _touch(root / a / "services" / "commands" / "cases" / "__init__.py", force=force)
    _touch(root / a / "services" / "queries" / "cases" / "__init__.py", force=force)
    _touch(root / a / "services" / "commands" / "images" / "__init__.py", force=force)
    _touch(root / a / "services" / "queries" / "images" / "__init__.py", force=force)
    _write_case_image_services(root, a, force)

    append_once(root / a / "models" / "__init__.py", (
        f"from {a}.models.tables.files import pending_upload  # noqa: F401\n"
        f"from {a}.models.tables.content import content_mention  # noqa: F401\n"
        f"from {a}.models.tables.content import content_mention_link  # noqa: F401\n"
        f"from {a}.models.tables.cases import case  # noqa: F401\n"
        f"from {a}.models.tables.cases import case_conversation  # noqa: F401\n"
        f"from {a}.models.tables.cases import case_conversation_message  # noqa: F401\n"
        f"from {a}.models.tables.cases import case_link  # noqa: F401\n"
        f"from {a}.models.tables.cases import case_participant  # noqa: F401\n"
        f"from {a}.models.tables.cases import case_type  # noqa: F401\n"
        f"from {a}.models.tables.images import image  # noqa: F401\n"
        f"from {a}.models.tables.images import image_annotation  # noqa: F401\n"
        f"from {a}.models.tables.images import image_event  # noqa: F401\n"
        f"from {a}.models.tables.images import image_link  # noqa: F401\n"
    ))

    _write_file_storage_extras(root, a, force)
    _write_case_image_routers(root, a, force)


def _write_file_storage_extras(root: Path, a: str, force: bool) -> None:
    """Gap 6: cleanup script + dev-only local storage router."""
    _write(root / "scripts" / "backfill" / "cleanup_expired_uploads.py", f"""\
\"\"\"
Cleanup script: mark expired PendingUpload rows and optionally delete their
storage objects.  Run manually or via a scheduled job.

Usage:
    python scripts/backfill/cleanup_expired_uploads.py [--delete-objects] [--dry-run]
\"\"\"
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update

from {a}.models.database import get_db_session
from {a}.models.tables.files.pending_upload import PendingUpload
from {a}.domain.files.enums import PendingUploadStatusEnum
from {a}.services.infra.storage import get_storage_client


async def run(delete_objects: bool, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    async with get_db_session() as session:
        stmt = select(PendingUpload).where(
            PendingUpload.status == PendingUploadStatusEnum.PENDING,
            PendingUpload.expires_at < now,
        )
        rows = (await session.execute(stmt)).scalars().all()
        print(f"Found {{len(rows)}} expired uploads")
        for upload in rows:
            if delete_objects:
                try:
                    get_storage_client().delete_object(upload.storage_key)
                    print(f"  deleted object: {{upload.storage_key}}")
                except Exception as exc:
                    print(f"  WARN delete failed {{upload.storage_key}}: {{exc}}")
            if not dry_run:
                upload.status = PendingUploadStatusEnum.EXPIRED
        if not dry_run:
            await session.commit()
            print(f"Marked {{len(rows)}} uploads as expired")
        else:
            print("[dry-run] no changes committed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-objects", action="store_true", help="also delete storage objects")
    parser.add_argument("--dry-run", action="store_true", help="scan only, no DB changes")
    args = parser.parse_args()
    asyncio.run(run(args.delete_objects, args.dry_run))
""", force=force)

    _write(root / a / "routers" / "dev" / "__init__.py", "", force=force)
    _write(root / a / "routers" / "dev" / "storage.py", f"""\
\"\"\"Dev-only local file storage endpoints.

Registered only when settings.environment == 'development'.  Simulates the
presigned PUT/GET URL flow without real object storage.
\"\"\"
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from {a}.config import settings

router = APIRouter()


def _resolve(key: str) -> Path:
    base = Path(settings.local_storage_path)
    path = (base / key).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid key")
    return path


@router.put("/dev/storage/put/{{key:path}}")
async def dev_storage_put(key: str, request: Request) -> Response:
    path = _resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = await request.body()
    path.write_bytes(body)
    return Response(status_code=200)


@router.get("/dev/storage/get/{{key:path}}")
async def dev_storage_get(key: str) -> FileResponse:
    path = _resolve(key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Object not found")
    return FileResponse(str(path))
""", force=force)

    replace_once(
        root / a / "__init__.py",
        "    _register_routers(app)\n",
        f"    _register_routers(app)\n"
        f"    if settings.storage_provider == 'local':\n"
        f"        from {a}.routers.dev.storage import router as _dev_storage_router\n"
        f"        app.include_router(_dev_storage_router)\n",
    )


def _write_case_image_routers(root: Path, a: str, force: bool) -> None:
    """Gap 8: case API router, image API router, event bus wiring in commands."""
    _write(root / a / "routers" / "api_v1" / "cases.py", f"""\
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.commands.cases.add_participant import add_participant
from {a}.services.commands.cases.create_case import create_case
from {a}.services.commands.cases.create_conversation import create_conversation
from {a}.services.commands.cases.edit_message import edit_message
from {a}.services.commands.cases.link_entity import link_entity
from {a}.services.commands.cases.mark_read import mark_read
from {a}.services.commands.cases.remove_participant import remove_participant
from {a}.services.commands.cases.send_message import send_message
from {a}.services.commands.cases.soft_delete_message import soft_delete_message
from {a}.services.commands.cases.unlink_entity import unlink_entity
from {a}.services.commands.cases.update_case import update_case
from {a}.services.commands.cases.update_case_state import update_case_state
from {a}.services.context import ServiceContext
from {a}.services.queries.cases.get_case import get_case
from {a}.services.queries.cases.get_conversation import get_conversation
from {a}.services.queries.cases.get_unread_counts import get_unread_counts
from {a}.services.queries.cases.list_cases import list_cases
from {a}.services.queries.cases.list_linked_entities import list_linked_entities
from {a}.services.queries.cases.list_messages import list_messages
from {a}.services.queries.cases.list_participants import list_participants
from {a}.services.run_service import run_service

router = APIRouter()


class CreateCaseBody(BaseModel):
    case_type_id: str | None = None
    type_label: str | None = None


class UpdateCaseBody(BaseModel):
    case_client_id: str
    case_type_id: str | None = None
    type_label: str | None = None


class UpdateCaseStateBody(BaseModel):
    case_client_id: str
    new_state: str


class LinkEntityBody(BaseModel):
    case_client_id: str
    entity_type: str
    entity_client_id: str
    role: str


class AddParticipantBody(BaseModel):
    case_client_id: str
    user_ids: list[str]


class CreateConversationBody(BaseModel):
    case_client_id: str


class SendMessageBody(BaseModel):
    conversation_client_id: str
    content: list
    plain_text: str = ""


class EditMessageBody(BaseModel):
    message_client_id: str
    content: list
    plain_text: str = ""


class MarkReadBody(BaseModel):
    case_participant_client_id: str
    up_to_message_seq: int


async def _run(command, data: dict, claims: dict, session: AsyncSession):
    outcome = await run_service(command, ServiceContext(identity=claims, incoming_data=data, session=session))
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


@router.post("")
async def create_case_route(body: CreateCaseBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(create_case, body.model_dump(), claims, session)


@router.get("")
async def list_cases_route(state: str | None = None, created_by_id: str | None = None, entity_type: str | None = None, entity_client_id: str | None = None, offset: int = 0, limit: int = 50, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(list_cases, {{"state": state, "created_by_id": created_by_id, "entity_type": entity_type, "entity_client_id": entity_client_id, "offset": offset, "limit": limit}}, claims, session)


@router.get("/unread-counts")
async def unread_counts_route(conversation_client_ids: str | None = None, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    ids = conversation_client_ids.split(",") if conversation_client_ids else None
    return await _run(get_unread_counts, {{"conversation_client_ids": ids}}, claims, session)


@router.get("/{{case_client_id}}")
async def get_case_route(case_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(get_case, {{"case_client_id": case_client_id}}, claims, session)


@router.patch("/{{case_client_id}}")
async def update_case_route(case_client_id: str, body: UpdateCaseBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(update_case, {{**body.model_dump(), "case_client_id": case_client_id}}, claims, session)


@router.patch("/{{case_client_id}}/state")
async def update_case_state_route(case_client_id: str, body: UpdateCaseStateBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(update_case_state, {{**body.model_dump(), "case_client_id": case_client_id}}, claims, session)


@router.post("/{{case_client_id}}/links")
async def link_entity_route(case_client_id: str, body: LinkEntityBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(link_entity, {{**body.model_dump(), "case_client_id": case_client_id}}, claims, session)


@router.delete("/links/{{case_link_client_id}}")
async def unlink_entity_route(case_link_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(unlink_entity, {{"case_link_client_id": case_link_client_id}}, claims, session)


@router.get("/{{case_client_id}}/links")
async def list_links_route(case_client_id: str, entity_type: str | None = None, role: str | None = None, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(list_linked_entities, {{"case_client_id": case_client_id, "entity_type": entity_type, "role": role}}, claims, session)


@router.post("/{{case_client_id}}/participants")
async def add_participant_route(case_client_id: str, body: AddParticipantBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(add_participant, {{**body.model_dump(), "case_client_id": case_client_id}}, claims, session)


@router.delete("/participants/{{case_participant_client_id}}")
async def remove_participant_route(case_participant_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(remove_participant, {{"case_participant_client_id": case_participant_client_id}}, claims, session)


@router.get("/{{case_client_id}}/participants")
async def list_participants_route(case_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(list_participants, {{"case_client_id": case_client_id}}, claims, session)


@router.post("/{{case_client_id}}/conversations")
async def create_conversation_route(case_client_id: str, body: CreateConversationBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(create_conversation, {{**body.model_dump(), "case_client_id": case_client_id}}, claims, session)


@router.get("/conversations/{{conversation_client_id}}")
async def get_conversation_route(conversation_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(get_conversation, {{"conversation_client_id": conversation_client_id}}, claims, session)


@router.post("/conversations/{{conversation_client_id}}/messages")
async def send_message_route(conversation_client_id: str, body: SendMessageBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(send_message, {{**body.model_dump(), "conversation_client_id": conversation_client_id}}, claims, session)


@router.get("/conversations/{{conversation_client_id}}/messages")
async def list_messages_route(conversation_client_id: str, before_seq: int | None = None, limit: int = 50, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(list_messages, {{"conversation_client_id": conversation_client_id, "before_seq": before_seq, "limit": limit}}, claims, session)


@router.patch("/messages/{{message_client_id}}")
async def edit_message_route(message_client_id: str, body: EditMessageBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(edit_message, {{**body.model_dump(), "message_client_id": message_client_id}}, claims, session)


@router.delete("/messages/{{message_client_id}}")
async def soft_delete_message_route(message_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(soft_delete_message, {{"message_client_id": message_client_id}}, claims, session)


@router.post("/messages/mark-read")
async def mark_read_route(body: MarkReadBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(mark_read, body.model_dump(), claims, session)
""", force=force)

    _write(root / a / "routers" / "api_v1" / "images.py", f"""\
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.commands.images.confirm_upload import confirm_upload
from {a}.services.commands.images.create_annotation import create_annotation
from {a}.services.commands.images.generate_upload_url import generate_upload_url
from {a}.services.commands.images.reorder_links import reorder_links
from {a}.services.commands.images.soft_delete_image import soft_delete_image
from {a}.services.commands.images.unlink_image import unlink_image
from {a}.services.context import ServiceContext
from {a}.services.queries.images.get_download_url import get_download_url
from {a}.services.queries.images.get_image import get_image
from {a}.services.queries.images.list_images_for_entity import list_images_for_entity
from {a}.services.run_service import run_service

router = APIRouter()


class GenerateImageUploadUrlBody(BaseModel):
    entity_type: str
    entity_client_id: str
    file_name: str
    content_type: str
    file_size_bytes: int | None = None


class ConfirmImageUploadBody(BaseModel):
    pending_upload_client_id: str
    entity_type: str
    entity_client_id: str


class UnlinkImageBody(BaseModel):
    image_client_id: str
    entity_type: str
    entity_client_id: str


class ReorderLinksBody(BaseModel):
    entity_type: str
    entity_client_id: str
    ordered_image_client_ids: list[str]


class CreateAnnotationBody(BaseModel):
    image_client_id: str
    annotation_type: str
    data: dict
    accuracy: int | None = None


async def _run(command, data: dict, claims: dict, session: AsyncSession):
    outcome = await run_service(command, ServiceContext(identity=claims, incoming_data=data, session=session))
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


@router.post("/upload-url")
async def image_upload_url_route(body: GenerateImageUploadUrlBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(generate_upload_url, body.model_dump(), claims, session)


@router.post("/confirm-upload")
async def image_confirm_upload_route(body: ConfirmImageUploadBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(confirm_upload, body.model_dump(), claims, session)


@router.get("")
async def list_images_route(entity_type: str, entity_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(list_images_for_entity, {{"entity_type": entity_type, "entity_client_id": entity_client_id}}, claims, session)


@router.delete("/links")
async def unlink_image_route(body: UnlinkImageBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(unlink_image, body.model_dump(), claims, session)


@router.post("/reorder")
async def reorder_links_route(body: ReorderLinksBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(reorder_links, body.model_dump(), claims, session)


@router.get("/{{image_client_id}}")
async def get_image_route(image_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(get_image, {{"image_client_id": image_client_id}}, claims, session)


@router.get("/{{image_client_id}}/download-url")
async def image_download_url_route(image_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(get_download_url, {{"image_client_id": image_client_id}}, claims, session)


@router.delete("/{{image_client_id}}")
async def soft_delete_image_route(image_client_id: str, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(soft_delete_image, {{"image_client_id": image_client_id}}, claims, session)


@router.post("/{{image_client_id}}/annotations")
async def create_annotation_route(image_client_id: str, body: CreateAnnotationBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(create_annotation, {{**body.model_dump(), "image_client_id": image_client_id}}, claims, session)
""", force=force)

    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import audit, auth, files, health, notifications\n",
        f"from {a}.routers.api_v1 import audit, auth, cases, files, health, images, notifications\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import audit, auth, files, health\n",
        f"from {a}.routers.api_v1 import audit, auth, cases, files, health, images\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        '    app.include_router(files.router, prefix="/api/v1/files", tags=["files"])\n',
        '    app.include_router(files.router, prefix="/api/v1/files", tags=["files"])\n'
        '    app.include_router(cases.router, prefix="/api/v1/cases", tags=["cases"])\n'
        '    app.include_router(images.router, prefix="/api/v1/images", tags=["images"])\n',
    )

    _write_case_event_wiring(root, a, force)


def _write_case_event_wiring(root: Path, a: str, force: bool) -> None:
    """Wire event_bus.dispatch into case commands per the contract event table."""
    _write(root / a / "services" / "commands" / "cases" / "create_case.py", f"""\
from sqlalchemy import select

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.events import CaseEvent
from {a}.domain.cases.serializers import serialize_case
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_type import CaseType
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def create_case(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case_type_id = data.get("case_type_id")
    type_label = data.get("type_label")
    if case_type_id:
        case_type = await ctx.session.get(CaseType, case_type_id)
        if case_type and type_label is None:
            type_label = case_type.name
    case = Case(created_by_id=ctx.user_id, updated_by_id=ctx.user_id, state=CaseStateEnum.OPEN, case_type_id=case_type_id, type_label=type_label)
    ctx.session.add(case)
    await ctx.session.commit()
    event = build_workspace_event(case, CaseEvent.CREATED, workspace_id=ctx.workspace_id)
    await dispatch([event])
    return {{"case": serialize_case(case)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "update_case.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.events import CaseEvent
from {a}.domain.cases.serializers import serialize_case
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_type import CaseType
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def update_case(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    if "case_type_id" not in data and "type_label" not in data:
        raise ValidationError("case_type_id or type_label is required")
    if "case_type_id" in data:
        case.case_type_id = data.get("case_type_id")
        if case.case_type_id and "type_label" not in data:
            case_type = await ctx.session.get(CaseType, case.case_type_id)
            case.type_label = case_type.name if case_type else case.type_label
    if "type_label" in data:
        case.type_label = data.get("type_label")
    case.updated_by_id = ctx.user_id
    case.updated_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    event = build_workspace_event(case, CaseEvent.UPDATED, workspace_id=ctx.workspace_id)
    await dispatch([event])
    return {{"case": serialize_case(case)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "update_case_state.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.events import CaseEvent, case_state_extra
from {a}.domain.cases.serializers import serialize_case
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def update_case_state(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    new_state = CaseStateEnum(data.get("new_state"))
    case.state = new_state
    case.updated_by_id = ctx.user_id
    case.updated_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    event = build_workspace_event(case, CaseEvent.STATE_CHANGED, workspace_id=ctx.workspace_id, extra=case_state_extra(new_state))
    await dispatch([event])
    return {{"case": serialize_case(case)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "add_participant.py", f"""\
from sqlalchemy import select, update

from {a}.domain.cases.events import CaseEvent
from {a}.domain.cases.serializers import serialize_participant
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def add_participant(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    case = await ctx.session.get(Case, data.get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    user_ids = set(data.get("user_ids") or [])
    existing = set((await ctx.session.execute(select(CaseParticipant.user_id).where(CaseParticipant.case_id == case.client_id, CaseParticipant.user_id.in_(user_ids)))).scalars().all())
    added = [CaseParticipant(case_id=case.client_id, user_id=user_id) for user_id in user_ids - existing]
    ctx.session.add_all(added)
    if added:
        await ctx.session.execute(update(Case).where(Case.client_id == case.client_id).values(participants_count=Case.participants_count + len(added)))
    await ctx.session.commit()
    if added:
        event = build_workspace_event(case, CaseEvent.PARTICIPANT_ADDED, workspace_id=ctx.workspace_id)
        await dispatch([event])
    return {{"added": [serialize_participant(participant) for participant in added]}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "remove_participant.py", f"""\
from sqlalchemy import func, update

from {a}.domain.cases.events import CaseEvent
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_participant import CaseParticipant
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def remove_participant(ctx: ServiceContext) -> dict:
    participant = await ctx.session.get(CaseParticipant, (ctx.incoming_data or {{}}).get("case_participant_client_id"))
    if participant is None:
        raise NotFound("CaseParticipant not found")
    case_id = participant.case_id
    case = await ctx.session.get(Case, case_id)
    await ctx.session.delete(participant)
    await ctx.session.execute(update(Case).where(Case.client_id == case_id).values(participants_count=func.greatest(Case.participants_count - 1, 0)))
    await ctx.session.commit()
    if case:
        event = build_workspace_event(case, CaseEvent.PARTICIPANT_REMOVED, workspace_id=ctx.workspace_id)
        await dispatch([event])
    return {{"deleted": True}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "create_conversation.py", f"""\
from sqlalchemy import update

from {a}.domain.cases.enums import CaseStateEnum
from {a}.domain.cases.events import CaseEvent
from {a}.domain.cases.serializers import serialize_conversation
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_workspace_event


async def create_conversation(ctx: ServiceContext) -> dict:
    case = await ctx.session.get(Case, (ctx.incoming_data or {{}}).get("case_client_id"))
    if case is None:
        raise NotFound("Case not found")
    conversation = CaseConversation(case_id=case.client_id, created_by_id=ctx.user_id, state=CaseStateEnum.OPEN)
    ctx.session.add(conversation)
    await ctx.session.execute(update(Case).where(Case.client_id == case.client_id).values(conversations_count=Case.conversations_count + 1))
    await ctx.session.commit()
    event = build_workspace_event(case, CaseEvent.CONVERSATION_CREATED, workspace_id=ctx.workspace_id)
    await dispatch([event])
    return {{"conversation": serialize_conversation(conversation)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "send_message.py", f"""\
from sqlalchemy import select, update

from {a}.domain.cases.events import ConversationMessageEvent, conversation_message_extra
from {a}.domain.cases.serializers import serialize_message
from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext
from {a}.services.infra.content import process_content_mentions, validate_content
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_conversation_event


async def _next_message_seq(ctx: ServiceContext, conversation_id: str) -> int:
    result = await ctx.session.execute(
        update(CaseConversation)
        .where(CaseConversation.client_id == conversation_id)
        .values(last_message_seq=CaseConversation.last_message_seq + 1)
        .returning(CaseConversation.last_message_seq)
    )
    return result.scalar_one()


async def send_message(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    conversation = await ctx.session.get(CaseConversation, data.get("conversation_client_id"))
    if conversation is None:
        raise NotFound("Conversation not found")
    blocks = validate_content(data.get("content"))
    content = [block.__dict__ for block in blocks]
    seq = await _next_message_seq(ctx, conversation.client_id)
    message = CaseConversationMessage(
        case_conversation_id=conversation.client_id,
        message_seq=seq,
        created_by_id=ctx.user_id,
        content=content,
        plain_text=data.get("plain_text", ""),
    )
    ctx.session.add(message)
    await ctx.session.flush()
    await process_content_mentions(ctx.session, content, ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE, message.client_id, ctx.user_id)
    await ctx.session.execute(update(CaseConversation).where(CaseConversation.client_id == conversation.client_id).values(messages_count=CaseConversation.messages_count + 1))
    await ctx.session.execute(update(Case).where(Case.client_id == conversation.case_id).values(messages_count=Case.messages_count + 1))
    await ctx.session.commit()
    event = build_conversation_event(
        message,
        ConversationMessageEvent.CREATED,
        conversation_id=conversation.client_id,
        workspace_id=ctx.workspace_id,
        extra=conversation_message_extra(seq),
    )
    await dispatch([event])
    return {{"message": serialize_message(message)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "edit_message.py", f"""\
from datetime import datetime, timezone

from {a}.domain.cases.events import ConversationMessageEvent
from {a}.domain.cases.serializers import serialize_message
from {a}.domain.content.enums import ContentMentionLinkEntityTypeEnum
from {a}.errors.not_found import NotFound
from {a}.errors.validation import ValidationError
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext
from {a}.services.infra.content import process_content_mentions, validate_content
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_conversation_event


async def edit_message(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data or {{}}
    message = await ctx.session.get(CaseConversationMessage, data.get("message_client_id"))
    if message is None:
        raise NotFound("Message not found")
    if message.has_been_deleted:
        raise ValidationError("deleted messages cannot be edited")
    blocks = validate_content(data.get("content"))
    content = [block.__dict__ for block in blocks]
    message.content = content
    message.plain_text = data.get("plain_text", "")
    message.has_been_edited = True
    message.updated_at = datetime.now(timezone.utc)
    await process_content_mentions(ctx.session, content, ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE, message.client_id, ctx.user_id, replace=True)
    await ctx.session.commit()
    event = build_conversation_event(
        message,
        ConversationMessageEvent.EDITED,
        conversation_id=message.case_conversation_id,
        workspace_id=ctx.workspace_id,
    )
    await dispatch([event])
    return {{"message": serialize_message(message)}}
""", force=True)

    _write(root / a / "services" / "commands" / "cases" / "soft_delete_message.py", f"""\
from sqlalchemy import func, select, update

from {a}.domain.cases.events import ConversationMessageEvent
from {a}.errors.not_found import NotFound
from {a}.models.tables.cases.case import Case
from {a}.models.tables.cases.case_conversation import CaseConversation
from {a}.models.tables.cases.case_conversation_message import CaseConversationMessage
from {a}.services.context import ServiceContext
from {a}.services.infra.events import dispatch
from {a}.services.infra.events.build_event import build_conversation_event


async def soft_delete_message(ctx: ServiceContext) -> dict:
    message = await ctx.session.get(CaseConversationMessage, (ctx.incoming_data or {{}}).get("message_client_id"))
    if message is None:
        raise NotFound("Message not found")
    conversation_id = message.case_conversation_id
    if not message.has_been_deleted:
        message.has_been_deleted = True
        conversation = await ctx.session.get(CaseConversation, conversation_id)
        await ctx.session.execute(update(CaseConversation).where(CaseConversation.client_id == conversation_id).values(messages_count=func.greatest(CaseConversation.messages_count - 1, 0)))
        if conversation:
            await ctx.session.execute(update(Case).where(Case.client_id == conversation.case_id).values(messages_count=func.greatest(Case.messages_count - 1, 0)))
    await ctx.session.commit()
    event = build_conversation_event(
        message,
        ConversationMessageEvent.DELETED,
        conversation_id=conversation_id,
        workspace_id=ctx.workspace_id,
    )
    await dispatch([event])
    return {{"deleted": True}}
""", force=True)
