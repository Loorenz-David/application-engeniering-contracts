# 43 — Image Record Pattern

## Overview

The Image module provides a reusable, polymorphic image architecture that works across any entity type (items, cases, messages, etc.) without requiring schema changes when a new entity gains image support.

Four tables compose this module:

| Table | Purpose |
|---|---|
| `images` | The canonical image record — URL, provider, source, dimensions, lifecycle |
| `image_links` | Polymorphic join — binds an image to any entity via `(entity_type, entity_client_id)` |
| `image_annotations` | Structured overlay data on an image (drawings, measurements, AI labels) |
| `image_events` | Async operation tracking for uploads and syncs (follows the Event mixin → contract 42) |

The polymorphic link table is the key design decision: an image row is created once; `ImageLink` rows declare which entities reference it and in what display order. This means one image can appear in an item gallery, a case file, and a conversation message simultaneously — without duplicating the image row.

---

## Domain enums — `domain/images/enums.py`

```python
# domain/images/enums.py
import enum


class ImageStorageProviderEnum(enum.Enum):
    S3       = "s3"
    SHOPIFY  = "shopify"
    EXTERNAL = "external"


class ImageSourceTypeEnum(enum.Enum):
    UPLOADED     = "uploaded"
    SHOPIFY_SYNC = "shopify_sync"
    GENERATED    = "generated"


class ImageSourceReferenceEnum(enum.Enum):
    S3_IMAGE_URL      = "s3_image_url"
    SHOPIFY_IMAGE_URL = "shopify_image_url"


class ImageLinkEntityTypeEnum(enum.Enum):
    ITEM                      = "item"
    CASE                      = "case"
    CASE_CONVERSATION_MESSAGE = "case_conversation_message"


class ImageAnnotationTypeEnum(enum.Enum):
    DRAW        = "draw"
    ARROW       = "arrow"
    CIRCLE      = "circle"
    RECTANGLE   = "rectangle"
    TEXT        = "text"
    MEASUREMENT = "measurement"
    HIGHLIGHT   = "highlight"


class ImageEventTypeEnum(enum.Enum):
    UPLOAD_ITEM_IMAGE    = "upload_item_image"
    UPLOAD_CASE_IMAGE    = "upload_case_image"
    UPLOAD_MESSAGE_IMAGE = "upload_message_image"


class ImageEventErrorEnum(enum.Enum):
    UPLOAD_FAILED        = "upload_failed"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    STORAGE_UNAVAILABLE  = "storage_unavailable"
    FILE_TOO_LARGE       = "file_too_large"
    VIRUS_DETECTED       = "virus_detected"
```

Extend `ImageEventErrorEnum` as integration points reveal new failure modes. The variants above cover the common surface.

---

## `Image` — `models/tables/images/image.py`

```python
# models/tables/images/image.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, BigInteger
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.images.enums import (
    ImageStorageProviderEnum,
    ImageSourceTypeEnum,
    ImageSourceReferenceEnum,
)


class Image(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "img"
    __tablename__    = "images"

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

    width_px:        Mapped[int | None] = mapped_column(Integer,    nullable=True)
    height_px:       Mapped[int | None] = mapped_column(Integer,    nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_by_id: Mapped[str]      = mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)
    updated_by_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id"), nullable=True)
    deleted_by_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id"), nullable=True)

    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    last_event_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("image_events.client_id", use_alter=True, name="fk_image_last_event_id"),
        nullable=True,
    )

    # Relationships
    created_by:        Mapped["User"]                     = relationship("User", foreign_keys=[created_by_id])
    updated_by:        Mapped["User | None"]              = relationship("User", foreign_keys=[updated_by_id])
    deleted_by:        Mapped["User | None"]              = relationship("User", foreign_keys=[deleted_by_id])
    image_links:       Mapped[list["ImageLink"]]          = relationship("ImageLink", back_populates="image")
    image_annotations: Mapped[list["ImageAnnotation"]]   = relationship("ImageAnnotation", back_populates="image")
    events:            Mapped[list["ImageEvent"]]         = relationship("ImageEvent", foreign_keys="[ImageEvent.image_id]", back_populates="image")
    last_event:        Mapped["ImageEvent | None"]        = relationship("ImageEvent", foreign_keys="[Image.last_event_id]")
```

