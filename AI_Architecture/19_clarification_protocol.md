# 19 — Clarification Protocol

## The problem

An agent mid-execution reaches a decision point where it lacks the information needed to proceed correctly. It has already called tools, gathered context, and is part-way through a task. It must not guess. It must not silently pick an option. It must surface the uncertainty to the right resolver.

The right resolver depends on what kind of uncertainty it is.

---

## Three types of uncertainty

| Type | Definition | Resolver |
|---|---|---|
| **In-scope** | The agent can get the answer using its own tools | Self — research with existing tools |
| **Cross-domain** | The answer lives outside the agent's tool scope | Orchestrator — resolves via research subagent |
| **Intent** | Only the user can clarify what they actually want | Human — propagates all the way up the chain |

These are mutually exclusive. Classify the uncertainty before deciding what to do.

---

## Decision tree

Every time an agent reaches a point of uncertainty, it walks this tree:

```
Step 1 — Can I answer this with my current tools?
    YES → call the tools, gather the data, proceed
    NO  → go to Step 2

Step 2 — Is this about INTENT?
         (What does the user want? What should the outcome be?
          No tool call can answer this — only the user can.)
    YES → escalate upward, mark as intent_clarification
          (propagates all the way to the human, regardless of position in hierarchy)
    NO  → go to Step 3

Step 3 — Is this about FACTS from another domain?
         (The data exists in the system but is outside my tool scope.)
    YES → escalate upward, mark as cross_domain_clarification
          (orchestrator resolves by spawning a research subagent)
    NO  → cannot classify uncertainty — treat as intent_clarification, escalate upward
```

Agents must walk this tree explicitly. Guessing is never an option.

---

## Research depth limit — forced escalation

`AgentConfig.max_research_depth_before_clarification` (default: 5) caps how many non-clarification tool calls the agent may make before the runner forces escalation. This prevents agents from spinning through tool calls searching for data they will never find.

**What happens when the cap is reached:**

The `AgentRunner._run_loop()` injects a forced system message into the conversation after the tool call batch that pushes the counter to the limit:

```
[System] You have reached the maximum number of research tool calls
without resolving the uncertainty. You must now call ask_clarification
to surface your question to the resolver. Do not call any other tools.
```

On the next LLM iteration, the agent sees this message and must call `ask_clarification`. The agent classifies the uncertainty type normally (cross_domain or intent) — the forced escalation does not set the type.

**Design intent:**

- `max_iterations` is a safety cap — prevents infinite loops.
- `max_research_depth_before_clarification` is a precision cap — prevents wasted tokens on searches that won't resolve.

An agent that calls 6+ tools looking for something it cannot find is confused, not diligent. The depth cap surfaces that confusion to the resolver rather than burning tokens.

---

## `AgentResult` — the return type of `AgentRunner.run()`

`AgentRunner.run()` no longer returns a plain string. It returns an `AgentResult` so the caller can inspect the outcome and act accordingly.

```python
# ai/agents/base.py
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ClarificationRequest:
    question: str                      # The specific question the agent needs answered
    clarification_type: Literal[
        "in_scope",                    # Shouldn't happen — agent should self-resolve
        "cross_domain",               # Orchestrator resolves via research subagent
        "intent",                     # Human must answer
    ]
    context_gathered: str             # Plain-text summary of what the agent has done so far
    referenced_data: dict             # Structured entities the question refers to (IDs, field values, fetched records)
    suggested_answers: list[str]      # Optional proposed answers — receiver picks one or writes their own
    domain_needed: str | None = None  # For cross_domain: which domain has the answer
    conversation_history: list = field(default_factory=list)  # Full message history for resume


@dataclass
class AgentResult:
    status: Literal["complete", "clarification_needed", "failed", "max_iterations"]
    content: str | None = None                    # Set when status == "complete"
    clarification: ClarificationRequest | None = None  # Set when status == "clarification_needed"
    error: str | None = None                      # Set when status == "failed"
    session_id: str | None = None                 # Always set — used for resume
    agent_name: str | None = None                 # Which agent produced this result — used by conversation session
    total_iterations: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
```

