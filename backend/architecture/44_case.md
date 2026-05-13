# 44 — Case Record Pattern

## Overview

The `Case` architecture provides a polymorphic case-management system for logistics-focused applications. Six models cover the full lifecycle: a type registry (`CaseType`), the case itself (`Case`), a polymorphic entity-link table (`CaseLink`), participants (`CaseParticipant`), conversation threads (`CaseConversation`), and rich-content messages (`CaseConversationMessage`).

`CaseLink` follows the same polymorphic-association pattern as `ImageLink` (see [43_image.md](43_image.md)): `(entity_type, entity_client_id)` without a DB-level FK constraint, allowing any entity to be linked to a case with a semantic role — no schema change required when adding new entity types. `CaseLinkEntityTypeEnum` is the single source of truth for supported entity types.

---

## Domain events — `domain/cases/events.py`

```python
import enum


class CaseEvent(str, enum.Enum):
    CREATED              = "case:created"
    UPDATED              = "case:updated"
    DELETED              = "case:deleted"
    STATE_CHANGED        = "case:state-changed"
    PARTICIPANT_ADDED    = "case:participant-added"
    PARTICIPANT_REMOVED  = "case:participant-removed"
    CONVERSATION_CREATED = "case:conversation-created"


class ConversationMessageEvent(str, enum.Enum):
    CREATED = "conversation:message-created"
    EDITED  = "conversation:message-edited"
    DELETED = "conversation:message-deleted"
```

`CaseEvent` values are broadcast to the workspace room — all participants see case-level changes regardless of which conversation they have open. `ConversationMessageEvent` values are broadcast to the conversation room — only users currently viewing that conversation receive them in real time.

### Event extras

```python
# domain/cases/events.py (continued)
from my_app.domain.cases.enums import CaseStateEnum


def case_state_extra(new_state: CaseStateEnum) -> dict:
    return {"new_state": new_state.value}


def conversation_message_extra(message_seq: int) -> dict:
    return {"message_seq": message_seq}
```

Extra builders are kept in the same file as the enum so the domain layer stays self-contained.

---

### Event wiring — which command emits what

| Command | Event type | Event name | Broadcast target |
|---|---|---|---|
| `create_case` | `WorkspaceEvent` | `CaseEvent.CREATED` | workspace room |
| `update_case` | `WorkspaceEvent` | `CaseEvent.UPDATED` | workspace room |
| `update_case_state` | `WorkspaceEvent` | `CaseEvent.STATE_CHANGED` + `extra=case_state_extra(new_state)` | workspace room |
| `add_participant` | `WorkspaceEvent` | `CaseEvent.PARTICIPANT_ADDED` | workspace room |
| `remove_participant` | `WorkspaceEvent` | `CaseEvent.PARTICIPANT_REMOVED` | workspace room |
| `create_conversation` | `WorkspaceEvent` | `CaseEvent.CONVERSATION_CREATED` | workspace room |
| `send_message` | `ConversationRoomEvent` | `ConversationMessageEvent.CREATED` + `extra=conversation_message_extra(seq)` | conversation room |
| `edit_message` | `ConversationRoomEvent` | `ConversationMessageEvent.EDITED` | conversation room |
| `soft_delete_message` | `ConversationRoomEvent` | `ConversationMessageEvent.DELETED` | conversation room |

`mark_read` emits no event — it is a private, per-participant operation with no side effect visible to others.

### Example — send_message with event