**Design decisions:**

- `file_size_bytes` uses `BigInteger` — matches the naming convention in the file storage contract (34). The unit is bytes, not KB, for precision.
- `source_reference` is nullable because `EXTERNAL` images may not have a structured provider reference.
- `deleted_at` is indexed — most list queries filter on `deleted_at IS NULL`.
- `updated_by_id` is a `String(64)` FK to `users.client_id`. The original schema said "raw string"; a FK is enforced and queryable.
- `last_event_id` uses `use_alter=True` to resolve the circular FK between `images` and `image_events` at the DDL level (see section below).

---

## `ImageLink` — `models/tables/images/image_link.py`

```python
# models/tables/images/image_link.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.images.enums import ImageLinkEntityTypeEnum


class ImageLink(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "iml"
    __tablename__    = "image_links"
    __table_args__   = (
        UniqueConstraint("image_id", "entity_type", "entity_client_id", name="uq_image_link_image_entity"),
    )

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id"), nullable=False, index=True)

    entity_type: Mapped[ImageLinkEntityTypeEnum] = mapped_column(
        SAEnum(ImageLinkEntityTypeEnum, name="image_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )
    entity_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_order:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    image: Mapped["Image"] = relationship("Image", back_populates="image_links")
```

**Design decision — `display_order` lives here, not on `Image`:**

An image can be linked to multiple entities. Its position in an item's gallery (slot 1) may differ from its position in a case file (slot 3). Storing `display_order` on `ImageLink` means each link carries independent ordering — you can reorder images per-entity without touching the image row itself.

**Polymorphic association trade-off:**

`entity_client_id` has no database-level FK constraint because it can reference any of several tables. This is intentional — the polymorphic pattern trades referential integrity at the DB layer for flexibility at the application layer. The application must enforce existence before creating a link; orphan cleanup runs via a scheduled job (see contract 37).

The `UniqueConstraint` on `(image_id, entity_type, entity_client_id)` prevents the same image from being linked to the same entity twice.

---

## `ImageAnnotation` — `models/tables/images/image_annotation.py`

```python
# models/tables/images/image_annotation.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.images.enums import ImageAnnotationTypeEnum


class ImageAnnotation(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "ian"
    __tablename__    = "image_annotations"

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id"), nullable=False, index=True)

    annotation_type: Mapped[ImageAnnotationTypeEnum] = mapped_column(
        SAEnum(ImageAnnotationTypeEnum, name="image_annotation_type_enum", create_type=True),
        nullable=False,
    )

    data:     Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    accuracy: Mapped[int | None]  = mapped_column(Integer, nullable=True)

    created_by_id: Mapped[str]      = mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    image:      Mapped["Image"] = relationship("Image", back_populates="image_annotations")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
```

`data` uses `JSONB` (PostgreSQL) — supports GIN indexing on annotation payload and is more efficient than `JSON` for reads. The `accuracy` column is an integer 0–100 representing a confidence percentage — relevant for AI-generated annotations; null for human-drawn ones.

**`data` shape per annotation type:**

| `annotation_type` | Expected `data` shape |
|---|---|
| `DRAW` | `{"points": [[x, y], ...], "color": "#hex", "width": 2}` |
| `ARROW` | `{"from": [x, y], "to": [x, y], "color": "#hex"}` |
| `CIRCLE` | `{"cx": x, "cy": y, "r": radius, "color": "#hex"}` |
| `RECTANGLE` | `{"x": x, "y": y, "w": width, "h": height, "color": "#hex"}` |
| `TEXT` | `{"x": x, "y": y, "text": "...", "font_size": 14}` |
| `MEASUREMENT` | `{"from": [x, y], "to": [x, y], "unit": "mm", "value": 12.5}` |
| `HIGHLIGHT` | `{"x": x, "y": y, "w": width, "h": height, "opacity": 0.4}` |

