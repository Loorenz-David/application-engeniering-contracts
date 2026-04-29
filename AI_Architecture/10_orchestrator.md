# 10 — Orchestrator Contract

## Definition

An orchestrator is an agent whose tools are other agents (subagents), not backend operations. Its job is to:
1. Decompose a complex task into bounded subtasks.
2. Delegate each subtask to the appropriate subagent.
3. Collect results and either synthesize a final response or delegate again.

The orchestrator never calls backend commands or queries directly. Its tool list contains only subagent invocations.

---

## When to use an orchestrator

Use an orchestrator when:
- A task spans multiple domains (e.g., create an invoice AND send a notification AND update a record).
- Subtasks can run independently or need to run in a specific sequence.
- The task is too large for a single agent's context window.
- Different parts of the task require different tool scopes.

Do not use an orchestrator when:
- A single agent with 3–5 tools can handle the task end-to-end.
- The task is purely sequential with no domain separation.

---

## Folder structure

```
ai/agents/
└── <orchestrator_name>/
    ├── agent.py               # Orchestrator config — tool list is subagent runners
    ├── system_prompt.md       # Orchestrator system prompt
    └── subagents/             # Subagents scoped to this orchestrator (optional)
        └── <subagent_name>/
            ├── agent.py
            └── system_prompt.md
```

---

## Subagent as a tool

For the orchestrator, each subagent is registered as a tool. The subagent tool function runs the subagent and returns its result.

```python
# ai/agents/workflow_orchestrator/subagents/record_subagent/tool.py
from my_app.ai.agents.base import AgentRunner, AgentContext
from my_app.ai.agents.workflow_orchestrator.subagents.record_subagent import agent as record_agent
from my_app.ai.providers.base import get_default_provider

SCHEMA: dict = {
    "name": "run_record_subagent",
    "description": (
        "Delegates a record-management task to the Record Subagent. "
        "Use this for any task involving creating, reading, or updating records. "
        "Provide a complete, self-contained task description — the subagent has no memory "
        "of the current conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "A complete, self-contained description of the task. "
                    "Include all necessary context — entity names, IDs, field values."
                ),
            },
        },
        "required": ["task"],
    },
}


def run_record_subagent_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    task = arguments.get("task", "").strip()
    if not task:
        raise ValidationError("task is required")

    provider = get_default_provider()
    runner = AgentRunner(provider, record_agent.CONFIG)
    result = runner.run(user_message=task, agent_ctx=agent_ctx)

    if result.status == "complete":
        return {"result": result.content, "subagent": "record_subagent"}

    if result.status == "clarification_needed":
        # Re-raise as a ClarificationSignal so the orchestrator's own runner
        # can intercept it and apply the knowledge-resolver logic.
        raise _ClarificationSignal(
            data={
                "question": result.clarification.question,
                "clarification_type": result.clarification.clarification_type,
                "context_gathered": result.clarification.context_gathered,
                "domain_needed": result.clarification.domain_needed,
                "subagent": "record_subagent",
                "subagent_history": result.clarification.conversation_history,
            },
            history=[],  # orchestrator manages its own history separately
        )

    # failed or max_iterations — return error string for the orchestrator LLM to act on
    return {"result": f"FAILED: {result.content or result.error}", "subagent": "record_subagent"}
```

---

## Orchestrator system prompt rules

The orchestrator's system prompt must explain:
- What subtask types exist and which subagent handles each.
- How to write a complete, self-contained task for a subagent (the subagent has no conversation history).
- When to run subagents in sequence vs when to synthesize from a single subagent result.
- How to aggregate results into a final user-facing response.

```markdown
# Workflow Orchestrator

You coordinate complex multi-step workflows by delegating to specialized subagents.

## Subagents available
- **run_record_subagent**: Handles all record creation, retrieval, and updates.
- **run_notification_subagent**: Handles sending notifications and emails.
- **run_report_subagent**: Handles generating and exporting reports.

## How to delegate
Each subagent is isolated — it has no access to this conversation history.
Write a complete task description that includes:
- What to do (the operation)
- What entity to act on (use the entity's ID or title)
- Any field values required for the operation

## Sequencing
Complete one subagent task and review its result before calling the next.
Do not call multiple subagents in parallel — execute sequentially and verify each result.

## Final response
After all subtasks complete, synthesize a single clear summary of what was accomplished.
```

---

## Task decomposition rules

The orchestrator must decompose the user's request before calling any subagent. The decomposition must be explicit — stated in the reasoning before the first tool call.