---

## `ask_clarification` tool

This tool is the mechanism by which an agent signals mid-task uncertainty. It is registered in every agent's tool list.

```python
# ai/tools/shared/ask_clarification_tool.py
from my_app.ai.agents.base import AgentContext
from my_app.errors.validation import ValidationError

SCHEMA: dict = {
    "name": "ask_clarification",
    "description": (
        "Use this tool when you cannot proceed because you lack information that your "
        "current tools cannot provide. "
        "Walk the decision tree in your instructions before calling this. "
        "Provide the exact question, a summary of what you have done, the data you are referring to, "
        "and — when the answer is a choice between known alternatives — a list of suggested answers. "
        "Do NOT use this to avoid calling your tools — research first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The specific, concrete question you need answered to proceed.",
            },
            "clarification_type": {
                "type": "string",
                "enum": ["cross_domain", "intent"],
                "description": (
                    "cross_domain: the answer is in another domain (state which one in domain_needed). "
                    "intent: only the user can answer this — it is about what they want, not what exists."
                ),
            },
            "context_gathered": {
                "type": "string",
                "description": (
                    "A plain-text summary of what you have done so far and what you already know. "
                    "This is shown to whoever resolves the question."
                ),
            },
            "referenced_data": {
                "type": "object",
                "description": (
                    "The structured data entities this question is about. "
                    "Include every entity you have fetched that is relevant to the question "
                    "(record IDs, titles, statuses, field values). "
                    "This allows the resolver to understand the question without re-fetching the data. "
                    "Example: {\"record_id\": \"rec_abc123\", \"title\": \"Smith Project\", \"status\": \"pending\"}."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Suggested answers the resolver can choose from. "
                    "Include when the question is a choice between known, concrete alternatives. "
                    "The resolver may pick one of these or provide a custom answer. "
                    "Omit for open-ended intent questions where no predefined options make sense. "
                    "Example: [\"Use the existing category 'type_a'\", \"Create a new category 'type_d'\", \"Skip categorisation for now\"]."
                ),
            },
            "domain_needed": {
                "type": "string",
                "description": "For cross_domain only: the domain that holds the answer (e.g. 'invoices', 'users').",
            },
        },
        "required": ["question", "clarification_type", "context_gathered", "referenced_data"],
    },
}


def ask_clarification_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    question = arguments.get("question", "").strip()
    clarification_type = arguments.get("clarification_type")
    context_gathered = arguments.get("context_gathered", "").strip()
    referenced_data = arguments.get("referenced_data")
    options = arguments.get("options", [])

    if not question:
        raise ValidationError("question is required")
    if clarification_type not in ("cross_domain", "intent"):
        raise ValidationError("clarification_type must be cross_domain or intent")
    if not context_gathered:
        raise ValidationError("context_gathered is required — summarize what you have done so far")
    if referenced_data is None:
        raise ValidationError("referenced_data is required — include the entities this question refers to")
    if not isinstance(referenced_data, dict):
        raise ValidationError("referenced_data must be an object")
    if clarification_type == "cross_domain" and not arguments.get("domain_needed"):
        raise ValidationError("domain_needed is required for cross_domain clarification")
    if not isinstance(options, list):
        raise ValidationError("options must be an array of strings")

    return {
        "clarification_requested": True,
        "question": question,
        "clarification_type": clarification_type,
        "context_gathered": context_gathered,
        "referenced_data": referenced_data,
        "options": options,
        "domain_needed": arguments.get("domain_needed"),
        "session_id": agent_ctx.session_id,
    }
```

---

## `AgentRunner` — handling the clarification signal

The runner detects when the LLM calls `ask_clarification` and exits the loop, returning a `clarification_needed` result instead of continuing.