These shapes are conventions, not enforced at the DB level. Validate in the command layer before persisting.

---

## `ImageEvent` — `models/tables/images/image_event.py`

```python
# models/tables/images/image_event.py
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.models.base.event import Event
from my_app.domain.images.enums import ImageEventTypeEnum, ImageEventErrorEnum


class ImageEvent(IdentityMixin, Event, db.Model):
    CLIENT_ID_PREFIX = "iev"
    EVENT_TYPE_ENUM  = ImageEventTypeEnum
    EVENT_ERROR_ENUM = ImageEventErrorEnum
    __tablename__    = "image_events"

    image_id: Mapped[str] = mapped_column(String(64), ForeignKey("images.client_id"), nullable=False, index=True)

    image:      Mapped["Image"] = relationship("Image", foreign_keys=[image_id], back_populates="events")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[ImageEvent.created_by_id]")
```

Follows the Event mixin exactly as specified in contract 42. `EVENT_TYPE_ENUM` and `EVENT_ERROR_ENUM` are required class variables — the mixin raises `AttributeError` at class creation time if either is missing.

---

## Foundation services

These services are entity-agnostic. They accept `entity_type` and `entity_client_id` as explicit parameters and contain no application-specific business rules. Authorization, entity existence checks, and per-entity business rules belong in the caller (handler or thin domain command), not here.

### Result types — `domain/images/results.py`

```python
# domain/images/results.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class UploadUrlResult:
    upload_url:               str
    pending_upload_client_id: str
    storage_key:              str
    expires_in:               int        # seconds


@dataclass
class ImageEventResult:
    client_id:  str
    event_type: str
    state:      str
    created_at: str
    created_by: UserCompactResult | None = None
    last_error: str | None               = None


@dataclass
class ImageAnnotationResult:
    client_id:       str
    annotation_type: str
    data:            dict | None         = None
    accuracy:        int | None          = None
    created_at:      str                 = ""
    created_by:      UserCompactResult | None = None


@dataclass
class ImageResult:
    client_id:        str
    image_url:        str
    storage_provider: str
    source_type:      str
    source_reference: str | None
    width_px:         int | None
    height_px:        int | None
    file_size_bytes:  int | None
    created_at:       str
    created_by:       UserCompactResult | None    = None
    last_event:       ImageEventResult | None     = None
    events:           list                        = field(default_factory=list)
    image_annotation: ImageAnnotationResult | None = None


@dataclass
class ImageLinkResult:
    link_client_id: str
    image:          ImageResult
    entity_type:    str
    entity_client_id: str
    display_order:  int


@dataclass
class DownloadUrlResult:
    download_url: str
    expires_in:   int        # seconds
```

`get_image` eagerly loads `created_by`, `last_event`, `events` (all), and `image_annotations` (first). `list_images_for_entity` loads `created_by` and `last_event` only — events array and annotations are omitted in list context to avoid N+1 joins.

`serialize_image_full` outputs all fields. `events: []` and `image_annotation: null` are the default when not loaded. The serializer handles gracefully — never raises on unloaded fields.

---

### Commands

#### `generate_upload_url` — `services/commands/images/generate_upload_url.py`

Creates a `PendingUpload` row and returns a presigned PUT URL. The caller owns the auth check and the entity existence check.

