# 02 — Tool Contract

## Definition

A tool is a Python function that exposes one backend operation — a command or a query — to an LLM. It is the boundary between the AI layer and the service layer.

A tool does exactly four things:
1. Validates and parses its input arguments.
2. Builds a `ServiceContext` using the agent's identity.
3. Calls one backend command or query via `run_service`.
4. Returns a structured result dict.

Nothing else belongs in a tool.

---

## File structure

One tool = one file. The file is named after the operation it exposes:

```
ai/tools/<domain>/
├── create_record_tool.py
├── update_record_tool.py
├── get_record_tool.py
├── list_records_tool.py
└── delete_record_tool.py
```

Tools that require private helpers prefix those files with `_`:

```
ai/tools/<domain>/
├── create_record_tool.py
├── _build_record_payload.py     # used only by create_record_tool
```

---

## Tool function signature

```python
def create_record_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    ...
```

**Rules:**
- `arguments: dict` — the raw dict the LLM passed. The tool parses and validates it.
- `agent_ctx: AgentContext` — carries the caller's identity. Used to build `ServiceContext`.
- Return type is always `dict`. Never return a string, a model object, or `None`.
- The function name ends in `_tool`. This makes it unambiguous at call sites.

---

## JSON Schema definition

Every tool must ship a companion JSON Schema that the LLM uses to generate arguments. The schema lives in the same file as the tool function.

```python
# ai/tools/record/create_record_tool.py
from my_app.services.commands.record.create_record import create_record
from my_app.services.context import ServiceContext
from my_app.ai.agents.base import AgentContext
from my_app.errors.validation import ValidationError


SCHEMA: dict = {
    "name": "create_record",
    "description": (
        "Creates a new record in the workspace. "
        "Use this when the user asks to add, create, or register a new record."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The display name for the new record.",
            },
            "category": {
                "type": "string",
                "enum": ["type_a", "type_b", "type_c"],
                "description": "The category this record belongs to.",
            },
            "notes": {
                "type": "string",
                "description": "Optional free-text notes.",
            },
        },
        "required": ["title", "category"],
    },
}


def create_record_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    title = arguments.get("title")
    category = arguments.get("category")

    if not title or not isinstance(title, str):
        raise ValidationError("title is required and must be a string")
    if category not in ("type_a", "type_b", "type_c"):
        raise ValidationError(f"category must be one of type_a, type_b, type_c")

    ctx = ServiceContext(
        user_id=agent_ctx.user_id,
        workspace_id=agent_ctx.workspace_id,
        incoming_data={
            "title": title.strip(),
            "category": category,
            "notes": arguments.get("notes", ""),
        },
    )

    return create_record(ctx)
```

---

## Schema field rules

| Field | Rule |
|---|---|
| `name` | Snake case. Verb + noun. No domain prefix. `create_record`, not `record_create` or `domain_create_record`. |
| `description` | Plain English. State what the tool does AND when the LLM should choose it. Include synonyms for the action ("add, create, register"). |
| `input_schema` | Full JSON Schema object. Always include `"required"`. Never use `"type": "any"` or omit the `"type"` field on a property. |
| Property descriptions | One sentence. The LLM reads these to fill in values. Be explicit about format ("ISO 8601 date string", "the record's `client_id`"). |

---

## One tool = one backend operation

| Correct | Wrong |
|---|---|
| `create_record_tool` calls `create_record` | `create_and_notify_tool` calls `create_record` then `send_notification` |
| `delete_record_tool` calls `delete_record` | `archive_or_delete_tool` branches internally based on a flag |
| `get_record_tool` calls `get_record_query` | `get_record_tool` queries the ORM directly |

If a user intent requires two operations, that is an agent-level concern. The agent calls two tools in sequence. The tools stay single-purpose.

---

## Error handling

Tools propagate `DomainError` subclasses unchanged. The agent runner and MCP server each handle error-to-response mapping at their own layer.

```python
# Tools do NOT catch DomainError — let it propagate.
# Tools DO catch unexpected exceptions and wrap them.

try:
    return some_command(ctx)
except DomainError:
    raise  # propagate cleanly
except Exception as exc:
    raise ToolExecutionError(f"Unexpected error in create_record_tool: {exc}") from exc
```

Never return an error as a dict key like `{"error": "..."}`. Errors are exceptions, not return values.

---

## Tool return format

Write commands return the canonical representation of the affected entity — same as the backend command returns:

```python
# Write tool return
{
    "id": "rec_abc123",
    "title": "My Record",
    "category": "type_a",
    "status": "active",
    "created_at": "2026-01-15T10:30:00Z",
}
```

Read tools (queries) return the query result directly:

```python
# List query return
{
    "items": [...],
    "total": 42,
    "page": 1,
    "per_page": 20,
}
```

The LLM uses the return value to decide the next step or to compose its response. Keep it flat and self-describing.

---

## Naming conventions

| Pattern | Example |
|---|---|
| Tool file | `<verb>_<entity>_tool.py` |
| Tool function | `<verb>_<entity>_tool` |
| Schema `name` field | `<verb>_<entity>` (no `_tool` suffix — this is what the LLM sees) |
| Domain folder | Same as the backend domain name |

The `name` in the schema is what the LLM uses in its tool call. It must be unique across all tools registered in the same agent or MCP server.

---

## Tool registration

Tools are not registered globally. Each agent and each MCP server declares the exact set of tools it exposes. This is the scope-bounding mechanism — an agent only has access to what it is given.

```python
# ai/agents/record_agent/agent.py
from my_app.ai.tools.record.create_record_tool import create_record_tool, SCHEMA as CREATE_SCHEMA
from my_app.ai.tools.record.get_record_tool import get_record_tool, SCHEMA as GET_SCHEMA

TOOLS = [
    (CREATE_SCHEMA, create_record_tool),
    (GET_SCHEMA, get_record_tool),
]
```

The agent runner iterates `TOOLS` to build the schema list sent to the LLM and to dispatch tool calls by name.

---

## What tools must NOT do

- Import from `routers/` or `ai/mcp/`
- Query the database directly (no SQLAlchemy imports in tool files)
- Call more than one backend command or query
- Contain conditional business logic (`if workspace_type == "premium"`)
- Modify `arguments` in-place before validation
- Call external HTTP APIs directly — go through the backend's infrastructure adapters
