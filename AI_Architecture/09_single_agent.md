# 09 — Single Agent Contract

## Definition

A single agent is an autonomous unit composed of:
- One system prompt that defines its identity, scope, and behavioral rules.
- A fixed set of tools it is allowed to call.
- A provider-agnostic tool loop that runs until the LLM signals completion.
- An `AgentContext` that carries the identity for every tool call.

A single agent handles one cohesive task domain. It does not spawn subagents.

---

## Folder structure

```
ai/agents/
├── base.py                    # AgentRunner — the provider-agnostic loop
└── <agent_name>/
    ├── agent.py               # Agent config: provider, tools, system prompt path
    └── system_prompt.md       # System prompt — plain Markdown, loaded at runtime
```

One folder = one agent. The folder name is the agent's identifier (e.g., `record_agent`, `report_agent`).

---

## Agent configuration (`agent.py`)

```python
# ai/agents/record_agent/agent.py
from pathlib import Path
from my_app.ai.agents.base import AgentConfig
from my_app.ai.tools.record.create_record_tool import create_record_tool, SCHEMA as CREATE
from my_app.ai.tools.record.get_record_tool import get_record_tool, SCHEMA as GET
from my_app.ai.tools.record.list_records_tool import list_records_tool, SCHEMA as LIST
from my_app.ai.tools.record.update_record_tool import update_record_tool, SCHEMA as UPDATE

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text()

TOOLS = [
    (CREATE, create_record_tool),
    (GET, get_record_tool),
    (LIST, list_records_tool),
    (UPDATE, update_record_tool),
]

CONFIG = AgentConfig(
    name="record_agent",
    system_prompt=SYSTEM_PROMPT,
    tools=TOOLS,
    max_iterations=10,
)
```

`agent.py` is a configuration file — no logic, no conditionals.

---

## AgentConfig

```python
# ai/agents/base.py
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    prompt_version: str                              # e.g. "v2" — recorded in session log
    tools: list[tuple[dict, callable]]               # (schema, function) pairs — ask_clarification is auto-injected
    max_iterations: int = 10                         # hard cap on tool loop iterations
    max_session_tokens: int = 50_000                 # hard cap on tokens per single run
    max_research_depth_before_clarification: int = 5 # max tool calls before forcing escalation
    llm_config: LLMConfig | None = None              # if None, use application default
    context_budget: ContextBudget | None = None      # if None, use default budget for model
```

`max_iterations` is a safety cap — an agent that loops indefinitely is stuck. `max_research_depth_before_clarification` is a precision cap — an agent that calls 6+ tools searching for something it can't find is wasting tokens and should escalate instead.

### `ask_clarification` auto-injection

The `ask_clarification` tool is injected into every agent's tool list by `AgentRunner.__init__`, regardless of what the agent config declares. It does not need to be listed in `agent.py`:

```python
# ai/agents/base.py

from my_app.ai.tools.shared.ask_clarification_tool import ask_clarification_tool, SCHEMA as ASK_SCHEMA

class AgentRunner:
    def __init__(self, provider: LLMProvider, config: AgentConfig):
        # Auto-inject ask_clarification if not already present
        tool_names = {schema["name"] for schema, _ in config.tools}
        tools = list(config.tools)
        if "ask_clarification" not in tool_names:
            tools.append((ASK_SCHEMA, ask_clarification_tool))

        self._tool_map = {schema["name"]: fn for schema, fn in tools}
        self._tool_schemas = [schema for schema, _ in tools]
        ...
```

---

## System prompt contract (`system_prompt.md`)

The system prompt defines what the agent is and what it must not do. Every system prompt must contain four sections:

### 1. Identity
What is this agent? What is its purpose?

```markdown
You are the Record Agent for [Application Name].
Your job is to help users create, retrieve, update, and manage records in their workspace.
```

### 2. Scope
What is the agent allowed to do? What is explicitly off-limits?

```markdown
You have access to the following tools: create_record, get_record, list_records, update_record.
You may only act on records within the current workspace.
You may not delete records — direct the user to the application UI for destructive operations.
```

### 3. Behavioral rules
How should the agent behave under uncertainty or ambiguity?