```python
# services/commands/images/generate_upload_url.py
from dataclasses import dataclass
from my_app.domain.images.enums import ImageLinkEntityTypeEnum
from my_app.domain.images.results import UploadUrlResult
from my_app.domain.files.enums import PendingUploadStatusEnum
from my_app.errors import ValidationFailed
from my_app.models.tables.files.pending_upload import PendingUpload
from my_app.services.infra.storage import get_storage_client
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome
import uuid
from datetime import datetime, timezone, timedelta

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml",
}
_MAX_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB
_PRESIGN_TTL    = 900                # 15 minutes


def generate_upload_url(ctx: ServiceContext) -> StatusOutcome:
    data         = ctx.incoming_data or {}
    entity_type  = data.get("entity_type")
    entity_client_id = data.get("entity_client_id")
    file_name    = data.get("file_name", "")
    content_type = data.get("content_type", "")
    size_bytes   = data.get("file_size_bytes")

    if not entity_type or not entity_client_id:
        raise ValidationFailed("entity_type and entity_client_id are required")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValidationFailed(f"content_type '{content_type}' is not allowed")
    if size_bytes and size_bytes > _MAX_SIZE_BYTES:
        raise ValidationFailed("file exceeds maximum allowed size")

    storage_key = f"images/{entity_type}/{entity_client_id}/{uuid.uuid4()}"
    storage     = get_storage_client()
    upload_url  = storage.generate_presigned_put_url(storage_key, content_type, _PRESIGN_TTL)

    upload = PendingUpload(
        created_by_id = ctx.user_id,
        storage_key   = storage_key,
        file_name     = file_name,
        content_type  = content_type,
        status        = PendingUploadStatusEnum.PENDING,
        expires_at    = datetime.now(timezone.utc) + timedelta(seconds=_PRESIGN_TTL),
        size_bytes    = size_bytes,
    )
    db.session.add(upload)
    db.session.flush()

    return StatusOutcome.ok(UploadUrlResult(
        upload_url               = upload_url,
        pending_upload_client_id = upload.client_id,
        storage_key              = storage_key,
        expires_in               = _PRESIGN_TTL,
    ))
```

---

#### `confirm_upload` — `services/commands/images/confirm_upload.py`

Verifies the file landed in storage, then creates the `Image`, `ImageLink`, and `ImageEvent` rows in one transaction. Maps `entity_type` → event type so the event log is queryable by upload context.

```python
# services/commands/images/confirm_upload.py
from my_app.domain.images.enums import (
    ImageLinkEntityTypeEnum, ImageEventTypeEnum,
    ImageStorageProviderEnum, ImageSourceTypeEnum, ImageSourceReferenceEnum,
)
from my_app.domain.images.results import ImageResult
from my_app.domain.files.enums import PendingUploadStatusEnum
from my_app.errors import NotFound, ValidationFailed
from my_app.models.tables.files.pending_upload import PendingUpload
from my_app.models.tables.images.image import Image
from my_app.models.tables.images.image_link import ImageLink
from my_app.models.tables.images.image_event import ImageEvent
from my_app.services.infra.storage import get_storage_client
from my_app.services.infra.jobs.tasks import create_instant_task
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome
from my_app.config import settings
from sqlalchemy import func

_ENTITY_EVENT_MAP = {
    ImageLinkEntityTypeEnum.ITEM:                      ImageEventTypeEnum.UPLOAD_ITEM_IMAGE,
    ImageLinkEntityTypeEnum.CASE:                      ImageEventTypeEnum.UPLOAD_CASE_IMAGE,
    ImageLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE: ImageEventTypeEnum.UPLOAD_MESSAGE_IMAGE,
}


def confirm_upload(ctx: ServiceContext) -> StatusOutcome:
    data                     = ctx.incoming_data or {}
    pending_upload_client_id = data.get("pending_upload_client_id")
    entity_type_raw          = data.get("entity_type")
    entity_client_id         = data.get("entity_client_id")

    upload = PendingUpload.query.filter_by(client_id=pending_upload_client_id).first()
    if not upload:
        raise NotFound("PendingUpload not found")
    if upload.status != PendingUploadStatusEnum.PENDING:
        raise ValidationFailed("upload already confirmed or expired")

    storage  = get_storage_client()
    metadata = storage.head_object(upload.storage_key)
    if not metadata:
        raise ValidationFailed("file has not been uploaded yet")

    entity_type   = ImageLinkEntityTypeEnum(entity_type_raw)
    provider      = ImageStorageProviderEnum(settings.storage_provider.lower())
    source_ref    = ImageSourceReferenceEnum.S3_IMAGE_URL if provider == ImageStorageProviderEnum.S3 else None

    image = Image(
        image_url        = upload.storage_key,
        storage_provider = provider,
        source_type      = ImageSourceTypeEnum.UPLOADED,
        source_reference = source_ref,
        file_size_bytes  = metadata["content_length"],
        created_by_id    = ctx.user_id,
    )
    db.session.add(image)
    db.session.flush()

    next_order = db.session.query(func.count(ImageLink.client_id)).filter_by(
        entity_type=entity_type, entity_client_id=entity_client_id
    ).scalar()

    link = ImageLink(
        image_id      = image.client_id,
        entity_type   = entity_type,
        entity_client_id = entity_client_id,
        display_order = next_order,
    )
    db.session.add(link)

    event = ImageEvent(
        image_id      = image.client_id,
        type          = _ENTITY_EVENT_MAP[entity_type],
        created_by_id = ctx.user_id,
    )
    db.session.add(event)
    db.session.flush()

    image.last_event_id = event.client_id
    upload.status       = PendingUploadStatusEnum.CONFIRMED

    db.session.commit()

    return StatusOutcome.ok(ImageResult(
        client_id        = image.client_id,
        image_url        = image.image_url,
        storage_provider = image.storage_provider.value,
        source_type      = image.source_type.value,
        width_px         = image.width_px,
        height_px        = image.height_px,
        file_size_bytes  = image.file_size_bytes,
        created_at       = image.created_at,
    ))
```