```python
# services/commands/cases/send_message.py
from my_app.services.infra.events.build_event import build_conversation_event
from my_app.services.infra.events import event_bus
from my_app.domain.cases.events import ConversationMessageEvent, conversation_message_extra


async def send_message(ctx: ServiceContext) -> dict:
    request      = parse_send_message_request(ctx.incoming_data)
    pending_events: list = []

    async with ctx.session.begin():
        conversation = await _resolve_conversation(ctx.session, request.conversation_client_id)
        seq          = await _next_message_seq(ctx.session, conversation.client_id)
        message      = CaseConversationMessage(
            case_conversation_id=conversation.client_id,
            message_seq=seq,
            created_by_id=ctx.user_id,
            content=request.content,
            plain_text=request.plain_text,
        )
        ctx.session.add(message)
        await ctx.session.flush()

        pending_events.append(build_conversation_event(
            message,
            ConversationMessageEvent.CREATED,
            conversation_id=conversation.client_id,
            workspace_id=ctx.workspace_id,
            extra=conversation_message_extra(seq),
        ))

    event_bus.dispatch(pending_events)
    return {"message": serialize_message(message)}
```

---

## Shared enums — `domain/cases/enums.py`

```python
import enum


class CaseLinkEntityTypeEnum(enum.Enum):
    TASK     = "task"
    CUSTOMER = "customer"


class CaseLinkRoleEnum(enum.Enum):
    ORIGIN     = "origin"
    SUBJECT    = "subject"
    CONTEXT    = "context"
    ACTOR      = "actor"
    RESOLUTION = "resolution"


class CaseStateEnum(enum.Enum):
    OPEN      = "open"
    RESOLVING = "resolving"
    RESOLVED  = "resolved"
```

`CaseLinkEntityTypeEnum` is extended here when a new entity type gains case-link support. Never pass raw strings as `entity_type` in application code.

---

## Models

### `CaseType` — `models/tables/cases/case_type.py`

```python
from sqlalchemy import String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.cases.enums import CaseLinkEntityTypeEnum


class CaseType(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "cty"
    __tablename__    = "case_types"

    name:        Mapped[str]       = mapped_column(String(128),  nullable=False)
    image:       Mapped[str | None] = mapped_column(String(512),  nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    entity_type: Mapped[CaseLinkEntityTypeEnum] = mapped_column(
        SAEnum(CaseLinkEntityTypeEnum, name="case_link_entity_type_enum", create_type=True),
        nullable=False,
        index=True,
    )

    cases: Mapped[list["Case"]] = relationship(
        "Case",
        foreign_keys="[Case.case_type_id]",
        back_populates="case_type",
    )
```

`image` is stored as a plain URL string. If the application uses the image architecture (contract 43), the handler resolves an `ImageLink` and stores its download URL here. The FK `create_type=True` here owns the `case_link_entity_type_enum` Postgres type; all other models that use this enum must set `create_type=False`.

---

### `Case` — `models/tables/cases/case.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, declared_attr, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.models.base.history_record import HistoryRecord
from my_app.domain.cases.enums import CaseStateEnum


class Case(IdentityMixin, HistoryRecord, db.Model):
    CLIENT_ID_PREFIX = "ca"
    __tablename__    = "cases"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    state: Mapped[CaseStateEnum] = mapped_column(
        SAEnum(CaseStateEnum, name="case_state_enum", create_type=True),
        nullable=False,
        default=CaseStateEnum.OPEN,
        index=True,
    )

    case_type_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("case_types.client_id"), nullable=True, index=True
    )

    type_label:          Mapped[str | None] = mapped_column(String(128), nullable=True)
    participants_count:  Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    conversations_count: Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    messages_count:      Mapped[int]        = mapped_column(Integer, nullable=False, default=0)

    case_type:     Mapped["CaseType | None"]           = relationship("CaseType",        foreign_keys=[case_type_id], back_populates="cases")
    created_by:    Mapped["User"]                       = relationship("User",            foreign_keys="[Case.created_by_id]")
    updated_by:    Mapped["User | None"]                = relationship("User",            foreign_keys="[Case.updated_by_id]")
    participants:  Mapped[list["CaseParticipant"]]      = relationship("CaseParticipant", foreign_keys="[CaseParticipant.case_id]",   back_populates="case")
    conversations: Mapped[list["CaseConversation"]]     = relationship("CaseConversation",foreign_keys="[CaseConversation.case_id]",  back_populates="case")
    links:         Mapped[list["CaseLink"]]             = relationship("CaseLink",        foreign_keys="[CaseLink.case_id]",          back_populates="case")
