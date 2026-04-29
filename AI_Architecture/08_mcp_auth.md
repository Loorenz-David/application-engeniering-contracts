# 08 — MCP Auth Contract

## The trust boundary

The MCP server is the trust boundary between external clients and the application. Every request that arrives at the MCP server must be authenticated before any tool, resource, or prompt handler executes.

The MCP server's job is to:
1. Validate the client's credentials.
2. Determine which workspace and user (or service account) the client represents.
3. Construct an `AgentContext` that all handlers use for the duration of the connection.
4. Enforce scope — which tools the client is authorized to call.

---

## Auth mechanisms

### API key (recommended for most integrations)

The client provides a static API key in the MCP initialization request or via an HTTP header (for SSE/Streamable HTTP transports).

API keys are stored hashed in the database. Each key is scoped to a workspace and an optional set of allowed tool names.

```python
# models/tables/ai/api_key.py
class AIApiKey(Base):
    __tablename__ = "ai_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String, nullable=False)   # bcrypt hash
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False)
    service_account_id: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "claude_desktop"
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### JWT (for user-delegated sessions)

The client presents a JWT issued by the backend's normal auth flow. The MCP server validates the JWT signature and extracts `user_id` and `workspace_id`.

This is used when a human user triggers an MCP session from the frontend and the agent must act on their behalf with their exact permissions.

### No anonymous access

The MCP server rejects any connection or request that does not present valid credentials. There is no guest or anonymous mode.

---

## `resolve_agent_context()`

This function is called at the start of every tool, resource, and prompt handler. It returns the `AgentContext` for the current connection session.

```python
# ai/mcp/auth.py
from contextvars import ContextVar
from my_app.ai.agents.base import AgentContext

_current_agent_ctx: ContextVar[AgentContext] = ContextVar("agent_ctx")


def set_agent_context(ctx: AgentContext) -> None:
    _current_agent_ctx.set(ctx)


def resolve_agent_context() -> AgentContext:
    try:
        return _current_agent_ctx.get()
    except LookupError:
        raise RuntimeError(
            "No AgentContext set. All MCP handlers must be called within an authenticated session."
        )
```

The context is set once during connection/session initialization, before any handler fires.

---

## Authentication flow (API key)

```python
# ai/mcp/auth.py
import bcrypt
from my_app.models.tables.ai.api_key import AIApiKey
from my_app.models import db
import uuid


def authenticate_api_key(raw_key: str) -> AgentContext:
    # Extract the key prefix to look up the record (avoids full-table scan)
    prefix = raw_key[:16]
    key_record = (
        db.session.query(AIApiKey)
        .filter(
            AIApiKey.client_id.startswith(prefix),
            AIApiKey.is_active == True,
        )
        .first()
    )

    if key_record is None:
        raise AuthenticationError("Invalid API key")

    if not bcrypt.checkpw(raw_key.encode(), key_record.key_hash.encode()):
        raise AuthenticationError("Invalid API key")

    if key_record.expires_at and key_record.expires_at < datetime.utcnow():
        raise AuthenticationError("API key has expired")

    # Update last_used_at (non-blocking — fire and forget is acceptable here)
    key_record.last_used_at = datetime.utcnow()
    db.session.commit()

    return AgentContext(
        user_id=None,
        service_account_id=key_record.service_account_id,
        workspace_id=key_record.workspace_id,
        session_id=str(uuid.uuid4()),
        scopes=frozenset(key_record.scopes),
    )
```

---

## Authentication flow (JWT)

```python
# ai/mcp/auth.py
from my_app.routers.utils.jwt_handler import decode_jwt


def authenticate_jwt(token: str) -> AgentContext:
    payload = decode_jwt(token)  # raises AuthenticationError on invalid/expired

    return AgentContext(
        user_id=payload["user_id"],
        service_account_id="jwt_session",
        workspace_id=payload["workspace_id"],
        session_id=str(uuid.uuid4()),
        scopes=frozenset(payload.get("agent_scopes", [])),
    )
```

Reuse the backend's existing JWT decode function. Do not duplicate JWT validation logic.

---

## Scope enforcement in MCP

Before dispatching any tool call, the MCP server verifies the requested tool name is in the session's scopes:

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    agent_ctx = resolve_agent_context()

    if name not in agent_ctx.scopes:
        return [types.TextContent(
            type="text",
            text=f"[FORBIDDEN] Tool '{name}' is not authorized for this session.",
        )]
    ...
```

Empty scopes (`frozenset()`) means no tools are accessible. If all tools should be accessible, the API key record must explicitly list every tool name, or the auth layer must use a sentinel value (`"*"`) that the dispatcher expands to all registered tools.

---

## Where credentials travel

For **stdio transport**: credentials are passed as environment variables or as part of the MCP initialization arguments. The server reads them at startup before any handlers fire.

For **SSE / Streamable HTTP transport**: credentials travel in the HTTP `Authorization` header on the initial connection request. The server authenticates before upgrading the connection.

Never accept credentials inside a tool's `arguments` dict. Credentials belong to the connection layer, not the request layer.

---

## Key rotation

API keys can be rotated without application restart. The new key is inserted into the database; the old key's `is_active` flag is set to `False`. The client switches to the new key. No deployment required.

When a key is deactivated, any in-flight session using that key continues until the session ends. The next connection attempt with the deactivated key is rejected.

---

## What MCP auth must NOT do

- Store raw API keys in the database — always store the hash.
- Log raw API keys in any log sink.
- Accept credentials in tool argument payloads.
- Grant scopes wider than what the API key record defines.
- Allow a client to change its own scopes mid-session.
