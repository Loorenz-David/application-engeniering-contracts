# 04 — MCP Server Contract

## What MCP provides

The Model Context Protocol (MCP) is a standard interface that allows any MCP-compatible client (Claude Desktop, Claude Code, Cursor, custom agents) to connect to your application and call its capabilities as tools, read its data as resources, and use stored prompt templates.

The MCP server is the adapter between the MCP protocol and the AI layer's tool/resource/prompt definitions.

---

## Embedded model

The MCP server runs in the same Python process as the Flask application. It is not a separate service. This means:

- Tool handlers call `services/commands/` and `services/queries/` as direct Python function calls.
- The server shares the Flask app's database session factory, Redis pool, and config.
- No HTTP round-trip, no token forwarding, no network auth between MCP server and backend.

The MCP server is started alongside the Flask app in a background thread (or as a separate entrypoint for stdio transport). It is initialized inside the app factory.

---

## Folder structure

```
my_app/
└── ai/
    └── mcp/
        ├── server.py           # Server factory — creates and configures the MCP server instance
        ├── tools/              # MCP tool registrations (thin wrappers over ai/tools/)
        │   └── <domain>.py
        ├── resources/          # MCP resource handlers
        │   └── <domain>.py
        └── prompts/            # MCP prompt templates
            └── <workflow>.py
```

---

## Server factory (`ai/mcp/server.py`)

```python
from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.types as types

from my_app.ai.mcp.tools import record as record_tools
from my_app.ai.mcp.resources import record as record_resources
from my_app.ai.mcp.prompts import record_workflows


def create_mcp_server() -> Server:
    server = Server("my-app-mcp")

    record_tools.register(server)
    record_resources.register(server)
    record_workflows.register(server)

    return server
```

Each domain module owns its own registration. The factory only composes them.

---

## Capabilities

Declare only what the server actually implements. Do not advertise capabilities you haven't built.

```python
# Returned during MCP initialization handshake
capabilities = types.ServerCapabilities(
    tools=types.ToolsCapability(listChanged=False),
    resources=types.ResourcesCapability(subscribe=False, listChanged=False),
    prompts=types.PromptsCapability(listChanged=False),
)
```

Set `listChanged=True` only if your server emits notifications when the tool/resource/prompt list changes at runtime. Most embedded servers do not need this.

---

## Transport selection

| Transport | When to use |
|---|---|
| **stdio** | Claude Desktop, Claude Code, any local client. The MCP server is spawned as a subprocess. Most common for developer tooling. |
| **SSE (Server-Sent Events)** | Web-based MCP clients, remote connections over HTTP. Client connects to an HTTP endpoint and receives events. |
| **Streamable HTTP** | Newer MCP transport. Supports both streaming and non-streaming over HTTP. Prefer over SSE for new web integrations. |

For an embedded server used by Claude Desktop or Claude Code, use **stdio**. The client spawns the server process; the Flask app is not involved in the transport layer.

For a web-based agent that connects over the network, use **Streamable HTTP** — it is the current MCP standard for HTTP transports.

### stdio entrypoint

```python
# ai/mcp/server.py
import asyncio
from mcp.server.stdio import stdio_server


async def run_stdio():
    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="my-app-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(run_stdio())
```

Register this entrypoint in `pyproject.toml` as a CLI script so clients can reference it by name.

---

## Registration pattern

Each domain module provides a `register(server: Server)` function that attaches all handlers for that domain. Handlers are registered using the MCP server's decorator or handler-registration API.

```python
# ai/mcp/tools/record.py
from mcp.server import Server
import mcp.types as types

from my_app.ai.tools.record.create_record_tool import create_record_tool, SCHEMA as CREATE_SCHEMA
from my_app.ai.tools.record.get_record_tool import get_record_tool, SCHEMA as GET_SCHEMA
from my_app.ai.mcp.auth import resolve_agent_context


def register(server: Server) -> None:

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=CREATE_SCHEMA["name"],
                description=CREATE_SCHEMA["description"],
                inputSchema=CREATE_SCHEMA["input_schema"],
            ),
            types.Tool(
                name=GET_SCHEMA["name"],
                description=GET_SCHEMA["description"],
                inputSchema=GET_SCHEMA["input_schema"],
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        agent_ctx = resolve_agent_context()  # See 08_mcp_auth.md

        if name == CREATE_SCHEMA["name"]:
            result = create_record_tool(arguments, agent_ctx)
        elif name == GET_SCHEMA["name"]:
            result = get_record_tool(arguments, agent_ctx)
        else:
            raise ValueError(f"Unknown tool: {name}")

        return [types.TextContent(type="text", text=str(result))]
```

---

## Error handling

Map `DomainError` subclasses to MCP error responses. Do not let raw Python exceptions surface to the MCP client.

```python
from my_app.errors.base import DomainError
import mcp.types as types


def handle_tool_error(exc: Exception) -> list[types.TextContent]:
    if isinstance(exc, DomainError):
        return [types.TextContent(
            type="text",
            text=f"Error [{exc.code}]: {exc.message}",
        )]
    raise exc  # unexpected errors propagate to the MCP framework
```

---

## Lifecycle and Flask integration

The MCP server for stdio transport is started as a separate process — it does not need to be wired into Flask's startup. The entrypoint script initializes the Flask app context manually so it can access the database and config:

```python
# ai/mcp/server.py (stdio entrypoint)
import asyncio
from my_app import create_app

flask_app = create_app()

async def run_stdio():
    with flask_app.app_context():
        server = create_mcp_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, ...)
```

For SSE or Streamable HTTP transport, mount the MCP server as a route or ASGI sub-application alongside the Flask app.

---

## What the MCP server must NOT do

- Contain business logic.
- Call ORM models directly.
- Duplicate tool logic — MCP tool handlers call `ai/tools/` functions, they do not re-implement them.
- Expose internal system state (raw SQL results, stack traces, environment variables) to clients.
