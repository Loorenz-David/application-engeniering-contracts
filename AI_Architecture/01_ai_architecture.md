# 01 — AI Architecture

## Layer map

```
MCP Client (Claude Desktop, Claude Code, any MCP client)
     │
     │  MCP Protocol (stdio / SSE / Streamable HTTP)
     ▼
┌──────────────────┐
│   MCP Server     │  Embedded in the Flask app — tools, resources, prompts
└────────┬─────────┘
         │  direct Python call (no HTTP hop)
         ▼
┌──────────────────┐
│   Agent Layer    │  Provider adapter → LLM → tool loop → result
│   (ai/)          │  Single agents, orchestrators, subagents, memory
└────────┬─────────┘
         │  ServiceContext
         ▼
┌──────────────────┐
│  Service Layer   │  Commands (write) and Queries (read)
│  (Backend)       │  Unchanged — has no knowledge of the agent calling it
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Domain / Models │  Pure business logic and ORM
└──────────────────┘
```

The AI layer sits entirely **above** the service layer. It is a consumer of the backend, not a part of it. The backend never imports from `ai/`.

---

## Hard dependency rules

| Layer | May import | Must NOT import |
|---|---|---|
| `ai/mcp/` | `ai/tools/`, `services/commands/*`, `services/queries/*`, `services/context`, `errors/` | `routers/`, `ai/agents/`, `ai/memory/` |
| `ai/agents/` | `ai/tools/`, `ai/providers/`, `ai/memory/`, `services/context`, `errors/` | `routers/`, `ai/mcp/` |
| `ai/tools/` | `services/commands/*`, `services/queries/*`, `services/context`, `errors/` | `ai/agents/`, `ai/mcp/`, `routers/` |
| `ai/providers/` | stdlib, provider SDKs only | Everything in `services/`, `ai/tools/`, `ai/agents/` |
| `ai/memory/` | `models/`, `services/queries/*`, `services/context`, `errors/` | `ai/agents/`, `ai/mcp/`, `routers/` |

If a lower layer needs to call something in the AI layer, the design is wrong. Invert the dependency or introduce an event.

---

## Folder structure

```
my_app/
├── ai/
│   ├── __init__.py
│   ├── providers/              # LLM provider adapters (model-agnostic interface)
│   │   ├── base.py             # LLMProvider protocol + shared types
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   └── google_provider.py
│   ├── tools/                  # Tool functions, grouped by domain
│   │   └── <domain>/
│   │       ├── create_<entity>_tool.py
│   │       └── get_<entity>_tool.py
│   ├── agents/                 # Agent definitions
│   │   ├── base.py             # AgentRunner — the provider-agnostic loop
│   │   └── <agent_name>/
│   │       ├── agent.py        # Agent config: provider, tools, memory, system prompt path
│   │       └── system_prompt.md
│   ├── memory/                 # Memory implementations
│   │   ├── context.py          # In-session context window management
│   │   ├── persistent.py       # DB-backed long-term memory
│   │   └── semantic.py         # Embeddings + vector search
│   └── mcp/                    # MCP server (embedded)
│       ├── server.py           # Server factory + registration
│       ├── tools/              # MCP tool registrations (thin wrappers over ai/tools/)
│       │   └── <domain>.py
│       ├── resources/          # MCP resource handlers
│       │   └── <domain>.py
│       └── prompts/            # MCP prompt templates
│           └── <workflow>.py
```

### Domain grouping rule

AI layer files follow the same domain grouping as the backend:

```
ai/tools/<domain>/          ←→   services/commands/<domain>/
ai/mcp/tools/<domain>.py    ←→   routers/api_v1/<domain>.py
```

Trace a domain vertically through both the backend and AI layers.

---

## Technology stack

This contract is model-agnostic. The AI layer uses a **provider adapter pattern** to isolate all LLM-provider-specific code behind a common interface.

### Provider interface (`ai/providers/base.py`)

```python
from typing import Protocol, Iterator
from dataclasses import dataclass


@dataclass
class Message:
    role: str          # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    stop_reason: str   # "end_turn" | "tool_use" | "max_tokens"


@dataclass
class LLMConfig:
    model: str
    max_tokens: int = 4096
    temperature: float = 0.0
    system_prompt: str | None = None


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[Message],
        tools: list[dict],
        config: LLMConfig,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        config: LLMConfig,
    ) -> Iterator[str]: ...
```

### MCP server

Use the official Python MCP SDK (`mcp`) or FastMCP. Both implement the MCP specification and are transport-agnostic. Choose based on the features you need — FastMCP provides higher-level decorators; the official SDK provides more control.

### Embedding model (semantic memory)

Use any embedding provider that exposes a `(text: str) -> list[float]` interface. Wrap it in the same adapter pattern used for LLM providers. Do not call embedding APIs directly from memory or tool code.

### Vector store

Use any vector store that supports cosine similarity search (pgvector, Chroma, Qdrant, Weaviate, Pinecone). Wrap in an adapter. The rest of the codebase never imports the vector store SDK directly.

---

## Embedded MCP server — why no HTTP hop

The MCP server is a separate process in terms of protocol transport (the client connects via stdio, SSE, or HTTP), but the server process **runs inside the same Python environment as the Flask app**. This means:

- MCP tools call `services/commands/` and `services/queries/` as direct Python function calls.
- No serialization round-trip, no network latency, no auth token forwarding.
- The server shares the same SQLAlchemy session factory, Redis pool, and config as the Flask app.

This is the embedded model. The MCP server is started alongside the Flask app, not as an independent service.

---

## What is NOT in scope for this contract

- Backend application structure — governed by [`Backend_architecture/`](../Backend_architecture/README.md)
- Frontend code
- Infrastructure provisioning (Docker, Terraform, cloud config)
- LLM provider pricing or model selection decisions
