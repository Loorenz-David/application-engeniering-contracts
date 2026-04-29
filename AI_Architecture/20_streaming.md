# 20 — Streaming Contract

## The problem

The current `AgentRunner.run()` is synchronous: it runs the full tool loop and returns an `AgentResult` only when the loop finishes. For any task that requires more than one or two LLM calls, the user stares at a spinner for several seconds — or minutes. Streaming solves this by emitting observable events as the agent works, so the interface can show progress in real time.

Streaming in an agentic context is different from streaming a plain LLM response. The agent loop has multiple phases — LLM thinking, tool execution, result processing — and each phase emits its own event type.

---

## Event model

Every observable moment in the agent loop is represented as a typed event. The `AgentRunner` emits these events in order; callers consume them via a generator.

```python
# ai/agents/base.py
from dataclasses import dataclass, field
from typing import Literal, Iterator


@dataclass
class StreamEvent:
    type: Literal[
        "session_start",       # agent run began
        "iteration_start",     # new LLM call starting
        "content_delta",       # partial text from the LLM (final response only)
        "tool_call_start",     # LLM decided to call a tool
        "tool_executing",      # tool function is running
        "tool_complete",       # tool returned a result
        "context_summarized",  # context window was reduced
        "clarification",       # agent paused — needs input
        "session_complete",    # agent finished successfully
        "session_failed",      # agent failed
        "session_max_iter",    # agent hit max_iterations
    ]
    session_id: str
    data: dict = field(default_factory=dict)
```

### Event payloads

| Event | `data` fields |
|---|---|
| `session_start` | `agent_name`, `workspace_id`, `user_id` |
| `iteration_start` | `iteration` (int) |
| `content_delta` | `delta` (str — partial text chunk) |
| `tool_call_start` | `tool_name`, `arguments` (dict) |
| `tool_executing` | `tool_name` |
| `tool_complete` | `tool_name`, `success` (bool), `latency_ms` (int) |
| `context_summarized` | `tokens_before`, `tokens_after` |
| `clarification` | `question`, `clarification_type`, `referenced_data`, `options` |
| `session_complete` | `content` (final response), `total_iterations`, `total_input_tokens`, `total_output_tokens` |
| `session_failed` | `error` |
| `session_max_iter` | `total_iterations` |

---

## `AgentRunner.stream()` — the streaming loop

```python
# ai/agents/base.py

class AgentRunner:

    def stream(
        self,
        user_message: str,
        agent_ctx: AgentContext,
    ) -> Iterator[StreamEvent]:
        yield StreamEvent(
            type="session_start",
            session_id=agent_ctx.session_id,
            data={
                "agent_name": self.config.name,
                "workspace_id": agent_ctx.workspace_id,
                "user_id": agent_ctx.user_id,
            },
        )
        messages = [Message(role="user", content=user_message)]
        yield from self._stream_loop(messages, agent_ctx)

    def stream_with_history(
        self,
        history: list[Message],
        answer: str,
        agent_ctx: AgentContext,
    ) -> Iterator[StreamEvent]:
        resumed = history + [Message(role="user", content=f"[Clarification answer]\n{answer}")]
        yield from self._stream_loop(resumed, agent_ctx)

    def _stream_loop(
        self,
        messages: list[Message],
        agent_ctx: AgentContext,
    ) -> Iterator[StreamEvent]:
        llm_config = self._build_llm_config()

        for iteration in range(self.config.max_iterations):
            yield StreamEvent(
                type="iteration_start",
                session_id=agent_ctx.session_id,
                data={"iteration": iteration + 1},
            )

            # Stream the LLM call — content_delta events during final response
            response, deltas = self._stream_llm_call(messages, llm_config)
            yield from deltas  # zero or more content_delta events

            _log_llm_call(self.config.name, agent_ctx.session_id, iteration, response)

            if response.stop_reason == "end_turn":
                yield StreamEvent(
                    type="session_complete",
                    session_id=agent_ctx.session_id,
                    data={
                        "content": response.content or "",
                        "total_iterations": iteration + 1,
                        "total_input_tokens": self._accumulated_input_tokens,
                        "total_output_tokens": self._accumulated_output_tokens,
                    },
                )
                return

            if response.stop_reason == "tool_use":
                messages.append(Message(role="assistant", content=response.content or "", tool_calls=[...]))
                for tool_call in response.tool_calls:
                    yield StreamEvent(
                        type="tool_call_start",
                        session_id=agent_ctx.session_id,
                        data={"tool_name": tool_call.name, "arguments": tool_call.arguments},
                    )
                    yield StreamEvent(
                        type="tool_executing",
                        session_id=agent_ctx.session_id,
                        data={"tool_name": tool_call.name},
                    )
                    try:
                        result = self._dispatch(tool_call, agent_ctx)
                    except _ClarificationSignal as signal:
                        yield StreamEvent(
                            type="clarification",
                            session_id=agent_ctx.session_id,
                            data={
                                "question": signal.data["question"],
                                "clarification_type": signal.data["clarification_type"],
                                "referenced_data": signal.data.get("referenced_data", {}),
                                "options": signal.data.get("options", []),
                                "conversation_history": messages[:],
                            },
                        )
                        return
                    yield StreamEvent(
                        type="tool_complete",
                        session_id=agent_ctx.session_id,
                        data={"tool_name": tool_call.name, "success": True, "latency_ms": 0},
                    )
                    messages.append(Message(role="tool", content=str(result), tool_call_id=tool_call.id))
                continue

            break

        yield StreamEvent(
            type="session_max_iter",
            session_id=agent_ctx.session_id,
            data={"total_iterations": self.config.max_iterations},
        )
```