---

#### `soft_delete_image` — `services/commands/images/soft_delete_image.py`

Marks the image as deleted. Physical storage removal is a background job — do not call `storage.delete_object()` here.

```python
# services/commands/images/soft_delete_image.py
from datetime import datetime, timezone
from my_app.errors import NotFound, ValidationFailed
from my_app.models.tables.images.image import Image
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome


def soft_delete_image(ctx: ServiceContext) -> StatusOutcome:
    image_client_id = (ctx.incoming_data or {}).get("image_client_id")

    image = Image.query.filter_by(client_id=image_client_id).first()
    if not image:
        raise NotFound("Image not found")
    if image.deleted_at is not None:
        raise ValidationFailed("image is already deleted")

    image.deleted_at    = datetime.now(timezone.utc)
    image.deleted_by_id = ctx.user_id
    db.session.commit()

    return StatusOutcome.ok({"client_id": image.client_id})
```

---

#### `unlink_image` — `services/commands/images/unlink_image.py`

Removes one `ImageLink` row. The `Image` row is not touched — the image remains and can still be linked to other entities.

```python
# services/commands/images/unlink_image.py
from my_app.errors import NotFound
from my_app.models.tables.images.image_link import ImageLink
from my_app.domain.images.enums import ImageLinkEntityTypeEnum
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome


def unlink_image(ctx: ServiceContext) -> StatusOutcome:
    data            = ctx.incoming_data or {}
    image_client_id = data.get("image_client_id")
    entity_type     = ImageLinkEntityTypeEnum(data.get("entity_type"))
    entity_client_id = data.get("entity_client_id")

    from my_app.models.tables.images.image import Image
    image = Image.query.filter_by(client_id=image_client_id).first()
    if not image:
        raise NotFound("Image not found")

    link = ImageLink.query.filter_by(
        image_id    = image.client_id,
        entity_type = entity_type,
        entity_client_id = entity_client_id,
    ).first()
    if not link:
        raise NotFound("ImageLink not found")

    db.session.delete(link)
    db.session.commit()

    return StatusOutcome.ok({"unlinked": True})
```

---

#### `create_annotation` — `services/commands/images/create_annotation.py`

Validates the `data` payload against required keys for the given annotation type, then inserts the row.