```markdown
- Always confirm the workspace context before creating or modifying records.
- If the user's intent is ambiguous, ask one clarifying question before calling a tool.
- Do not assume values for required fields — ask if they are missing.
- If a tool returns an error, explain it in plain language and suggest next steps.
```

### 4. Output format
What should the agent's responses look like?

```markdown
- Respond in plain English. Do not return raw JSON to the user.
- After creating or updating a record, confirm what was done with the record's title and ID.
- When listing records, present them as a numbered list with title and status.
```

---

## AgentRunner — the tool loop

`AgentRunner.run()` returns an `AgentResult`, not a plain string. The caller inspects `result.status` before acting. See [19_clarification_protocol.md](19_clarification_protocol.md) for the full `AgentResult` and `ClarificationRequest` type definitions.

```python
# ai/agents/base.py
from my_app.ai.providers.base import LLMProvider, Message, LLMResponse
from my_app.ai.agents.base import AgentResult, ClarificationRequest, _ClarificationSignal


class AgentRunner:

    def __init__(self, provider: LLMProvider, config: AgentConfig):
        self.provider = provider
        self.config = config
        self._tool_map: dict[str, callable] = {
            schema["name"]: fn for schema, fn in config.tools
        }
        self._tool_schemas: list[dict] = [schema for schema, _ in config.tools]
        self._accumulated_input_tokens = 0
        self._accumulated_output_tokens = 0
        self._current_messages: list[Message] = []

    def run(self, user_message: str, agent_ctx: AgentContext) -> AgentResult:
        messages: list[Message] = [Message(role="user", content=user_message)]
        return self._run_loop(messages, agent_ctx)

    def run_with_history(
        self,
        history: list[Message],
        answer: str,
        agent_ctx: AgentContext,
    ) -> AgentResult:
        resumed = history + [Message(role="user", content=f"[Clarification answer]\n{answer}")]
        return self._run_loop(resumed, agent_ctx)

    def _run_loop(self, messages: list[Message], agent_ctx: AgentContext) -> AgentResult:
        llm_config = self.config.llm_config or _default_llm_config()
        llm_config = LLMConfig(
            **{**vars(llm_config), "system_prompt": self.config.system_prompt}
        )
        research_depth = 0  # counts non-clarification tool calls; capped by max_research_depth_before_clarification

        for iteration in range(self.config.max_iterations):
            self._current_messages = messages[:]
            response: LLMResponse = self.provider.chat(
                messages=messages,
                tools=self._tool_schemas,
                config=llm_config,
            )
            self._accumulated_input_tokens += response.input_tokens
            self._accumulated_output_tokens += response.output_tokens

            _log_llm_call(
                agent_name=self.config.name,
                session_id=agent_ctx.session_id,
                iteration=iteration,
                response=response,
            )

            if self._accumulated_input_tokens + self._accumulated_output_tokens > self.config.max_session_tokens:
                return AgentResult(
                    status="failed",
                    error="Session token limit exceeded.",
                    session_id=agent_ctx.session_id,
                    total_iterations=iteration + 1,
                    total_input_tokens=self._accumulated_input_tokens,
                    total_output_tokens=self._accumulated_output_tokens,
                )

            if response.stop_reason == "end_turn":
                return AgentResult(
                    status="complete",
                    content=response.content or "",
                    session_id=agent_ctx.session_id,
                    agent_name=self.config.name,
                    total_iterations=iteration + 1,
                    total_input_tokens=self._accumulated_input_tokens,
                    total_output_tokens=self._accumulated_output_tokens,
                )

            if response.stop_reason == "tool_use":
                messages.append(Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=[
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                ))
                for tool_call in response.tool_calls:
                    try:
                        tool_result = self._dispatch(tool_call, agent_ctx)
                        messages.append(Message(
                            role="tool",
                            content=str(tool_result),
                            tool_call_id=tool_call.id,
                        ))
                        if tool_call.name != "ask_clarification":
                            research_depth += 1
                    except _ClarificationSignal as signal:
                        return AgentResult(
                            status="clarification_needed",
                            clarification=ClarificationRequest(
                                question=signal.data["question"],
                                clarification_type=signal.data["clarification_type"],
                                context_gathered=signal.data["context_gathered"],
                                referenced_data=signal.data.get("referenced_data", {}),
                                suggested_answers=signal.data.get("options", []),
                                domain_needed=signal.data.get("domain_needed"),
                                conversation_history=messages[:],
                            ),
                            session_id=agent_ctx.session_id,
                            total_iterations=iteration + 1,
                            total_input_tokens=self._accumulated_input_tokens,
                            total_output_tokens=self._accumulated_output_tokens,
                        )

                # Depth cap — force escalation when max research calls reached without resolution.
                # The injected message appears as a user turn so the LLM sees it on the next iteration
                # and must call ask_clarification instead of calling another research tool.
                if research_depth >= self.config.max_research_depth_before_clarification:
                    messages.append(Message(
                        role="user",
                        content=(
                            "[System] You have reached the maximum number of research tool calls "
                            "without resolving the uncertainty. You must now call ask_clarification "
                            "to surface your question to the resolver. Do not call any other tools."
                        ),
                    ))
                continue

            break

        _log_max_iterations_warning(self.config.name, agent_ctx.session_id)
        return AgentResult(
            status="max_iterations",
            content="The task could not be completed within the allowed number of steps.",
            session_id=agent_ctx.session_id,
            agent_name=self.config.name,
            total_iterations=self.config.max_iterations,
            total_input_tokens=self._accumulated_input_tokens,
            total_output_tokens=self._accumulated_output_tokens,
        )

    def _dispatch(self, tool_call, agent_ctx: AgentContext) -> dict:
        if tool_call.name not in agent_ctx.scopes:
            raise ScopeViolationError(f"Tool '{tool_call.name}' not in session scopes.")
        fn = self._tool_map.get(tool_call.name)
        if fn is None:
            raise ValueError(f"Tool '{tool_call.name}' not registered in this agent.")
        result = fn(tool_call.arguments, agent_ctx)
        if tool_call.name == "ask_clarification" and result.get("clarification_requested"):
            raise _ClarificationSignal(result, self._current_messages[:])
        return result
```