```

`HistoryRecord` provides `updated_at` and `updated_by_id` (FK to `users.client_id`). `type_label` is a free-text snapshot — populated from `CaseType.name` when `case_type_id` is supplied, but accepts any string, enabling case types not registered in `case_types`.

`conversations_count` and `messages_count` are denormalized counters updated by `create_conversation`, `send_message`, and `soft_delete_message`. These are the authoritative display counts — never derived from a `COUNT(*)`. `messages_count` on `Case` is the total across all conversations; `messages_count` on `CaseConversation` is per-thread.

---

### `CaseLink` — `models/tables/cases/case_link.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.cases.enums import CaseLinkEntityTypeEnum, CaseLinkRoleEnum


class CaseLink(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "clk"
    __tablename__    = "case_links"
    __table_args__   = (
        UniqueConstraint("case_id", "entity_type", "entity_client_id", name="uq_case_link_case_entity"),
    )

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.client_id"), nullable=False, index=True
    )

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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id], back_populates="links")
```

No DB-level FK on `entity_client_id` — same design as `ImageLink`. The handler must verify entity existence before calling `link_entity`. `entity_type` uses `create_type=False` because `CaseType` owns the Postgres enum.

---

### `CaseParticipant` — `models/tables/cases/case_participant.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin


class CaseParticipant(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "cpa"
    __tablename__    = "case_participants"
    __table_args__   = (
        UniqueConstraint("case_id", "user_id", name="uq_case_participant"),
    )

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.client_id"), nullable=False, index=True
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    last_read_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id], back_populates="participants")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
```

`last_read_message_seq` enables O(1) unread count: `conversation.last_message_seq − participant.last_read_message_seq`. Updated by the `mark_read` command — never decremented.

---

### `CaseConversation` — `models/tables/cases/case_conversation.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, declared_attr, relationship
from my_app.models.base.identity import IdentityMixin
from my_app.domain.cases.enums import CaseStateEnum


class CaseConversation(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "ccv"
    __tablename__    = "case_conversations"

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.client_id"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    state: Mapped[CaseStateEnum] = mapped_column(
        SAEnum(CaseStateEnum, name="case_state_enum", create_type=False),
        nullable=False,
        default=CaseStateEnum.OPEN,
        index=True,
    )

    last_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_count:   Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    case:       Mapped["Case"]                           = relationship("Case", foreign_keys=[case_id], back_populates="conversations")
    created_by: Mapped["User"]                           = relationship("User", foreign_keys="[CaseConversation.created_by_id]")
    messages:   Mapped[list["CaseConversationMessage"]]  = relationship(
        "CaseConversationMessage",
        foreign_keys="[CaseConversationMessage.case_conversation_id]",
        back_populates="conversation",
        order_by="CaseConversationMessage.message_seq",
    )
```

`state` reuses the `case_state_enum` Postgres type (`create_type=False`). `last_message_seq` is the atomic counter used to assign `message_seq` on new messages — see Atomic message sequencing. `messages_count` tracks non-deleted message count and is decremented by `soft_delete_message`.

---

### `CaseConversationMessage` — `models/tables/cases/case_conversation_message.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, declared_attr, relationship
from my_app.models.base.identity import IdentityMixin


class CaseConversationMessage(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "ccm"
    __tablename__    = "case_conversation_messages"
    __table_args__   = (
        UniqueConstraint("case_conversation_id", "message_seq", name="uq_message_seq"),
    )

    case_conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("case_conversations.client_id"), nullable=False, index=True
    )

    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    content:    Mapped[list | dict] = mapped_column(JSONB, nullable=False)
    plain_text: Mapped[str]         = mapped_column(Text,  nullable=False, default="")

    has_been_edited:  Mapped[bool]           = mapped_column(Boolean,              nullable=False, default=False)
    updated_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_been_deleted: Mapped[bool]           = mapped_column(Boolean,              nullable=False, default=False)

    conversation: Mapped["CaseConversation"] = relationship(
        "CaseConversation", foreign_keys=[case_conversation_id], back_populates="messages"
    )
    created_by: Mapped["User"] = relationship("User", foreign_keys="[CaseConversationMessage.created_by_id]")