```python
# ai/agents/base.py — in the tool loop

def _dispatch(self, tool_call: ToolCall, agent_ctx: AgentContext) -> dict:
    ...
    result = fn(tool_call.arguments, agent_ctx)

    if tool_call.name == "ask_clarification":
        raise _ClarificationSignal(result, self._current_messages[:])
    return result


class _ClarificationSignal(Exception):
    def __init__(self, clarification_data: dict, history: list):
        self.data = clarification_data
        self.history = history


def run(self, user_message: str, agent_ctx: AgentContext) -> AgentResult:
    messages = [Message(role="user", content=user_message)]
    ...
    try:
        for iteration in range(self.config.max_iterations):
            ...
            for tool_call in response.tool_calls:
                try:
                    tool_result = self._dispatch(tool_call, agent_ctx)
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
                            conversation_history=signal.history,
                        ),
                        session_id=agent_ctx.session_id,
                        total_iterations=iteration + 1,
                        total_input_tokens=self._accumulated_input_tokens,
                        total_output_tokens=self._accumulated_output_tokens,
                    )
                messages.append(Message(role="tool", content=str(tool_result), ...))
    ...
```

---

## Resume — continuing after the question is answered

The caller provides an answer — either by selecting one of the `suggested_answers` or writing a custom response — and resumes the agent by calling `run_with_history()`. The answer is injected into the existing conversation history as a user message; the loop continues from exactly where it paused.

```python
# ai/agents/base.py

def run_with_history(
    self,
    history: list[Message],
    answer: str,
    agent_ctx: AgentContext,
) -> AgentResult:
    resumed_history = history + [
        Message(
            role="user",
            content=f"[Clarification answer]\n{answer}",
        )
    ]
    return self._run_loop(resumed_history, agent_ctx)
```

The `answer` string is always free text — whether it came from a selected option or was typed by a human or resolved by an orchestrator. The agent does not know how the answer was produced.

**Option selection**: if the resolver picks a suggested answer by index, the caller passes the text of that option, not the index. This keeps the resume contract simple and provider-agnostic.

```python
# Resolver selects option 1 (index 0)
answer = clarification.suggested_answers[0]
runner.run_with_history(history=clarification.conversation_history, answer=answer, agent_ctx=agent_ctx)

# Resolver writes a custom answer
answer = "Use the existing category 'type_b' and flag the record for review."
runner.run_with_history(history=clarification.conversation_history, answer=answer, agent_ctx=agent_ctx)
```

The caller is responsible for storing the conversation history from the `ClarificationRequest` and passing it back at resume time. For async resume (human may answer hours later), store via persistent memory (see [14_persistent_memory.md](14_persistent_memory.md)) using `session_id` as the key.

---

## Upward propagation chain

When an agent returns `clarification_needed`, the caller decides what to do:

### Single agent → Human interface

The HTTP router or MCP server receives `AgentResult(status="clarification_needed")`. It surfaces the question to the user and waits.

```python
# routers/api_v1/agent.py
result = runner.run(user_message, agent_ctx)

if result.status == "clarification_needed":
    cl = result.clarification
    save_memory(
        workspace_id=agent_ctx.workspace_id,
        agent_name=config.name,
        memory_type="pending_clarification",
        key=f"clarification_{result.session_id}",
        value={
            "question": cl.question,
            "referenced_data": cl.referenced_data,
            "suggested_answers": cl.suggested_answers,
            "history": [m.__dict__ for m in cl.conversation_history],
        },
        ttl_hours=48,
    )
    return http_response(200, {
        "status": "clarification_needed",
        "session_id": result.session_id,
        "question": cl.question,
        "context": cl.context_gathered,
        "referenced_data": cl.referenced_data,    # the entities the question is about
        "options": cl.suggested_answers,          # [] if no suggestions — UI shows free-text input
    })
```

### Subagent → Orchestrator

The orchestrator receives `AgentResult(status="clarification_needed")` from a subagent. It makes one of three decisions:

**A — Answer directly** (orchestrator already has the data in its current context):

The orchestrator first checks `suggested_answers` — if one of the options matches what it knows from context, it selects that option's text. Otherwise it composes a custom answer from the `referenced_data` and its own context.

```python
if result.status == "clarification_needed":
    cl = result.clarification
    answer = self._try_resolve_from_context(cl)
    # _try_resolve_from_context checks cl.referenced_data and cl.suggested_answers
    # against the orchestrator's current conversation history. If a suggested answer
    # matches, it returns that option's text. Otherwise it derives a custom answer.
    if answer:
        resumed = runner.run_with_history(
            history=cl.conversation_history,
            answer=answer,
            agent_ctx=agent_ctx,
        )
        return resumed
```