```
User: "Create a record for the Smith project and send a confirmation email to the team."

Orchestrator decomposition:
1. Create record (→ record_subagent)
2. Get the record ID from result
3. Send confirmation email (→ notification_subagent, including record ID)
```

Do not call a subagent without knowing what to do with its result.

---

## Context passing between subagents

Subagents are stateless. If subagent B needs the output of subagent A, the orchestrator extracts the relevant data from A's result and includes it explicitly in B's task description.

```python
# Orchestrator tool loop (conceptual)

# Step 1 — call record subagent
record_result = run_record_subagent_tool(
    {"task": "Create a record titled 'Smith Project' in category type_a."},
    agent_ctx,
)
record_id = record_result["result"]  # orchestrator parses this

# Step 2 — call notification subagent with explicit context
notification_result = run_notification_subagent_tool(
    {
        "task": (
            f"Send a confirmation email to team@example.com. "
            f"The subject is 'Smith Project created'. "
            f"Include the record ID: {record_id}."
        )
    },
    agent_ctx,
)
```

Never pass raw agent output verbatim into the next subagent's task. Parse it and extract only what the next subagent needs.

---

## Research subagent registration

Research subagents are read-only agents scoped to a single domain. They exist to answer one cross-domain question, not to perform full tasks. They are declared in the orchestrator's `agent.py` and registered in `AgentConfig.research_subagents`.

### `AgentConfig` — `research_subagents` field

```python
# ai/agents/base.py
@dataclass
class AgentConfig:
    ...
    research_subagents: dict[str, "AgentConfig"] = field(default_factory=dict)
    # Orchestrators only. Maps domain name → read-only subagent config.
    # Non-orchestrators leave this empty.
```

### Orchestrator `agent.py` — declaring research subagents

```python
# ai/agents/workflow_orchestrator/agent.py
from my_app.ai.agents.workflow_orchestrator.subagents.invoices_research import agent as invoice_research
from my_app.ai.agents.workflow_orchestrator.subagents.users_research import agent as users_research

CONFIG = AgentConfig(
    name="workflow_orchestrator",
    system_prompt=SYSTEM_PROMPT,
    prompt_version="v1",
    tools=TOOLS,
    max_iterations=15,
    research_subagents={
        "invoices": invoice_research.CONFIG,
        "users": users_research.CONFIG,
    },
)
```

### Research subagent config rules

- `max_iterations=3` — they answer one question, not multi-step tasks.
- Tools: read-only query tools only. No commands, no mutations.
- System prompt: states the domain, instructs the agent to return only the answer with no additional prose.
- No `research_subagents` of their own — research subagents cannot spawn further research subagents.

### `AgentRunner` — accessing research subagent configs

```python
# ai/agents/base.py

class AgentRunner:
    def __init__(self, provider: LLMProvider, config: AgentConfig):
        ...
        self._research_subagent_configs: dict[str, AgentConfig] = config.research_subagents
```

---

## `_try_resolve_from_context`

Before spawning a research subagent or propagating upward, the orchestrator checks whether the clarification can be answered from its own current conversation history.

```python
# ai/agents/base.py — in AgentRunner (orchestrators only)

def _try_resolve_from_context(self, clarification: ClarificationRequest) -> str | None:
    """
    Inspect the orchestrator's current conversation history for data that answers
    the clarification question.

    Check order:
    1. suggested_answers match — if any prior tool result confirms one of the
       suggested_answers options, return that option's text verbatim.
    2. referenced_data match — if the referenced_data entities appear in a prior
       tool result with enough detail to answer the question, compose a direct answer.
    3. Return None if context is insufficient — the orchestrator cannot answer.

    This method never calls any tool. It only reads self._current_messages.
    """
    prior_tool_results = [
        m.content for m in self._current_messages
        if m.role == "tool"
    ]
    combined_context = "\n".join(prior_tool_results)

    # Step 1: check if any suggested answer is confirmed by prior results
    for option in clarification.suggested_answers:
        # A simple heuristic: check whether key terms from the option appear in
        # the prior tool results. In practice, this can be an LLM sub-call or
        # a structured lookup depending on the orchestrator's domain.
        if _option_is_supported_by_context(option, combined_context):
            return option

    # Step 2: check if referenced_data entities are present in prior results
    for key, value in clarification.referenced_data.items():
        if str(value) in combined_context:
            # Entity is present — compose a direct answer from available data
            return _compose_answer_from_context(clarification.question, combined_context)

    return None
```

