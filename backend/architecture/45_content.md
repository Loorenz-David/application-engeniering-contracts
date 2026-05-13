# 45 — Input Content Schema & Mention Tracking

## Overview

The Input Content schema standardizes every `content` JSONB field used across domains that support rich-text input (case messages, task notes, task details). Content is always a list of typed blocks; each block carries a `text` string plus type-specific optional fields.

`ContentMention` and `ContentMentionLink` provide a fast-lookup layer on top of the JSONB: when content is saved, `process_content_mentions()` extracts every `MENTION` block and writes rows to these two tables. This enables O(1) "find all places entity X is mentioned" queries without scanning JSONB, and gives event integrations a clean hook to fire notifications when someone is mentioned.

---

## Input content block schema — `domain/content/schemas.py`

### Block structure

```
[
  { "type": "TEXT",    "text": "Hey "                                               },
  { "type": "MENTION", "text": "@alice", "mention": { "mention_table": "users",
                                                       "mention_id": 42,
                                                       "client_id": "usr_01abc..." } },
  { "type": "LABEL",  "text": "urgent", "label_value": "urgent"                    },
  { "type": "LINK",   "text": "ticket", "link": "https://..."                      }
]
```

Every block must have `type` and `text`. The additional keys (`mention`, `label_value`, `link`) are present **only** for their respective type and are invalid on any other type.

### Enums — `domain/content/enums.py`

```python
import enum


class InputContentTypeEnum(enum.Enum):
    TEXT    = "text"
    MENTION = "mention"
    LABEL   = "label"
    LINK    = "link"


class ContentMentionLinkEntityTypeEnum(enum.Enum):
    CASE_CONVERSATION_MESSAGE = "case_conversation_message"
    TASK_DETAILS_MENTION      = "task_details_mention"
    TASK_NOTE_MENTION         = "task_note_mention"
```

`ContentMentionLinkEntityTypeEnum` is extended here when a new content-bearing entity type gains mention tracking. Never pass raw strings.

### Dataclass — `domain/content/schemas.py`

```python
from dataclasses import dataclass


@dataclass
class InputContentBlock:
    type:        str
    text:        str
    mention:     dict | None = None
    label_value: str | None  = None
    link:        str | None  = None
```

---

## Validation utility — `services/infra/content.py` (partial)

```python
from my_app.domain.content.enums import InputContentTypeEnum
from my_app.domain.content.schemas import InputContentBlock
from my_app.errors import ValidationFailed


def validate_content_block(block: dict) -> InputContentBlock:
    if not isinstance(block, dict):
        raise ValidationFailed("Each content block must be a dict")

    block_type = block.get("type")
    if not block_type:
        raise ValidationFailed("Content block missing 'type'")

    try:
        type_enum = InputContentTypeEnum(block_type)
    except ValueError:
        raise ValidationFailed(f"Invalid content block type: {block_type!r}")

    text = block.get("text")
    if text is None:
        raise ValidationFailed("Content block missing 'text'")

    mention = None
    if type_enum == InputContentTypeEnum.MENTION:
        mention = block.get("mention")
        if not isinstance(mention, dict):
            raise ValidationFailed("MENTION block requires a 'mention' object")
        for key in ("mention_table", "mention_id", "client_id"):
            if key not in mention:
                raise ValidationFailed(f"MENTION block missing '{key}' in mention dict")

    label_value = None
    if type_enum == InputContentTypeEnum.LABEL:
        label_value = block.get("label_value")
        if label_value is None:
            raise ValidationFailed("LABEL block missing 'label_value'")

    link = None
    if type_enum == InputContentTypeEnum.LINK:
        link = block.get("link")
        if link is None:
            raise ValidationFailed("LINK block missing 'link'")

    return InputContentBlock(
        type        = type_enum.value,
        text        = text,
        mention     = mention,
        label_value = label_value,
        link        = link,
    )


def validate_content(content) -> list[InputContentBlock]:
    if not isinstance(content, list):
        raise ValidationFailed("content must be a list of blocks")
    return [validate_content_block(block) for block in content]
```