```python
# services/commands/images/create_annotation.py
from my_app.errors import NotFound, ValidationFailed
from my_app.models.tables.images.image import Image
from my_app.models.tables.images.image_annotation import ImageAnnotation
from my_app.domain.images.enums import ImageAnnotationTypeEnum
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome

_REQUIRED_KEYS: dict[ImageAnnotationTypeEnum, set[str]] = {
    ImageAnnotationTypeEnum.DRAW:        {"points", "color"},
    ImageAnnotationTypeEnum.ARROW:       {"from", "to"},
    ImageAnnotationTypeEnum.CIRCLE:      {"cx", "cy", "r"},
    ImageAnnotationTypeEnum.RECTANGLE:   {"x", "y", "w", "h"},
    ImageAnnotationTypeEnum.TEXT:        {"x", "y", "text"},
    ImageAnnotationTypeEnum.MEASUREMENT: {"from", "to", "unit", "value"},
    ImageAnnotationTypeEnum.HIGHLIGHT:   {"x", "y", "w", "h"},
}


def create_annotation(ctx: ServiceContext) -> StatusOutcome:
    data            = ctx.incoming_data or {}
    image_client_id = data.get("image_client_id")
    ann_type        = ImageAnnotationTypeEnum(data.get("annotation_type"))
    payload         = data.get("data") or {}
    accuracy        = data.get("accuracy")

    if accuracy is not None and not (0 <= accuracy <= 100):
        raise ValidationFailed("accuracy must be 0–100")

    required = _REQUIRED_KEYS.get(ann_type, set())
    missing  = required - payload.keys()
    if missing:
        raise ValidationFailed(f"missing required keys for {ann_type.value}: {sorted(missing)}")

    image = Image.query.filter_by(client_id=image_client_id).first()
    if not image or image.deleted_at is not None:
        raise NotFound("Image not found")

    annotation = ImageAnnotation(
        image_id        = image.client_id,
        annotation_type = ann_type,
        data            = payload,
        accuracy        = accuracy,
        created_by_id   = ctx.user_id,
    )
    db.session.add(annotation)
    db.session.commit()

    return StatusOutcome.ok({"client_id": annotation.client_id})
```

---

#### `reorder_links` — `services/commands/images/reorder_links.py`

Accepts an ordered list of image `client_id` values and reassigns `display_order` starting at 0. All updates run in one transaction.

```python
# services/commands/images/reorder_links.py
from my_app.errors import ValidationFailed
from my_app.models.tables.images.image import Image
from my_app.models.tables.images.image_link import ImageLink
from my_app.domain.images.enums import ImageLinkEntityTypeEnum
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome


def reorder_links(ctx: ServiceContext) -> StatusOutcome:
    data                   = ctx.incoming_data or {}
    entity_type            = ImageLinkEntityTypeEnum(data.get("entity_type"))
    entity_client_id       = data.get("entity_client_id")
    ordered_client_ids     = data.get("ordered_image_client_ids", [])

    images = {
        img.client_id: img
        for img in Image.query.filter(Image.client_id.in_(ordered_client_ids)).all()
    }
    links = {
        link.image_id: link
        for link in ImageLink.query.filter_by(entity_type=entity_type, entity_client_id=entity_client_id).all()
    }

    for position, client_id in enumerate(ordered_client_ids):
        image = images.get(client_id)
        if not image:
            raise ValidationFailed(f"image '{client_id}' not found")
        link = links.get(image.client_id)
        if not link:
            raise ValidationFailed(f"image '{client_id}' is not linked to this entity")
        link.display_order = position

    db.session.commit()
    return StatusOutcome.ok({"reordered": len(ordered_client_ids)})
```

---

### Queries

#### `get_download_url` — `services/queries/images/get_download_url.py`

```python
# services/queries/images/get_download_url.py
from my_app.errors import NotFound
from my_app.models.tables.images.image import Image
from my_app.domain.images.results import DownloadUrlResult
from my_app.services.infra.storage import get_storage_client
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome

_GET_TTL = 3600   # 1 hour


def get_download_url(ctx: ServiceContext) -> StatusOutcome:
    image_client_id = (ctx.incoming_data or {}).get("image_client_id")

    image = Image.query.filter_by(client_id=image_client_id).first()
    if not image or image.deleted_at is not None:
        raise NotFound("Image not found")

    url = get_storage_client().generate_presigned_get_url(image.image_url, _GET_TTL)
    return StatusOutcome.ok(DownloadUrlResult(download_url=url, expires_in=_GET_TTL))
```

