# 03 — Agent Identity Contract

## Why identity matters here

The backend service layer accepts any `ServiceContext` and executes the requested operation. It does not know whether the caller is a human via HTTP or an AI agent. This means the AI layer is entirely responsible for constructing a correct and honest `ServiceContext`.

An anonymous or spoofed context is a security hole. Every agent action must be traceable to a real actor.

---

## AgentContext

`AgentContext` is the AI layer's equivalent of an HTTP session. It carries the identity that will be injected into every `ServiceContext` the agent builds.

```python
# ai/agents/base.py
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    user_id: int | None          # Set for user-triggered agents
    service_account_id: str      # Always set — names the agent ("record_agent", "mcp_client")
    workspace_id: int
    session_id: str              # Unique per agent run — used for tracing and audit
    scopes: frozenset[str]       # Allowed tool names for this session
```

`AgentContext` is immutable. It is created once at the start of an agent run and passed through unchanged.

---

## Two actor types

### 1. User-delegated agent

The agent acts on behalf of a real user. The user authenticated via the normal JWT flow and then initiated an agent task.

```
User logs in → receives JWT → frontend triggers agent task →
agent layer extracts user_id and workspace_id from JWT →
builds AgentContext(user_id=42, workspace_id=7, service_account_id="record_agent", ...)
```

The `ServiceContext` built by the tool carries the user's real `user_id`. All permission checks, workspace isolation, and audit records reflect the user.

### 2. Service account agent

The agent runs autonomously — scheduled jobs, background workflows, system-initiated tasks. There is no human user in the session.

```
Scheduled trigger → agent starts with preconfigured credentials →
builds AgentContext(user_id=None, service_account_id="nightly_sync_agent", workspace_id=7, ...)
```

The `ServiceContext` carries `user_id=None` and an explicit `service_account_id`. The backend must be prepared to handle `user_id=None` in commands triggered by service accounts — the audit log records the `service_account_id` instead.

---

## Building ServiceContext from AgentContext

Every tool builds its own `ServiceContext`. The pattern is identical across all tools:

```python
from my_app.services.context import ServiceContext
from my_app.ai.agents.base import AgentContext


def _build_ctx(agent_ctx: AgentContext, incoming_data: dict) -> ServiceContext:
    return ServiceContext(
        user_id=agent_ctx.user_id,
        workspace_id=agent_ctx.workspace_id,
        actor=agent_ctx.service_account_id,  # always recorded
        incoming_data=incoming_data,
    )
```

The `actor` field is the explicit trace of who (or what) performed the operation. It is recorded in the audit log regardless of whether `user_id` is set.

---

## Scope enforcement

`AgentContext.scopes` contains the set of tool names this agent session is authorized to call. The agent runner enforces this before dispatching any tool call.

```python
# ai/agents/base.py (agent runner)

def dispatch_tool(tool_name: str, arguments: dict, agent_ctx: AgentContext) -> dict:
    if tool_name not in agent_ctx.scopes:
        raise ScopeViolationError(
            f"Tool '{tool_name}' is not in the allowed scopes for this session."
        )
    tool_fn = _tool_registry[tool_name]
    return tool_fn(arguments, agent_ctx)
```

Scopes are assigned at session creation, not at runtime. An agent cannot expand its own scope.

---

## Session ID

Every agent run is assigned a `session_id` (a UUID) at startup. This ID is:

- Attached to every `ServiceContext` built during the run.
- Recorded in every audit log entry.
- Stored in the observability trace.
- Returned to the caller so the run can be replayed or debugged.

```python
import uuid

session_id = str(uuid.uuid4())
agent_ctx = AgentContext(
    user_id=user_id,
    service_account_id="record_agent",
    workspace_id=workspace_id,
    session_id=session_id,
    scopes=frozenset(["create_record", "get_record", "list_records"]),
)
```

---

## MCP client identity

When an MCP client connects to the embedded MCP server, the server authenticates the client and constructs an `AgentContext` for the session. The MCP server is responsible for this — it is the trust boundary.

See [08_mcp_auth.md](08_mcp_auth.md) for how client credentials are validated and converted into `AgentContext`.

---

## Audit trail

Every write tool call must produce an audit log entry. This is enforced at the command level in the backend (see `Backend_architecture/36_audit_log.md`), but the AI layer must ensure the `actor` field is always populated so the audit log is meaningful.

A command audit entry triggered by an agent looks like:

```json
{
  "event": "record.created",
  "actor_user_id": 42,
  "actor_service_account": "record_agent",
  "session_id": "f3a1c2d4-...",
  "workspace_id": 7,
  "resource_id": "rec_abc123",
  "timestamp": "2026-01-15T10:30:00Z"
}
```

When `user_id` is `None` (service account), `actor_user_id` is omitted. `actor_service_account` is always present.

---

## What is forbidden

- Building `AgentContext` with both `user_id=None` and `service_account_id=None`. At least one must identify the actor.
- Hardcoding workspace IDs or user IDs in tool files.
- Sharing an `AgentContext` across two separate agent sessions.
- Bypassing scope enforcement by calling tool functions directly instead of through the agent runner's dispatch.