---

## Provider streaming — `_stream_llm_call`

Content deltas are emitted **only during the final response turn** (when the LLM produces prose for the user). During tool-use turns the LLM is producing function call arguments, not user-facing text — those are not streamed as `content_delta`.

```python
def _stream_llm_call(
    self,
    messages: list[Message],
    llm_config: LLMConfig,
) -> tuple[LLMResponse, Iterator[StreamEvent]]:
    chunks = []
    content_so_far = []
    tool_calls_so_far = []

    def _generate_deltas() -> Iterator[StreamEvent]:
        for chunk in self.provider.stream(messages, self._tool_schemas, llm_config):
            if chunk.type == "content_delta":
                content_so_far.append(chunk.delta)
                yield StreamEvent(
                    type="content_delta",
                    session_id=self._current_session_id,
                    data={"delta": chunk.delta},
                )
            chunks.append(chunk)

    deltas = _generate_deltas()
    # Consume the generator to completion before returning response
    response = self.provider.finalize_stream(chunks, content_so_far, tool_calls_so_far)
    return response, deltas
```

The `LLMProvider` protocol gains a `finalize_stream()` method that assembles the streaming chunks into a complete `LLMResponse` — same type as `chat()` returns — so the loop logic is identical.

---

## `LLMProvider` protocol update

```python
# ai/providers/base.py

class LLMProvider(Protocol):
    def chat(self, messages, tools, config) -> LLMResponse: ...

    def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        config: LLMConfig,
    ) -> Iterator[LLMStreamChunk]: ...

    def finalize_stream(
        self,
        chunks: list[LLMStreamChunk],
        content: list[str],
        tool_calls: list[dict],
    ) -> LLMResponse: ...

    def count_tokens(self, text: str) -> int: ...


@dataclass
class LLMStreamChunk:
    type: Literal["content_delta", "tool_call_delta", "stop"]
    delta: str = ""
    tool_call_index: int | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
```

---

## HTTP transport — SSE endpoint

Agent runs that might take more than 2 seconds should use Server-Sent Events. The HTTP router yields one SSE line per `StreamEvent`.

```python
# routers/api_v1/agent.py
from flask import Response, stream_with_context
import json


@agent_blueprint.route("/agents/stream", methods=["POST"])
@require_auth
def stream_agent(ctx: ServiceContext):
    agent_ctx = build_agent_context(ctx)
    runner = AgentRunner(get_provider(), get_config(ctx))

    def generate():
        for event in runner.stream(ctx.incoming_data["message"], agent_ctx):
            yield f"data: {json.dumps({'type': event.type, **event.data})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

The client consumes `EventSource` or `fetch` with a readable stream and renders events as they arrive.

---

## MCP transport — streaming tool results

For SSE and Streamable HTTP MCP transports, `stream()` maps naturally — the MCP SDK handles SSE framing. For stdio transport, events are written as newline-delimited JSON.

MCP tool calls do not stream content deltas — the MCP protocol expects a complete `TextContent` result. Use the non-streaming `AgentRunner.run()` path for MCP tool handlers. Use `AgentRunner.stream()` only for direct HTTP integrations where the client can consume SSE.

---

## When to use streaming vs non-streaming

| Caller | Use |
|---|---|
| HTTP router (user-facing chat) | `stream()` — user expects real-time output |
| Orchestrator calling a subagent | `run()` — orchestrator needs the full result before continuing |
| MCP tool handler | `run()` — MCP protocol expects a complete response |
| Background task runner | `run()` — progress is reported via task status events, not SSE |
| Test harness | `run()` — deterministic, no SSE needed |

---

## What streaming must NOT do

- Emit `content_delta` events during tool-use turns — only during the final prose response.
- Stream tool `arguments` as individual characters — emit one `tool_call_start` event with the complete arguments dict.
- Buffer all events and emit them at the end — that defeats the purpose.
- Allow the SSE connection to time out silently — emit a heartbeat comment (`": heartbeat\n\n"`) every 15 seconds if no event has been sent.
- Expose raw tool result payloads as `content_delta` — tool results are structured data, not user-facing text.
