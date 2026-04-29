# 14 — Persistent Memory Contract

## Definition

Persistent memory is information about an agent's actions, decisions, and context that is stored in the database and can be retrieved in a future session. It is how an agent "remembers" across conversations.

Persistent memory is not a cache and not a log. It is a structured store of facts that are useful for future agent runs.

---

## What to store

Store information that:
- An agent would need to reconstruct context in a future session.
- Would be costly or impossible to re-derive from the application's domain data.
- The user expressed as a preference or decision that should persist.

| Store | Don't store |
|---|---|
| "User prefers to receive weekly summaries on Mondays" | Raw message history (use context manager) |
| "Workspace 7 completed onboarding on 2026-01-15" | Intermediate tool call results |
| Pending confirmation state (for HITL resume) | Data already in the domain models |
| User's stated working preferences | LLM-generated prose the user didn't ask to save |

---

## Memory model

```python
# models/tables/ai/agent_memory.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, JSON, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from my_app.models import Base


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Types: "preference", "decision", "pending_confirmation", "workflow_state", "fact"
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_agent_memories_workspace_agent_key", "workspace_id", "agent_name", "key"),
    )
```

The composite index on `(workspace_id, agent_name, key)` makes the most common retrieval pattern — "give me all memories for agent X in workspace Y" — fast without a full table scan.

---

## Memory types

| Type | Use | TTL |
|---|---|---|
| `preference` | User-expressed preferences that persist indefinitely | None (manual delete) |
| `fact` | Factual state about the workspace that the agent derived | None or domain-driven |
| `decision` | A significant decision made during an agent run | None |
| `workflow_state` | Partial state of an in-progress multi-step workflow | 7 days |
| `pending_confirmation` | HITL pause state — the agent is waiting for user confirmation | 48 hours |

Set `expires_at` for `workflow_state` and `pending_confirmation` records. Stale state that is never resumed should not accumulate.

---

## Read / write interface

```python
# ai/memory/persistent.py
from my_app.models.tables.ai.agent_memory import AgentMemory
from my_app.models import db
import uuid
from datetime import datetime, timedelta


def save_memory(
    workspace_id: int,
    agent_name: str,
    memory_type: str,
    key: str,
    value: dict,
    user_id: int | None = None,
    session_id: str | None = None,
    ttl_hours: int | None = None,
) -> None:
    expires_at = (
        datetime.utcnow() + timedelta(hours=ttl_hours)
        if ttl_hours else None
    )
    existing = (
        db.session.query(AgentMemory)
        .filter_by(workspace_id=workspace_id, agent_name=agent_name, key=key)
        .first()
    )
    if existing:
        existing.value = value
        existing.updated_at = datetime.utcnow()
        existing.expires_at = expires_at
    else:
        db.session.add(AgentMemory(
            client_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            user_id=user_id,
            agent_name=agent_name,
            memory_type=memory_type,
            key=key,
            value=value,
            session_id=session_id,
            expires_at=expires_at,
        ))
    db.session.commit()


def load_memory(
    workspace_id: int,
    agent_name: str,
    key: str,
) -> dict | None:
    record = (
        db.session.query(AgentMemory)
        .filter_by(workspace_id=workspace_id, agent_name=agent_name, key=key)
        .filter(
            (AgentMemory.expires_at == None) |
            (AgentMemory.expires_at > datetime.utcnow())
        )
        .first()
    )
    return record.value if record else None


def load_all_memories(
    workspace_id: int,
    agent_name: str,
    memory_type: str | None = None,
) -> list[dict]:
    query = (
        db.session.query(AgentMemory)
        .filter_by(workspace_id=workspace_id, agent_name=agent_name)
        .filter(
            (AgentMemory.expires_at == None) |
            (AgentMemory.expires_at > datetime.utcnow())
        )
    )
    if memory_type:
        query = query.filter_by(memory_type=memory_type)
    return [{"key": r.key, "value": r.value, "type": r.memory_type} for r in query.all()]
```

---

## Memory injection at session start

When an agent session starts, it loads relevant memories and prepends them to the message history as a system-level context block.

```python
# ai/agents/base.py

def build_memory_context(
    workspace_id: int,
    agent_name: str,
) -> str | None:
    memories = load_all_memories(workspace_id, agent_name)
    if not memories:
        return None
    lines = ["[Persistent memory — recalled from previous sessions]"]
    for m in memories:
        lines.append(f"- {m['key']}: {m['value']}")
    return "\n".join(lines)
```

This block is injected as the first `user` message in the history before the actual user request. The LLM reads it as prior context.

---

## Memory as a tool

Agents can write to persistent memory explicitly through a `save_memory_tool`. This allows the LLM to decide what is worth remembering.

```python
SCHEMA: dict = {
    "name": "save_memory",
    "description": (
        "Saves a piece of information to persistent memory for use in future sessions. "
        "Use this when the user states a preference, makes a significant decision, "
        "or when you learn something about the workspace that should be remembered."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "A short label for this memory (e.g., 'preferred_report_format').",
            },
            "value": {
                "type": "string",
                "description": "The content to remember.",
            },
        },
        "required": ["key", "value"],
    },
}
```

Agents should not save memory indiscriminately. The system prompt must instruct the agent to save memory only for explicit preferences, important decisions, or stated facts — not for every tool call result.

---

## Memory expiry and cleanup

A scheduled job runs nightly to delete expired memory records:

```python
# services/commands/ai/purge_expired_memories.py
def purge_expired_memories(ctx: ServiceContext) -> dict:
    count = (
        db.session.query(AgentMemory)
        .filter(AgentMemory.expires_at < datetime.utcnow())
        .delete()
    )
    db.session.commit()
    return {"purged_count": count}
```

Register this command as a scheduled job. See `Backend_architecture/37_scheduled_jobs.md`.

---

## What persistent memory must NOT store

- Raw conversation message history (use `ContextManager` for that).
- Data already modeled in domain tables (record titles, user names, workspace settings).
- Secrets, tokens, or credentials.
- PII that has not been explicitly offered by the user for memory ("remember my address").
- LLM reasoning chains or intermediate tool call results.
