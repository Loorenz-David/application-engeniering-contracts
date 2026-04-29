# 18 — Observability Contract

## What to observe

Every LLM call and every tool call generates observable events. These events answer:
- What did the agent do, in what order?
- How many tokens were used and at what cost?
- How long did each step take?
- Did the agent fail, and why?
- Who triggered the session and what was the outcome?

---

## Log levels for the AI layer

| Level | Use |
|---|---|
| `INFO` | Session started, session ended, tool called, tool result received |
| `WARNING` | Max iterations reached, context summarized, injection pattern detected, scope violation attempt |
| `ERROR` | Tool raised unexpected exception, LLM call failed, agent returned no result |

Never log at `DEBUG` in production without a feature flag — LLM call payloads can be large.

---

## Agent session event: session started

Logged when an agent run begins.

```python
logger.info(
    "agent_session_started",
    extra={
        "event": "agent.session.started",
        "agent_name": config.name,
        "session_id": agent_ctx.session_id,
        "workspace_id": agent_ctx.workspace_id,
        "user_id": agent_ctx.user_id,
        "service_account_id": agent_ctx.service_account_id,
        "tool_count": len(config.tools),
    }
)
```

---

## LLM call event

Logged after every LLM API response is received.

```python
# ai/agents/base.py — in the tool loop

def _log_llm_call(
    agent_name: str,
    session_id: str,
    iteration: int,
    response: LLMResponse,
    latency_ms: int,
) -> None:
    logger.info(
        "llm_call_completed",
        extra={
            "event": "agent.llm.call",
            "agent_name": agent_name,
            "session_id": session_id,
            "iteration": iteration,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.input_tokens + response.output_tokens,
            "stop_reason": response.stop_reason,
            "tool_calls_requested": len(response.tool_calls),
            "latency_ms": latency_ms,
        }
    )
```

---

## Tool call event

Logged after every tool call completes (success or failure).

```python
def _log_tool_call(
    agent_name: str,
    session_id: str,
    tool_name: str,
    success: bool,
    latency_ms: int,
    error_code: str | None = None,
) -> None:
    logger.info(
        "tool_call_completed",
        extra={
            "event": "agent.tool.call",
            "agent_name": agent_name,
            "session_id": session_id,
            "tool_name": tool_name,
            "success": success,
            "latency_ms": latency_ms,
            "error_code": error_code,
        }
    )
```

Never log tool `arguments` at INFO level — they may contain PII. Log them at DEBUG level only, and only when a debug flag is enabled.

---

## Agent session event: session ended

Logged when the agent run completes (success, failure, or max_iterations).

```python
logger.info(
    "agent_session_ended",
    extra={
        "event": "agent.session.ended",
        "agent_name": config.name,
        "session_id": agent_ctx.session_id,
        "workspace_id": agent_ctx.workspace_id,
        "outcome": "success" | "failed" | "max_iterations",
        "total_iterations": iteration_count,
        "total_input_tokens": accumulated_input_tokens,
        "total_output_tokens": accumulated_output_tokens,
        "total_latency_ms": total_latency_ms,
    }
)
```

---

## Token and cost tracking (DB)

Token usage is stored in the database for billing, audit, and usage analysis.

```python
# models/tables/ai/agent_session_log.py
class AgentSessionLog(Base):
    __tablename__ = "agent_session_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    service_account_id: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # success | failed | max_iterations
    total_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Write this record at the end of every agent session via a command:

```python
# services/commands/ai/log_agent_session.py
def log_agent_session(ctx: ServiceContext) -> dict:
    ...  # standard command pattern — writes AgentSessionLog
```

The command is called by the `AgentRunner` after the loop exits, regardless of outcome.

---

## MCP server observability

Log every MCP client request at the server level:

```python
# ai/mcp/server.py — middleware

def _log_mcp_request(tool_name: str, agent_ctx: AgentContext, latency_ms: int, success: bool) -> None:
    logger.info(
        "mcp_tool_call",
        extra={
            "event": "mcp.tool.call",
            "tool_name": tool_name,
            "session_id": agent_ctx.session_id,
            "workspace_id": agent_ctx.workspace_id,
            "service_account_id": agent_ctx.service_account_id,
            "latency_ms": latency_ms,
            "success": success,
        }
    )
```

---

## Context summarization event

Logged when `ContextManager.fit()` triggers a summarization to prevent silent data loss.

```python
logger.warning(
    "context_summarized",
    extra={
        "event": "agent.context.summarized",
        "agent_name": agent_name,
        "session_id": session_id,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "messages_summarized": messages_removed_count,
    }
)
```

---

## Alerting rules

| Alert | Trigger | Severity |
|---|---|---|
| High token usage | Single session exceeds 50k tokens | WARNING |
| Max iterations hit | `outcome == "max_iterations"` | WARNING |
| Tool error rate | >10% of tool calls in a 5-min window return errors | ERROR |
| LLM latency | P95 LLM call latency > 10s | WARNING |
| Injection detected | Any session triggers injection pattern warning | ERROR |
| Scope violation | Any scope violation attempt logged | ERROR |

---

## What must NOT be logged

- Raw user messages or agent responses at INFO level (may contain PII).
- Raw tool arguments (may contain PII or sensitive business data).
- API keys or JWT tokens in any log field.
- LLM response content at any level outside of explicit debug mode.
- Full system prompts (they may contain confidential domain logic).

Adhere to the same logging constraints as the backend: see `Backend_architecture/17_logging.md`.