---

#### `get_image` — `services/queries/images/get_image.py`

```python
# services/queries/images/get_image.py
from my_app.errors import NotFound
from my_app.models.tables.images.image import Image
from my_app.domain.images.results import ImageResult
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome


def get_image(ctx: ServiceContext) -> StatusOutcome:
    image_client_id = (ctx.incoming_data or {}).get("image_client_id")

    image = Image.query.filter_by(client_id=image_client_id).first()
    if not image or image.deleted_at is not None:
        raise NotFound("Image not found")

    return StatusOutcome.ok(ImageResult(
        client_id        = image.client_id,
        image_url        = image.image_url,
        storage_provider = image.storage_provider.value,
        source_type      = image.source_type.value,
        width_px         = image.width_px,
        height_px        = image.height_px,
        file_size_bytes  = image.file_size_bytes,
        created_at       = image.created_at,
    ))
```

---

#### `list_images_for_entity` — `services/queries/images/list_images_for_entity.py`

Returns active (non-deleted) images linked to an entity, ordered by `display_order`.

```python
# services/queries/images/list_images_for_entity.py
from my_app.models.tables.images.image import Image
from my_app.models.tables.images.image_link import ImageLink
from my_app.domain.images.enums import ImageLinkEntityTypeEnum
from my_app.domain.images.results import ImageLinkResult, ImageResult
from my_app.services.context import ServiceContext
from my_app.services.outcome import StatusOutcome


def list_images_for_entity(ctx: ServiceContext) -> StatusOutcome:
    data        = ctx.incoming_data or {}
    entity_type = ImageLinkEntityTypeEnum(data.get("entity_type"))
    entity_client_id = data.get("entity_client_id")

    rows = (
        db.session.query(ImageLink, Image)
        .join(Image, Image.client_id == ImageLink.image_id)
        .filter(
            ImageLink.entity_type == entity_type,
            ImageLink.entity_client_id == entity_client_id,
            Image.deleted_at.is_(None),
        )
        .order_by(ImageLink.display_order)
        .all()
    )

    results = [
        ImageLinkResult(
            link_client_id = link.client_id,
            entity_type    = link.entity_type.value,
            entity_client_id = link.entity_client_id,
            display_order  = link.display_order,
            image          = ImageResult(
                client_id        = image.client_id,
                image_url        = image.image_url,
                storage_provider = image.storage_provider.value,
                source_type      = image.source_type.value,
                width_px         = image.width_px,
                height_px        = image.height_px,
                file_size_bytes  = image.file_size_bytes,
                created_at       = image.created_at,
            ),
        )
        for link, image in rows
    ]

    return StatusOutcome.ok(results)
```

---

### Foundation services — isolation rule

Foundation services contain **no authorization logic and no entity existence checks**. The caller is responsible for both before invoking a foundation service. This keeps the services composable: an item handler, a case handler, and a message handler all call the same `confirm_upload` — each enforcing its own auth rules before the call.

```
Handler (entity-specific)
  │
  ├─ 1. Verify caller has permission for this entity (auth — handler layer)
  ├─ 2. Verify entity exists (DB check — handler layer)
  └─ 3. Call foundation service (confirm_upload / create_annotation / etc.)
           └─ foundation service handles image mechanics only
```

---

## Circular FK — `use_alter`

`Image.last_event_id` → `image_events.client_id` and `ImageEvent.image_id` → `images.client_id` form a circular dependency. PostgreSQL cannot create both FKs in the same `CREATE TABLE` pass.

SQLAlchemy resolves this with `use_alter=True` on the FK that should be added as a separate `ALTER TABLE` after both tables exist:

```python
last_event_id: Mapped[str | None] = mapped_column(
    String(64),
    ForeignKey("image_events.client_id", use_alter=True, name="fk_image_last_event_id"),
    nullable=True,
)
```

Alembic picks this up automatically during `alembic revision --autogenerate`. No manual migration editing needed.

---

## File structure

```
my_app/
├── domain/
│   └── images/
│       ├── enums.py              # all image domain enums
│       └── results.py            # UploadUrlResult, ImageResult, ImageLinkResult, DownloadUrlResult
├── models/
│   └── tables/
│       └── images/
│           ├── image.py              # Image(IdentityMixin, db.Model)
│           ├── image_link.py         # ImageLink(IdentityMixin, db.Model)
│           ├── image_annotation.py   # ImageAnnotation(IdentityMixin, db.Model)
│           └── image_event.py        # ImageEvent(IdentityMixin, Event, db.Model)
└── services/
    ├── commands/
    │   └── images/
    │       ├── generate_upload_url.py   # presigned PUT + PendingUpload row
    │       ├── confirm_upload.py        # Image + ImageLink + ImageEvent in one tx
    │       ├── soft_delete_image.py     # sets deleted_at / deleted_by_id
    │       ├── unlink_image.py          # removes ImageLink row only
    │       ├── create_annotation.py     # validates + inserts ImageAnnotation
    │       └── reorder_links.py         # bulk display_order update
    └── queries/
        └── images/
            ├── get_download_url.py          # presigned GET URL
            ├── get_image.py                 # single image by client_id
            └── list_images_for_entity.py    # ordered list for (entity_type, entity_client_id)
```

Register all four in `models/__init__.py` under the image section so Alembic detects them:

```python
# ── Image ────────────────────────────────────────────
from .tables.images.image            import Image
from .tables.images.image_link       import ImageLink
from .tables.images.image_annotation import ImageAnnotation
from .tables.images.image_event      import ImageEvent
```

---

## Adding a new entity type

When a new entity (e.g., `WORK_ORDER`) needs image support:

1. Add `WORK_ORDER = "work_order"` to `ImageLinkEntityTypeEnum` in `domain/images/enums.py`.
2. Generate and apply a migration — Alembic updates the Postgres enum type.
3. No model changes required.

The entity table itself does not need any image columns or FKs — all linkage lives in `image_links`.

---

## Rules

- **`IMAGE_TYPE_ENUM` and `IMAGE_ERROR_ENUM` must be set on `ImageEvent`.** The Event mixin enforces this at class-creation time.
- **`deleted_at IS NOT NULL` means the image is soft-deleted.** Queries must filter it. Storage cleanup (deleting the object from S3) is a background job triggered by the image event, not the delete command.
- **`image_url` is the canonical public URL.** For S3 images this may be a presigned URL at write time, but the stored value should be the permanent object key or CDN URL, not a short-lived presigned URL. Generate presigned GET URLs at read time (see contract 34).
- **Validate `data` shape in the command layer.** The `JSONB` column has no schema constraint. The command for each annotation type is responsible for validating the payload against the expected shape before inserting.
- **`display_order` is per-link, not per-image.** Always read `display_order` from the `ImageLink` row, not from `Image`. Sort image galleries by `ImageLink.display_order` when querying.
- **No FK constraint on `entity_client_id`.** The application must verify the referenced entity exists before creating an `ImageLink`. The scheduled orphan-cleanup job (contract 37) removes links whose `entity_client_id` no longer exists.
- **Workers set `state = IN_PROGRESS` before doing work and `state = COMPLETED` or `state = FAILED` after.** Idempotency guard: if `event.state in (COMPLETED, FAILED)` on entry, return early. See contract 42 for the full worker flow.
- **`use_alter=True` on `Image.last_event_id` is required.** Removing it breaks the migration on fresh databases.
- **`source_reference` is nullable.** Set it only when the URL origin is a known structured provider (S3 or Shopify). Leave null for truly external URLs.
- **`accuracy` is 0–100 integer.** Null means the annotation was created by a human (no confidence score applies). Never store a float; round at the command layer.