```

`content` is JSONB — a list of rich-text content block objects following the application's content schema. `plain_text` is extracted from `content` at write time for full-text search; never recomputed at read time. `has_been_deleted` rows are retained for `message_seq` continuity and audit — the query layer redacts content.

---

## Design decisions

| Field | Original | Corrected | Reason |
|---|---|---|---|
| `Case.created_by` | string | `created_by_id` FK to `users` | FK is auditable and queryable; string snapshots are not |
| `Case.updated_by` | string | `updated_by_id` via `HistoryRecord` | Consistent with every `*_by_id` in the system |
| `Case.type` | string named `type` | `type_label` string | Avoids shadowing Python builtin; name clarifies it is a snapshot |
| `CASE_LINK.case__id` FK to CASE_TYPE | as described | `case_id` FK to `cases` | CaseLink links a case to an entity — the case_id points to `cases` |
| `CASE_TYPE.case_id` FK to CASE | as described | removed from foundation | No semantic value at the foundation layer; application-specific if needed |
| `has_been_edit` | boolean name | `has_been_edited` | Correct past participle |
| `CASE_CONVERSATION.created_by` | FK to USER | `created_by_id` FK to `users` | Consistent naming throughout |
| `content_mentions` on CASE / CASE_CONVERSATION | relationship | not in foundation | `ContentMention` is application-specific; add the relationship in the application once the model is defined |

---

## Atomic message sequencing

`message_seq` must be assigned via an atomic UPDATE — never via `SELECT MAX(message_seq) + 1`, which is vulnerable to race conditions under concurrent inserts:

```python
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession


async def _next_message_seq(session: AsyncSession, conversation_id: str) -> int:
    stmt = (
        update(CaseConversation)
        .where(CaseConversation.client_id == conversation_id)
        .values(last_message_seq=CaseConversation.last_message_seq + 1)
        .returning(CaseConversation.last_message_seq)
    )
    result = await session.execute(stmt)
    return result.scalar_one()
```

This UPDATE holds a row-level lock for the duration of the transaction, serializing concurrent message inserts on the same conversation. The returned value is the new `last_message_seq` — used directly as `message_seq` on the new message row.

---

## Foundation services

All foundation services contain **zero auth logic**. Handlers own: JWT validation, entity existence checks, permission checks, and `ServiceContext` construction.

### Result types — `domain/cases/results.py`

```python
from dataclasses import dataclass
from dataclasses import dataclass, field
from typing import Any
from my_app.domain.users.results import UserCompactResult


@dataclass
class CaseResult:
    client_id:            str
    state:                str
    type_label:           str | None
    participants_count:   int
    conversations_count:  int
    messages_count:       int
    created_at:           str
    created_by_id:        str


@dataclass
class CaseLinkResult:
    client_id:   str
    entity_type: str
    entity_client_id: str
    role:        str
    created_at:  str


@dataclass
class CaseParticipantResult:
    client_id:             str
    user_id:               str
    last_read_message_seq: int
    joined_at:             str


@dataclass
class CaseConversationResult:
    client_id:        str
    state:            str
    messages_count:   int
    last_message_seq: int
    created_at:       str
    last_messages:    list = field(default_factory=list)  # list[CaseConversationMessageResult]


@dataclass
class CaseConversationMessageResult:
    client_id:        str
    message_seq:      int
    content:          list[Any] | None  # None when has_been_deleted=True
    plain_text:       str
    has_been_edited:  bool
    has_been_deleted: bool
    created_at:       str
    created_by:       UserCompactResult | None = None