Call `validate_content(content)` in any service that accepts a content field, before writing to the database.

---

## Models

### `ContentMention` — `models/tables/content/content_mention.py`

One record per unique (mention_table, mention_id) pair. Created once; reused across every occurrence.

```python
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin


class ContentMention(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "cmt"
    __tablename__    = "content_mentions"
    __table_args__   = (
        UniqueConstraint("mention_table", "mention_id", name="uq_content_mention"),
    )

    mention_table: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    mention_id:    Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    links: Mapped[list["ContentMentionLink"]] = relationship(
        "ContentMentionLink",
        foreign_keys="[ContentMentionLink.content_mention_id]",
        back_populates="content_mention",
    )
```

`mention_table` is the source table name (e.g. `"users"`, `"tasks"`). `mention_id` is the `client_id` of the mentioned record — the string identifier from the input content block's `mention.client_id` field.

---

### `ContentMentionLink` — `models/tables/content/content_mention_link.py`

One record per occurrence of a mention in a specific content entity. A single `ContentMention` can have many `ContentMentionLink` rows across different messages and entities.

```python
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, declared_attr, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.content.enums import ContentMentionLinkEntityTypeEnum


class ContentMentionLink(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "cml"
    __tablename__    = "content_mention_links"

    content_mention_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("content_mentions.client_id"), nullable=False, index=True
    )

    entity_type: Mapped[ContentMentionLinkEntityTypeEnum] = mapped_column(
        SAEnum(ContentMentionLinkEntityTypeEnum, name="content_mention_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )

    entity_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    content_mention: Mapped["ContentMention"] = relationship(
        "ContentMention", foreign_keys=[content_mention_id], back_populates="links"
    )
    created_by: Mapped["User"] = relationship("User", foreign_keys="[ContentMentionLink.created_by_id]")
```

No DB-level FK on `entity_client_id` — same polymorphic pattern as `CaseLink` and `ImageLink`. The entity type uniquely identifies which table `entity_client_id` refers to.

---

## Design decisions

| Field | Original | Corrected | Reason |
|---|---|---|---|
| `ContentMentionLink.mention_id FK to CONTENT_MENTION` | as described | `content_mention_id FK to content_mentions` | Consistent `<table_singular>_id` FK naming |
| `ContentMentionLink.created_by FK to USER` | as described | `created_by_id FK to users` | Consistent with every `*_by_id` in the system |
| `ContentMention.mention_id` type | implied integer | string | Stores the `client_id` from the mention block — the public identifier |

---

## `process_content_mentions` utility — `services/infra/content.py`

```python
from datetime import datetime, timezone

from my_app.models import db
from my_app.models.tables.content.content_mention import ContentMention
from my_app.models.tables.content.content_mention_link import ContentMentionLink
from my_app.services.infra.identity import generate_id
from my_app.domain.content.enums import InputContentTypeEnum, ContentMentionLinkEntityTypeEnum


def process_content_mentions(
    content:       list,
    entity_type:   ContentMentionLinkEntityTypeEnum,
    entity_client_id: str,
    created_by_id: str,
    replace:       bool = False,
) -> None:
    if replace:
        db.session.query(ContentMentionLink).filter_by(
            entity_type=entity_type,
            entity_client_id=entity_client_id,
        ).delete()
        db.session.flush()

    now = datetime.now(timezone.utc)
    for block in (content or []):
        if block.get("type") != InputContentTypeEnum.MENTION.value:
            continue
        mention_data      = block.get("mention") or {}
        mention_table     = mention_data.get("mention_table")
        mention_client_id = mention_data.get("client_id")
        if not mention_table or not mention_client_id:
            continue

        cm = db.session.query(ContentMention).filter_by(
            mention_table=mention_table,
            mention_id=mention_client_id,
        ).first()
        if not cm:
            cm = ContentMention(
                client_id     = generate_id(ContentMention.CLIENT_ID_PREFIX),
                mention_table = mention_table,
                mention_id    = mention_client_id,
            )
            db.session.add(cm)
            db.session.flush()

        existing = db.session.query(ContentMentionLink).filter_by(
            content_mention_id=cm.client_id,
            entity_type=entity_type,
            entity_client_id=entity_client_id,
        ).first()
        if not existing:
            db.session.add(ContentMentionLink(
                client_id          = generate_id(ContentMentionLink.CLIENT_ID_PREFIX),
                content_mention_id = cm.client_id,
                entity_type        = entity_type,
                entity_client_id   = entity_client_id,
                created_at         = now,
                created_by_id      = created_by_id,
            ))
```