`_option_is_supported_by_context` and `_compose_answer_from_context` are application-specific helpers. For simple orchestrators, a string-match heuristic is sufficient. For complex ones, a single LLM call with the combined context is appropriate — the key constraint is that this call is cheap (no tools, just inference) and the result is never stored or logged as an agent session.

---

## Orchestrator as knowledge resolver

When a subagent raises a `clarification_needed` signal, the orchestrator is the first resolver in the chain. It must not blindly propagate every question upward — it should answer what it can.

The orchestrator has three resolution options, evaluated in order:

### Option 1 — Answer directly from current context

The orchestrator already has the data in its own conversation history (gathered from a previously completed subagent run).

```python
# ai/agents/base.py — in the orchestrator's _dispatch
except _ClarificationSignal as signal:
    answer = self._try_resolve_from_context(signal.data["question"])
    if answer:
        # Resume the subagent with the answer injected into its history
        subagent_runner = AgentRunner(provider, subagent_config)
        resumed = subagent_runner.run_with_history(
            history=signal.data["subagent_history"],
            answer=answer,
            agent_ctx=agent_ctx,
        )
        return {"result": resumed.content, "subagent": signal.data["subagent"]}
```

### Option 2 — Resolve via a research subagent (`cross_domain` type)

The question is about data in another domain. The orchestrator spawns a minimal, read-only research subagent for that domain, gets the answer, and resumes the original subagent.

```python
    if signal.data["clarification_type"] == "cross_domain":
        domain = signal.data.get("domain_needed")
        research_config = self._research_subagent_configs.get(domain)
        if research_config:
            research_runner = AgentRunner(provider, research_config)
            research_result = research_runner.run(
                user_message=(
                    f"Answer this question using your tools. Return only the answer.\n\n"
                    f"Question: {signal.data['question']}"
                ),
                agent_ctx=agent_ctx,
            )
            if research_result.status == "complete":
                subagent_runner = AgentRunner(provider, subagent_config)
                resumed = subagent_runner.run_with_history(
                    history=signal.data["subagent_history"],
                    answer=research_result.content,
                    agent_ctx=agent_ctx,
                )
                return {"result": resumed.content, "subagent": signal.data["subagent"]}
```

Research subagents are minimal: read-only tools only (query tools, no commands), `max_iterations=3`. They exist to answer one question, not to perform full tasks.

### Option 3 — Propagate upward

The orchestrator cannot resolve the question — either it is an `intent` type (only the human can answer), or the research subagent also failed. Propagate the clarification signal upward to the orchestrator's own caller.

```python
    # Cannot resolve — propagate unchanged
    raise _ClarificationSignal(
        data=signal.data,
        history=self._current_messages[:],
    )
```

The chain terminates when the question reaches either the human interface (HTTP router, MCP server) or an orchestrator that can resolve it.

---

## Orchestrator system prompt — clarification handling

The orchestrator system prompt must include a section on how to handle subagent clarification:

```markdown
## When a subagent asks for clarification
If a subagent tool returns a clarification request:
1. Check if you already have the answer from a previous subagent result.
   YES → provide the answer and re-run the subagent with it.
   NO  → escalate to the user using ask_clarification with the same type and question.
Never guess the answer. Never skip the question and proceed with assumptions.
```

---

## Failure handling

If a subagent returns an error or an ambiguous result:
1. The orchestrator does not proceed to dependent subtasks.
2. It reports the failure clearly to the user with enough context to retry.
3. It does not retry automatically — retrying on failure without user input can compound errors.

```markdown
# System prompt rule
If a subagent reports FAILED, stop the workflow. Explain what failed, what was
completed so far, and ask the user how to proceed. Never silently retry.
```

---

## Max iterations

The orchestrator has its own `max_iterations` cap (default: 15, higher than a single agent because it coordinates multiple subagent calls). If the cap is reached, the orchestrator reports partial progress and stops.

---

## What the orchestrator must NOT do

- Call backend commands or queries directly — all operations go through subagent tools.
- Pass an entire conversation history to a subagent — write a fresh, self-contained task.
- Run subagents in parallel (unless the application explicitly implements a parallel subagent harness with result merging).
- Assume a subagent succeeded — always check `result.status` before proceeding.
- Expose internal subagent names or implementation details to the user.
- Guess the answer to a subagent's `clarification_needed` — either resolve it or propagate it.
- Allow peer-to-peer subagent communication — all cross-domain knowledge flows through the orchestrator.