```

---

### Commands

#### `create_case` — `services/commands/cases/create_case.py`

```python
# incoming_data keys:
#   created_by_id (str)
#   case_type_id  (str | None)   — optional FK to case_types.client_id
#   type_label    (str | None)   — populated from CaseType.name if omitted and case_type_id provided
#
# Resolves CaseType when case_type_id is given; snapshots CaseType.name into type_label.
# Returns CaseResult.
```

#### `update_case` — `services/commands/cases/update_case.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   updated_by_id  (str)
#   case_type_id   (str | None)  — key presence drives update; null clears the FK
#   type_label     (str | None)  — key presence drives update; auto-populated from
#                                  CaseType.name when case_type_id is set without a label
#
# Uses key-presence checks ("case_type_id" in data) to distinguish "clear this field"
# (key present, value null) from "leave this field alone" (key absent).
# At least one of case_type_id or type_label must be present alongside the required keys.
# Returns CaseResult.
```

#### `update_case_state` — `services/commands/cases/update_case_state.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   new_state      (str)          — must be a valid CaseStateEnum value
#   updated_by_id  (str)
#
# Updates Case.state, Case.updated_by_id, Case.updated_at.
# Transition guards (e.g. RESOLVED → OPEN not allowed) belong in the handler layer.
# Returns CaseResult.
```

#### `link_entity` — `services/commands/cases/link_entity.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   entity_type    (str)   — must be a valid CaseLinkEntityTypeEnum value
#   entity_client_id (str) — handler must verify entity existence before calling
#   role           (str)   — must be a valid CaseLinkRoleEnum value
#
# Creates a CaseLink row. Raises DomainError if entity_type or role is invalid,
# or if the (case_id, entity_type, entity_client_id) combination already exists.
# Returns CaseLinkResult.
```

#### `unlink_entity` — `services/commands/cases/unlink_entity.py`

```python
# incoming_data keys:
#   case_link_client_id (str)
#
# Hard-deletes the CaseLink row. Raises DomainError if not found.
```

#### `add_participant` — `services/commands/cases/add_participant.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   user_ids       (list[str])  — one or more user.client_id values to add in a single transaction
#
# Queries existing participants to filter out duplicates — no error on overlap.
# Bulk-inserts only the net-new participants, then increments Case.participants_count
# atomically via UPDATE by the count of newly inserted rows only.
# Returns {"added": list[dict]} — newly added participants; empty list if all
# supplied user_ids were already participants.
```

#### `remove_participant` — `services/commands/cases/remove_participant.py`

```python
# incoming_data keys:
#   case_participant_client_id (str)
#
# Deletes CaseParticipant row and decrements Case.participants_count atomically.
# Never decrements below 0.
```

#### `create_conversation` — `services/commands/cases/create_conversation.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   created_by_id  (str)
#
# Creates a new CaseConversation with state=OPEN, last_message_seq=0.
# Returns CaseConversationResult.
```

#### `send_message` — `services/commands/cases/send_message.py`

```python
# incoming_data keys:
#   conversation_client_id (str)
#   created_by_id          (str)
#   content                (list[dict])  — rich-text content blocks
#   plain_text             (str)         — extracted plain text; handler extracts before calling
#
# Assigns message_seq via the atomic UPDATE pattern on CaseConversation.last_message_seq.
# Returns CaseConversationMessageResult.
```

#### `edit_message` — `services/commands/cases/edit_message.py`

```python
# incoming_data keys:
#   message_client_id (str)
#   content           (list[dict])
#   plain_text        (str)
#
# Updates content, plain_text, has_been_edited=True, updated_at=now().
# Raises DomainError if has_been_deleted=True.
# Returns CaseConversationMessageResult.
```

#### `soft_delete_message` — `services/commands/cases/soft_delete_message.py`

```python
# incoming_data keys:
#   message_client_id (str)
#
# Sets has_been_deleted=True. Content is NOT removed from the DB — redaction
# is the responsibility of the query/serialization layer.
```

#### `mark_read` — `services/commands/cases/mark_read.py`

```python
# incoming_data keys:
#   case_participant_client_id (str)
#   up_to_message_seq          (int)
#
# Updates CaseParticipant.last_read_message_seq only when up_to_message_seq is greater
# than the current value. Idempotent on duplicate delivery — never decrements.
```

---

### Queries

#### `get_case` — `services/queries/cases/get_case.py`

```python
# incoming_data keys:
#   case_client_id (str)
# Returns CaseResult. Raises DomainError if not found.
```

#### `list_cases` — `services/queries/cases/list_cases.py`

```python
# incoming_data keys (all optional filters):
#   state         (str | None)   — CaseStateEnum value
#   entity_type   (str | None)   — filter via JOIN on CaseLink
#   entity_client_id (str | None) — filter via JOIN on CaseLink
#   created_by_id    (str | None)
#   limit         (int, default 50)
#   offset        (int, default 0)
#
# When entity_type + entity_client_id are both provided, JOINs case_links to filter.
# Returns list[CaseResult].
```

#### `get_conversation` — `services/queries/cases/get_conversation.py`

```python
# incoming_data keys:
#   conversation_client_id (str)
# Returns CaseConversationResult. Raises DomainError if not found.
```

#### `list_messages` — `services/queries/cases/list_messages.py`

```python
# incoming_data keys:
#   conversation_client_id (str)
#   limit      (int, default 50)
#   before_seq (int | None)      — cursor for keyset pagination
#
# Returns list[CaseConversationMessageResult] ordered by message_seq DESC.
# Deleted messages are included; their content field is set to None.
```

#### `list_participants` — `services/queries/cases/list_participants.py`

```python
# incoming_data keys:
#   case_client_id (str)
# Returns list[CaseParticipantResult].
```

#### `list_linked_entities` — `services/queries/cases/list_linked_entities.py`

```python
# incoming_data keys:
#   case_client_id (str)
#   entity_type    (str | None)  — optional filter by CaseLinkEntityTypeEnum value
#   role           (str | None)  — optional filter by CaseLinkRoleEnum value
# Returns list[CaseLinkResult].
```

#### `get_unread_counts` — `services/queries/cases/get_unread_counts.py`

```python
# incoming_data keys:
#   user_id                  (str)         — participant user.client_id whose unread state is queried
#   conversation_client_ids  (list[str])   — optional; if omitted, returns all conversations
#                                            where the user has at least one unread message
#
# Two operating modes:
#   Client mode  — pass conversation_client_ids; returns unread count for each (including 0)
#   Worker mode  — omit conversation_client_ids; returns only conversations with unread > 0
#
# One JOIN query regardless of list size (no N+1).
# Unread count = last_message_seq − last_read_message_seq, floored at 0.
#
# Returns: {"unread_counts": {conversation_client_id: int, ...}}
```

Unread count is arithmetic — no `COUNT(*)` needed:

```python
(CaseConversation.last_message_seq - CaseParticipant.last_read_message_seq).label("unread_count")
```

The handler passes `user_id` from the JWT; the client never sends it directly.

---

## Foundation isolation rule

```
Handler (router layer)
│  ├─ @jwt_required()
│  ├─ resolve entity (check existence + auth)
│  ├─ permission check
│  └─ ServiceContext(incoming_data=..., identity=get_jwt())
         │
         ▼
   Foundation service
         │  ├─ validates incoming_data keys
         │  ├─ DB read / write
         │  └─ returns Result dataclass
         │
         ▼
   Handler builds HTTP response
