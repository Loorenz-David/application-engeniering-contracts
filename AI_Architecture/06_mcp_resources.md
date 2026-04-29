# 06 — MCP Resources Contract

## What resources are

Resources are read-only, structured data that the MCP server exposes to clients so they can read application state without calling a tool. A client (or the LLM) fetches a resource by URI; the server returns its content.

Resources are not tools. They do not take complex input arguments and they do not write to the database. They are analogous to GET endpoints that return application state.

Use resources when:
- The data is reference material the LLM needs to reason about (current workspace settings, available categories, the list of existing records).
- The data changes infrequently and reading it does not trigger side effects.
- The client needs to inspect state before deciding which tool to call.

Use tools when:
- The operation takes user-provided arguments.
- The operation writes, updates, or deletes data.
- The operation triggers a side effect (email, event, notification).

---

## Resource URI naming

Resources are identified by URI. Follow this naming convention:

```
<app-name>://<domain>/<entity>                   # collection
<app-name>://<domain>/<entity>/<id>              # single item
<app-name>://<domain>/<entity>/<id>/<sub>        # nested
```

Examples:
```
myapp://records/list
myapp://records/rec_abc123
myapp://workspace/settings
myapp://workspace/members
myapp://categories/list
```

Rules:
- Use the application's short name as the scheme.
- Use singular domain names where the resource is a single item, plural for collections.
- Use the entity's `client_id` (never the internal DB `id`) in the URI.
- Do not embed query parameters in URIs. Resources represent a defined slice of state, not a filtered query.

---

## Resource definition and registration

```python
# ai/mcp/resources/record.py
from mcp.server import Server
import mcp.types as types

from my_app.services.queries.record.list_records import list_records_query
from my_app.services.queries.record.get_record import get_record_query
from my_app.services.context import ServiceContext
from my_app.ai.mcp.auth import resolve_agent_context
import json


def register(server: Server) -> None:

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri="myapp://records/list",
                name="Records",
                description="All active records in the current workspace.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        agent_ctx = resolve_agent_context()

        if uri == "myapp://records/list":
            ctx = ServiceContext(
                user_id=agent_ctx.user_id,
                workspace_id=agent_ctx.workspace_id,
                actor=agent_ctx.service_account_id,
                incoming_data={},
            )
            result = list_records_query(ctx)
            return json.dumps(result, default=str)

        if uri.startswith("myapp://records/"):
            record_id = uri.removeprefix("myapp://records/")
            ctx = ServiceContext(
                user_id=agent_ctx.user_id,
                workspace_id=agent_ctx.workspace_id,
                actor=agent_ctx.service_account_id,
                incoming_data={"record_id": record_id},
            )
            result = get_record_query(ctx)
            return json.dumps(result, default=str)

        raise ValueError(f"Unknown resource URI: {uri}")
```

---

## Resource content types

| MIME type | When to use |
|---|---|
| `application/json` | Structured data (default for most resources) |
| `text/plain` | Simple string values, configuration text |
| `text/markdown` | Documentation, formatted instructions |

Always set `mimeType` in the `Resource` definition. Clients use it to decide how to render or parse the content.

---

## Dynamic resource URIs

If resource URIs are not known at registration time (e.g., one resource per workspace record), use resource templates:

```python
@server.list_resource_templates()
async def list_resource_templates() -> list[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            uriTemplate="myapp://records/{record_id}",
            name="Record detail",
            description="Full detail for a specific record, identified by its client_id.",
            mimeType="application/json",
        ),
    ]
```

The client fills in `{record_id}` and calls `read_resource` with the resolved URI. The handler parses the URI and looks up the record.

---

## What resources must NOT do

- Write to the database.
- Emit events or trigger side effects.
- Accept complex filter arguments (that is a tool's job).
- Return paginated results — if the data set is large, expose a tool instead of a resource.
- Expose internal IDs, raw SQL rows, or stack traces.

---

## Resources vs tools decision table

| Need | Use |
|---|---|
| LLM needs to know what categories exist | Resource |
| LLM needs to filter records by date range | Tool (list query with arguments) |
| LLM needs workspace name and timezone | Resource |
| LLM needs to read a specific record before updating it | Resource |
| LLM needs to create a record | Tool |
| LLM needs a sorted, paginated list | Tool |