**B — Resolve via research subagent** (`cross_domain` type):

```python
    if result.clarification.clarification_type == "cross_domain":
        research_result = self._run_research_subagent(
            domain=result.clarification.domain_needed,
            question=result.clarification.question,
            agent_ctx=agent_ctx,
        )
        if research_result.status == "complete":
            resumed = runner.run_with_history(
                history=result.clarification.conversation_history,
                answer=research_result.content,
                agent_ctx=agent_ctx,
            )
            return resumed
```

**C — Propagate upward** (intent clarification, or research failed):

```python
    # Cannot resolve — propagate to the orchestrator's own caller
    return AgentResult(
        status="clarification_needed",
        clarification=result.clarification,  # pass through unchanged
        session_id=agent_ctx.session_id,
    )
```

---

## Research subagent pattern

When an orchestrator needs to resolve a `cross_domain` question, it spawns a minimal research subagent scoped to the needed domain.

```python
# ai/agents/base.py — in the orchestrator runner

def _run_research_subagent(
    self,
    domain: str,
    question: str,
    agent_ctx: AgentContext,
) -> AgentResult:
    research_config = self._research_subagent_configs.get(domain)
    if research_config is None:
        return AgentResult(status="failed", error=f"No research subagent for domain '{domain}'")

    task = (
        f"Answer the following question using your tools. "
        f"Return only the answer — no explanation needed.\n\n"
        f"Question: {question}"
    )
    runner = AgentRunner(self.provider, research_config)
    return runner.run(task, agent_ctx)
```

Research subagents are minimal: one domain, read-only tools (query tools only, no commands), `max_iterations=3`. They exist to answer a single question, not to perform full tasks.

---

## System prompt instruction for clarification

Every agent's system prompt must include a clarification section:

```markdown
## When you are uncertain

Before calling ask_clarification, walk this tree:

1. Can I answer this with my current tools?
   YES → call the tools and research first. Do not escalate.
   NO  → continue.

2. Is this about WHAT the user wants (intent)?
   YES → call ask_clarification with type "intent".

3. Is this about data in another domain (outside my tools)?
   YES → call ask_clarification with type "cross_domain" and name the domain.

Never guess. Never pick an option arbitrarily to avoid asking.

When calling ask_clarification:

- **context_gathered**: always fill this with a plain-text summary of what you have done
  so far and what you already know. The resolver reads this to understand the situation.

- **referenced_data**: always include the structured entities this question is about —
  the IDs, titles, statuses, and field values of the records or objects you have fetched.
  Do not describe them in prose here; put the raw values as a JSON object.
  Example: {"record_id": "rec_abc123", "title": "Smith Project", "status": "pending"}

- **options**: include suggested answers when the question is a choice between
  concrete, known alternatives. Each option must be a complete, actionable answer —
  not a label. The resolver can select one or override with their own answer.
  Example: ["Assign category 'type_a' and proceed", "Assign category 'type_b' and proceed",
            "Leave uncategorised and flag for review"]
  Omit options for open-ended intent questions where no predefined alternatives make sense.
```

---

## What this protocol must NOT allow

- Calling `ask_clarification` before attempting to use available tools — research first.
- Using `ask_clarification` to avoid making a decision the agent has enough data to make.
- Submitting an empty or vague `referenced_data` — always include the concrete entities the question is about.
- Providing options that are vague labels instead of complete actionable answers — each option must be a full answer the agent can act on directly.
- An orchestrator guessing at the answer to a `cross_domain` question — either resolve it or propagate it.
- A subagent calling a peer agent directly — cross-domain resolution always goes through the orchestrator.
- Resuming a session without the original conversation history — the history is what makes resume coherent.
- Passing an option index as the resume answer — always pass the option's text, never an index.
- Propagating an `intent` clarification that the orchestrator itself can resolve — the orchestrator only propagates what it genuinely cannot answer.