```

Foundation services never call `get_jwt()`, never access `request`, and never check user roles. Entity existence for CaseLink must be confirmed in the handler before `link_entity` is called.

---

## Integration with the image system (contract 43)

Cases and case conversation messages support images via the `ImageLink` polymorphic pattern — **no new models are needed**. The `ImageLinkEntityTypeEnum` in `domain/images/enums.py` already carries `CASE` and `CASE_CONVERSATION_MESSAGE`, and `confirm_upload`'s `_ENTITY_EVENT_MAP` already maps these to their event types.

The entire integration lives in the **handler layer**. The handler resolves the entity (existence + auth), then calls the existing image foundation services with `entity_type` and `entity_client_id` already populated. The image services have zero knowledge of cases.

### Handler pattern

```python
# Case image handler — entity resolved in handler, image service called unchanged
case = db.session.query(Case).filter_by(client_id=case_client_id).first()
if not case:
    raise NotFound(...)

data = request.get_json() or {}
data["entity_type"]   = "case"          # ImageLinkEntityTypeEnum.CASE.value
data["entity_client_id"] = case.client_id
data["created_by_id"] = identity.get("user_id")

outcome = run_service(generate_upload_url, ServiceContext(incoming_data=data, identity=identity))
```

The same pattern applies to `confirm_upload` and `list_images_for_entity`. For message images, replace `"case"` with `"case_conversation_message"` and resolve `CaseConversationMessage` instead.

### Upload flow for a case or message image

```
1. POST /api/v1/cases/<case_client_id>/images/upload-url
   Handler resolves case → calls generate_upload_url
   Returns: pending_upload_client_id + presigned S3 URL

