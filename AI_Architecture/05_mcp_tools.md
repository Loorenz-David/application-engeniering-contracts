# 05 — MCP Tools Contract

## Relationship to ai/tools/

MCP tools are thin registration wrappers. The implementation lives in `ai/tools/<domain>/`. The MCP layer's job is to:

1. Advertise the tool's schema to MCP clients via `list_tools`.
2. Dispatch `call_tool` requests to the correct `ai/tools/` function.
3. Map errors to MCP-compatible responses.
4. Return results as `TextContent` or `EmbeddedResource`.

Do not duplicate logic. If you find yourself writing business logic in an MCP tool handler, move it to `ai/tools/<domain>/` instead.

---

## One MCP tool = one ai/tool

| MCP tool name | Calls |
|---|---|
| `create_record` | `ai/tools/record/create_record_tool.py` |
| `get_record` | `ai/tools/record/get_record_tool.py` |
| `list_records` | `ai/tools/record/list_records_tool.py` |

If a new backend command or query is added, a corresponding `ai/tools/` function is created first, then optionally exposed as an MCP tool.

---

## Tool naming

MCP tool names are the `name` field from the tool's `SCHEMA` dict. They follow the same convention as the underlying tool function — snake case, verb + noun — without the `_tool` suffix:

| `ai/tools/` file | Schema `name` | MCP tool name |
|---|---|---|
| `create_record_tool.py` | `create_record` | `create_record` |
| `list_records_tool.py` | `list_records` | `list_records` |
| `approve_request_tool.py` | `approve_request` | `approve_request` |

Tool names must be unique across all domains registered in the same MCP server. If two domains have operations with the same verb+noun, qualify the name with the domain: `record_create` vs `invoice_create`.

---

## Full registration example

```python
# ai/mcp/tools/record.py
from mcp.server import Server
import mcp.types as types

from my_app.ai.tools.record.create_record_tool import create_record_tool, SCHEMA as CREATE
from my_app.ai.tools.record.get_record_tool import get_record_tool, SCHEMA as GET
from my_app.ai.tools.record.list_records_tool import list_records_tool, SCHEMA as LIST
from my_app.ai.tools.record.update_record_tool import update_record_tool, SCHEMA as UPDATE
from my_app.ai.tools.record.delete_record_tool import delete_record_tool, SCHEMA as DELETE
from my_app.ai.mcp.auth import resolve_agent_context
from my_app.errors.base import DomainError

_TOOLS = {
    CREATE["name"]: (CREATE, create_record_tool),
    GET["name"]: (GET, get_record_tool),
    LIST["name"]: (LIST, list_records_tool),
    UPDATE["name"]: (UPDATE, update_record_tool),
    DELETE["name"]: (DELETE, delete_record_tool),
}


def register(server: Server) -> None:

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=schema["name"],
                description=schema["description"],
                inputSchema=schema["input_schema"],
            )
            for schema, _ in _TOOLS.values()
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict
    ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        if name not in _TOOLS:
            raise ValueError(f"Unknown tool: {name}")

        agent_ctx = resolve_agent_context()
        _, tool_fn = _TOOLS[name]

        try:
            result = tool_fn(arguments, agent_ctx)
            return [types.TextContent(type="text", text=_serialize(result))]
        except DomainError as exc:
            return [types.TextContent(
                type="text",
                text=f"[{exc.code}] {exc.message}",
            )]
```

---

## Result serialization

MCP tools must return a list of content blocks. For structured data, serialize to a JSON string inside a `TextContent` block. Do not return raw Python dicts.

```python
import json

def _serialize(result: dict) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)
```

Use `default=str` to handle datetimes and other non-serializable types that the backend serializer may have left in. Never let a serialization error surface to the MCP client.

---

## Error mapping

| Error type | MCP response |
|---|---|
| `NotFoundError` | `TextContent` with `[NOT_FOUND] <message>` |
| `PermissionError` | `TextContent` with `[FORBIDDEN] <message>` |
| `ValidationError` | `TextContent` with `[VALIDATION] <message>` |
| `ConflictError` | `TextContent` with `[CONFLICT] <message>` |
| Unexpected `Exception` | Re-raise — MCP framework converts to protocol error |

Error content blocks use a `[CODE] message` prefix so the LLM can distinguish error responses from success responses without parsing JSON.

---

## Dangerous tool annotation

Tools that perform irreversible operations (delete, send, publish) must be annotated so the MCP server and any calling agent can apply a confirmation gate. Add a `"dangerous": true` key to the tool's `SCHEMA`:

```python
SCHEMA: dict = {
    "name": "delete_record",
    "description": "Permanently deletes a record. This cannot be undone.",
    "dangerous": True,
    "input_schema": { ... },
}
```

The MCP server reads this flag during dispatch and calls the confirmation gate before executing. See [17_safety_guardrails.md](17_safety_guardrails.md) for the confirmation gate contract.

---

## Tool description quality

The description field is the most important field in the schema. It determines whether the LLM chooses the right tool. A weak description produces wrong tool selections.

**Weak:**
```
"description": "Creates a record."
```

**Strong:**
```
"description": (
    "Creates a new record in the current workspace. "
    "Use this when the user says they want to add, create, register, or submit a new record. "
    "Do not use this to update an existing record — use update_record instead."
)
```

Rules for descriptions:
- State the primary action clearly.
- List common user phrasings ("add", "create", "register").
- State when NOT to use this tool if a similar tool exists.
- Mention required context ("the workspace must already exist").
- Keep it under 3 sentences.

---

## What MCP tool handlers must NOT do

- Contain `if/else` branching over business state.
- Access the database directly.
- Call more than one `ai/tools/` function per handler dispatch.
- Return unstructured prose (e.g., `"The record was successfully created."`) — return the structured result dict and let the LLM narrate.
- Swallow errors silently.