---

## Research-first rule

Before an agent calls `ask_clarification`, it must attempt to resolve the uncertainty using its own tools. Escalating without researching first is a contract violation.

The system prompt clarification section (see [19_clarification_protocol.md](19_clarification_protocol.md)) must be present in every agent that has `ask_clarification` in its tool list. It instructs the LLM to walk the decision tree before escalating.

---

## Result format

`AgentRunner.run()` returns an `AgentResult`. The caller must branch on `result.status`:

```python
result = AgentRunner(provider, config).run(user_message, agent_ctx)

if result.status == "complete":
    return http_response(200, {"reply": result.content})

elif result.status == "clarification_needed":
    # Store history, surface question to caller
    ...

elif result.status == "failed":
    return http_response(500, {"error": result.error})

elif result.status == "max_iterations":
    return http_response(200, {"reply": result.content})  # partial result
```

Agents do not return structured JSON inside `content`. The LLM's prose response is the result. If a structured output is required, define a dedicated output tool that the LLM must call before finishing.

---

## Entry points

An agent can be invoked from multiple callers. All entry points handle `AgentResult`:

```python
# From an HTTP router
result = AgentRunner(provider, record_agent.CONFIG).run(
    user_message=ctx.incoming_data["message"],
    agent_ctx=build_agent_context_from_request(ctx),
)

# From an MCP server tool handler
result = AgentRunner(provider, record_agent.CONFIG).run(
    user_message=arguments["message"],
    agent_ctx=resolve_agent_context(),
)

# From an orchestrator (receives AgentResult, not a string)
result = AgentRunner(provider, record_agent.CONFIG).run(
    user_message=subtask_description,
    agent_ctx=agent_ctx,
)
```

The `AgentRunner` does not know or care who is calling it.

---

## What a single agent must NOT do

- Spawn subagents or call other agents directly.
- Modify its own tool list or scopes at runtime.
- Continue running after reaching `max_iterations`.
- Return raw tool results to the user — always narrate in plain language.
- Hold state between separate `run()` calls (it is stateless; memory is handled separately — see [13_context_management.md](13_context_management.md)).
- Call `ask_clarification` without first attempting to resolve the uncertainty with its own tools (research-first rule).
- Continue calling research tools after `max_research_depth_before_clarification` is reached — at that point the runner injects a forced escalation message and the agent must call `ask_clarification`.
- Return a plain string from `run()` — always return `AgentResult`.