2. Client uploads the file directly to S3 (no server involvement).

3. POST /api/v1/cases/<case_client_id>/images/confirm
   Handler resolves case → calls confirm_upload
   confirm_upload creates: Image + ImageLink(entity_type=CASE) + ImageEvent
```

Identical flow for messages, replacing the case URL prefix with `messages/<message_client_id>`.

### Extending to new entity types

When a new entity needs image support:
1. Add its value to `ImageLinkEntityTypeEnum` in `domain/images/enums.py`
2. Add its upload event type to `ImageEventTypeEnum`
3. Add the mapping to `_ENTITY_EVENT_MAP` in `services/commands/images/confirm_upload.py`
4. Add three handler routes in that entity's router (upload-url, confirm, list)

No changes to `Image`, `ImageLink`, `ImageAnnotation`, `ImageEvent`, or any image service are required.

---

## File structure

```
my_app/
├── domain/
│   └── cases/
│       ├── enums.py           # CaseLinkEntityTypeEnum, CaseLinkRoleEnum, CaseStateEnum
│       ├── events.py          # CaseEvent, ConversationMessageEvent str enums + extra builders
│       └── results.py         # CaseResult, CaseLinkResult, CaseParticipantResult, etc.
├── models/
│   └── tables/
│       └── cases/
│           ├── case_type.py
│           ├── case.py
│           ├── case_link.py
│           ├── case_participant.py
│           ├── case_conversation.py
│           └── case_conversation_message.py
└── services/
    ├── commands/
    │   └── cases/
    │       ├── create_case.py
    │       ├── update_case.py
    │       ├── update_case_state.py
    │       ├── link_entity.py
    │       ├── unlink_entity.py
    │       ├── add_participant.py
    │       ├── remove_participant.py
    │       ├── create_conversation.py
    │       ├── send_message.py
    │       ├── edit_message.py
    │       ├── soft_delete_message.py
    │       └── mark_read.py
    └── queries/
        └── cases/
            ├── get_case.py
            ├── list_cases.py
            ├── get_conversation.py
            ├── list_messages.py
            ├── list_participants.py
            ├── list_linked_entities.py
            └── get_unread_counts.py
```

---

## Response shapes

All case endpoints follow the adjacent-image pattern — images are never embedded in message objects; they are sideloaded at the top level of the response alongside the messages:

```json
// GET /api/v1/cases/<id>/conversations/<id>/messages
{
  "messages": [
    {
      "client_id": "ccm_...",
      "message_seq": 5,
      "content": [...],
      "plain_text": "...",
      "has_been_edited": false,
      "has_been_deleted": false,
      "created_at": "2024-01-01T00:00:00+00:00",
      "created_by": { "client_id": "usr_...", "username": "...", "role_name": "...", "online": true, "last_online": "...", "app_viewing": null }
    }
  ],
  "images": [
    {
      "entity_client_id": "ccm_...",   // links back to the message
      "link_client_id": "ilnk_...",
      "display_order": 0,
      "image": { "client_id": "img_...", "image_url": "...", ... }
    }
  ]
}