`replace=True` deletes all existing `ContentMentionLink` rows for the given entity before processing — used by edit operations where content changes replace the previous mention set.

---

## Integration pattern

### Service that creates content (e.g. `send_message`)

```python
from my_app.services.infra.content import validate_content, process_content_mentions
from my_app.domain.content.enums import ContentMentionLinkEntityTypeEnum

def send_message(ctx) -> dict:
    ...
    validate_content(content)           # raises ValidationFailed on invalid blocks

    db.session.add(message)
    db.session.flush()                  # assign message.client_id if generated by default

    process_content_mentions(
        content       = content,
        entity_type   = ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE,
        entity_client_id = message.client_id,
        created_by_id = created_by_id,
    )
    db.session.commit()
```

### Service that edits content (e.g. `edit_message`)

```python
def edit_message(ctx) -> dict:
    ...
    validate_content(content)

    process_content_mentions(           # replace=True removes stale mention links first
        content       = content,
        entity_type   = ContentMentionLinkEntityTypeEnum.CASE_CONVERSATION_MESSAGE,
        entity_client_id = message.client_id,
        created_by_id = message.created_by_id,
        replace       = True,
    )

    message.content         = content
    message.plain_text      = plain_text
    message.has_been_edited = True
    message.updated_at      = datetime.now(timezone.utc)
    db.session.commit()
```

The `validate_content` call must come before the DB write. The `process_content_mentions` call must come after `flush()` when the message `client_id` is generated by the model default.

---

## File structure

```
my_app/
├── domain/
│   └── content/
│       ├── enums.py     # InputContentTypeEnum, ContentMentionLinkEntityTypeEnum
│       └── schemas.py   # InputContentBlock dataclass
├── models/
│   └── tables/
│       └── content/
│           ├── content_mention.py
│           └── content_mention_link.py
└── services/
    └── infra/
        └── content.py   # validate_content_block, validate_content, process_content_mentions
```

---

## Rules

- **`validate_content(content)` must be called before every DB write of a content field.** It is the only gate against invalid block shapes reaching the JSONB column.
- **`process_content_mentions` must be called after `flush()` when needed.** The `entity_client_id` argument requires the row's `client_id`; flushing assigns it when the model default generated it.
- **Use `replace=True` on edit operations.** Edit replaces the entire content; stale mention links from the previous version must be removed before processing the new set.
- **`ContentMention` rows are shared and reused.** Never delete a `ContentMention` record when removing a `ContentMentionLink` — other links may still reference it.
- **Extend `ContentMentionLinkEntityTypeEnum` here when a new content-bearing entity type gains mention tracking.** Never pass raw strings.
- **`ContentMentionLink.entity_client_id` has no DB-level FK.** Same polymorphic pattern as `CaseLink` and `ImageLink` — the handler layer is responsible for ensuring the entity exists.
- **`ContentMention.mention_id` stores the `client_id` string.** It is the value from `mention.client_id` in the input content block.
- **Queries that need to fire events on mention (e.g. "notify @alice") should read from `content_mention_links`, not scan JSONB.** The link table is the authoritative fast-lookup surface.
