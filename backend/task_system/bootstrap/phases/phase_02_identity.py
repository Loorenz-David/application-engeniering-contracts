from pathlib import Path

import typer

from bootstrap.writer import touch_file as _touch
from bootstrap.writer import write_file as _write


def _phase2(root: Path, a: str, force: bool) -> None:
    typer.echo("\n── Phase 2 — Identity & Foundational Models ─────────────────────────")

    # ── IdentityMixin ─────────────────────────────────────────────────────────
    _write(root / a / "models" / "base" / "identity.py", f"""\
from ulid import ULID

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column


def generate_id(prefix: str) -> str:
    \"\"\"Generate a prefixed ULID string — e.g. generate_id('usr') → 'usr_01ARZ...' \"\"\"
    return f"{{prefix}}_{{ULID()}}"


class IdentityMixin:
    \"\"\"Adds id (internal PK) and client_id (public ULID) to any model.

    Combine with Base and the model class:
        class MyModel(IdentityMixin, Base): ...
    \"\"\"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )

    # Subclasses declare CLIENT_ID_PREFIX = "xxx" and call generate_id() on insert.
    CLIENT_ID_PREFIX: str = ""
""", force=force)

    # ── HistoryRecord mixin ───────────────────────────────────────────────────
    _write(root / a / "models" / "base" / "history_record.py", """\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class HistoryRecord:
    \"\"\"Mixin for history/audit tables. Captures what changed, when, by whom, and why.

    Always combine with IdentityMixin:
        class MyHistoryRecord(IdentityMixin, HistoryRecord, Base): ...
    \"\"\"

    @declared_attr
    def updated_by_id(cls) -> Mapped[int]:
        return mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    from_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    to_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
""", force=force)

    # ── table directories ─────────────────────────────────────────────────────
    _touch(root / a / "models" / "tables" / "__init__.py", force=force)
    _touch(root / a / "models" / "tables" / "users" / "__init__.py", force=force)

    # ── User model ────────────────────────────────────────────────────────────
    _write(root / a / "models" / "tables" / "users" / "user.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class User(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "usr"
    __tablename__ = "users"

    # Timestamps & provenance
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    # Identity
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Localisation
    languages: Mapped[str | None] = mapped_column(String(512), nullable=True)
    language_preference: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Profile
    profile_picture: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Presence
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_online: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # FK shortcuts — updated atomically with the new child record
    last_app_view_record_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user_app_view_records.id"), nullable=True
    )
    last_history_record_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user_history_records.id"), nullable=True
    )

    # Role (wired in Phase 4)
    role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id"), nullable=True, index=True
    )

    # Relationships
    created_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by_id],
        primaryjoin="User.created_by_id == User.id",
    )
    app_view_records: Mapped[list["UserAppViewRecord"]] = relationship(
        "UserAppViewRecord",
        foreign_keys="[UserAppViewRecord.user_id]",
        back_populates="user",
    )
    user_history_records: Mapped[list["UserHistoryRecord"]] = relationship(
        "UserHistoryRecord",
        foreign_keys="[UserHistoryRecord.user_id]",
        back_populates="user",
    )
    last_app_view_record: Mapped["UserAppViewRecord | None"] = relationship(
        "UserAppViewRecord",
        foreign_keys=[last_app_view_record_id],
    )
    last_history_record: Mapped["UserHistoryRecord | None"] = relationship(
        "UserHistoryRecord",
        foreign_keys=[last_history_record_id],
    )
""", force=force)

    # ── UserAppViewRecord ─────────────────────────────────────────────────────
    _write(root / a / "models" / "tables" / "users" / "user_app_view_record.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class UserAppViewRecord(IdentityMixin, Base):
    \"\"\"One row per continuous visit a user spends viewing an entity.

    entity_type must be an EntityType enum value (defined in Phase 7).
    entity_client_id is None for list-page views — use workspace client_id instead.
    \"\"\"
    CLIENT_ID_PREFIX = "uavr"
    __tablename__ = "user_app_view_records"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="app_view_records",
    )
""", force=force)

    # ── UserHistoryRecord ─────────────────────────────────────────────────────
    _write(root / a / "models" / "tables" / "users" / "user_history_record.py", f"""\
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.history_record import HistoryRecord
from {a}.models.base.identity import IdentityMixin


class UserHistoryRecord(IdentityMixin, HistoryRecord, Base):
    CLIENT_ID_PREFIX = "uhr"
    __tablename__ = "user_history_records"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="user_history_records",
    )
    updated_by: Mapped["User"] = relationship(
        "User",
        foreign_keys="[UserHistoryRecord.updated_by_id]",
    )
""", force=force)

    # ── update models/__init__.py ─────────────────────────────────────────────
    models_init = root / a / "models" / "__init__.py"
    current = models_init.read_text(encoding="utf-8") if models_init.exists() else ""
    imports = (
        f"from {a}.models.tables.users import user  # noqa: F401\n"
        f"from {a}.models.tables.users import user_app_view_record  # noqa: F401\n"
        f"from {a}.models.tables.users import user_history_record  # noqa: F401\n"
    )
    if imports.strip() not in current:
        models_init.write_text(current + imports, encoding="utf-8")
        typer.echo(f"  update  {models_init}")
    else:
        typer.echo(f"  skip    {models_init} (already updated)")