// GET /api/v1/cases/<id>/conversations/<id>
{
  "conversation": {
    "client_id": "ccv_...",
    "state": "open",
    "messages_count": 42,
    "last_message_seq": 42,
    "created_at": "...",
    "last_messages": [...]  // last 20 messages, same shape as above
  },
  "images": []  // populated by bootstrap_case_images.sh overwrite
}

// GET /api/v1/cases  (compact list)
{
  "cases": [
    {
      "client_id": "ca_...",
      "state": "open",
      "type_label": "...",
      "conversations_count": 3,
      "messages_count": 27,
      "created_at": "..."
    }
  ]
}
```

The frontend maintains a flat `Map<entity_client_id, ImageLinkResult[]>` indexed by `entity_client_id`. On render, each message resolves its images from this map — no N+1 round-trips.

---

## Rules

- **Extend `CaseLinkEntityTypeEnum` here when a new entity type gains case-link support.** Never pass raw strings as `entity_type`.
- **`type_label` must be set on case creation.** Populate from `CaseType.name` when `case_type_id` is supplied; store the user's custom string otherwise. Never leave it null unless the application explicitly permits unnamed cases.
- **`participants_count`, `conversations_count`, and `messages_count` are denormalized counters.** Never derive them from `COUNT(*)`. `messages_count` on `Case` mirrors the total across all conversations; `messages_count` on `CaseConversation` is per-thread. Both are decremented by `soft_delete_message`.
- **`message_seq` must be assigned atomically.** Always use the `UPDATE … RETURNING` pattern on `CaseConversation.last_message_seq`. Never compute it with `SELECT MAX(message_seq)`.
- **Images are sideloaded, never embedded.** `CaseConversationMessageResult` has no `images` field. The `list_messages` service (overwritten by `bootstrap_case_images.sh`) returns `{"messages": [...], "images": [...]}` where each image entry carries `entity_client_id` for frontend resolution.
- **`has_been_deleted` messages must not be hard-deleted.** The row is retained for `message_seq` continuity and audit. The serializer sets `content = None` and `plain_text = ""` for deleted messages.
- **State transitions are not enforced at the model layer.** `update_case_state` accepts any valid `CaseStateEnum` value. Transition guards belong in the handler or a domain policy module.
- **`add_participant` accepts a batch `user_ids` list and silently skips duplicates.** It computes the net-new set before inserting and increments `participants_count` only by that count, never by the full input length.
- **`mark_read` never decrements `last_read_message_seq`.** Only update when `up_to_message_seq > participant.last_read_message_seq`. This makes it idempotent on duplicate delivery.
- **`get_unread_counts` has two modes.** Pass `conversation_client_ids` for client-side inbox loading (returns all listed conversations, including those with 0 unread). Omit it for worker/badge queries (returns only conversations with unread > 0). Never use `COUNT(*)` — unread count is arithmetic on `last_message_seq − last_read_message_seq`.
- **`CaseConversation.state` reuses the `case_state_enum` Postgres type.** Use `create_type=False` on the `SAEnum` declaration to avoid a duplicate type error. `CaseType.entity_type` owns the `case_link_entity_type_enum` Postgres type; all other references use `create_type=False`.
- **`content_mentions` is not part of this foundation.** If the application defines a `ContentMention` model, add the relationship on `Case` and `CaseConversation` at the application layer.
- **`plain_text` is extracted from `content` at write time.** Never recompute it at read time. Handlers or a domain utility function extract it before calling `send_message` or `edit_message`.
