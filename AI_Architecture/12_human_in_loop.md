# 12 — Human-in-the-Loop Contract

## Definition

Human-in-the-loop (HITL) is the mechanism by which an agent pauses execution and requires a human response before proceeding.

There are two distinct HITL patterns. They look similar but serve different purposes and must not be confused:

| Pattern | When | Tool used | Handled by |
|---|---|---|---|
| **Confirmation gate** | Before a dangerous / irreversible action | `confirm_action` | Any agent with a dangerous tool |
| **Intent clarification** | Mid-task, when only the human can resolve what they want | `ask_clarification` (type: `intent`) | Propagates upward through the chain — see [19_clarification_protocol.md](19_clarification_protocol.md) |

A confirmation gate is about **safety** — the agent knows what to do, but must not do it without consent. An intent clarification is about **understanding** — the agent does not know what to do and cannot resolve it from data or tools.

HITL confirmation gates are not optional for dangerous operations. They are a hard architectural requirement for any tool that is irreversible, destructive, or has significant external impact.

---

## When to pause and ask

An agent must pause and request human confirmation before:

| Category | Examples |
|---|---|
| **Destructive operations** | Delete a record, archive a workspace, remove a user |
| **Irreversible external effects** | Send an email, send an SMS, post to a webhook, publish data |
| **Bulk operations** | Update or delete more than one entity in a single action |
| **High-stakes writes** | Submit a payment, approve a request, change a user's role |
| **Ambiguous intent** | The user's request can reasonably be interpreted in two or more ways |

An agent must NOT pause for:
- Read operations (list, get, search).
- Low-stakes writes that are easily reversed (add a note, update a title).
- Operations the user has already confirmed in the current message.

---

## Confirmation gate pattern

A confirmation gate is a tool registered in the agent's tool list whose sole job is to record that the user has explicitly approved an action.

```python
# ai/tools/shared/confirm_action_tool.py
SCHEMA: dict = {
    "name": "confirm_action",
    "description": (
        "Records the user's explicit confirmation to proceed with a proposed action. "
        "MUST be called before executing any dangerous operation. "
        "Present the action to the user first, then call this tool only after they say yes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action_description": {
                "type": "string",
                "description": "A plain-English description of the action the user is confirming.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Must be true. Only call this tool when the user has explicitly agreed.",
            },
        },
        "required": ["action_description", "confirmed"],
    },
}


def confirm_action_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    if not arguments.get("confirmed"):
        raise ValidationError("confirmed must be true. Only call this after the user agrees.")
    return {
        "confirmation_recorded": True,
        "action": arguments["action_description"],
        "session_id": agent_ctx.session_id,
    }
```

---

## Dangerous tool execution flow

For a tool marked `"dangerous": True` (see [05_mcp_tools.md](05_mcp_tools.md)), the agent must follow this sequence:

```
1. Agent identifies that a dangerous operation is required.
2. Agent presents the proposed action to the user in plain language:
   "I'm about to delete the record 'Smith Project' (rec_abc123). This cannot be undone. Shall I proceed?"
3. User responds with explicit approval ("yes", "go ahead", "confirm").
4. Agent calls confirm_action_tool with a description of the action.
5. Agent calls the dangerous tool.
```

If the user responds with anything other than explicit approval, the agent cancels the operation and confirms the cancellation:
```
"Understood — I've cancelled the deletion. The record 'Smith Project' is unchanged."
```

---

## System prompt language for dangerous tools

The agent's system prompt must include an explicit rule for dangerous operations. Every agent that has a dangerous tool in its tool list must include this in its system prompt:

```markdown
## Dangerous operations
Before calling any tool that deletes, sends, publishes, or makes an irreversible change:
1. State clearly what you are about to do and what the effect will be.
2. Ask the user: "Shall I proceed?"
3. Wait for an explicit "yes" or equivalent before calling confirm_action, then the operation tool.
4. If the user says no or expresses uncertainty, cancel and confirm cancellation.
Never infer consent from context. Confirmation must be explicit in the current message.
```

---

## Ambiguity gates (pre-task)

When the user's intent is ambiguous **before any tool has been called**, the agent asks one clarifying question upfront and does not start the task.

```
User: "Remove the Smith record."
→ Two interpretations: soft-delete (archive) or hard-delete (destroy).

Agent: "Do you want to archive the Smith Project record (so it can be restored later) 
or permanently delete it (which cannot be undone)?"
```

Rules:
- Ask exactly one question, not multiple.
- Offer the concrete options — do not ask open-ended questions.
- After the user answers, proceed without asking again.

This is a pre-task gate — the agent has not started work yet. It is distinct from a mid-task intent clarification (see below).

---

## Mid-task intent clarification

When an agent is already executing (tools have been called, data has been gathered) and hits a decision point where only the human can resolve what they want, it uses `ask_clarification` with type `"intent"`. This produces an `AgentResult(status="clarification_needed")` that propagates upward through the hierarchy until it reaches the human.

This is handled entirely by the clarification protocol — see [19_clarification_protocol.md](19_clarification_protocol.md).

The distinction from a pre-task ambiguity gate:
- **Pre-task**: agent has not started, asks synchronously in the same turn.
- **Mid-task**: agent has partially executed, pauses and waits for an async answer, then resumes from where it stopped.

---

## Interrupt and resume

Some HITL flows require the agent to pause mid-task, wait for an asynchronous human response, and then resume.

The pattern:
1. Agent reaches a confirmation point and cannot proceed without input.
2. Agent saves its current state to persistent memory (see [14_persistent_memory.md](14_persistent_memory.md)) tagged with the session ID.
3. Agent returns a response to the user explaining what it needs and that it is waiting.
4. Human reviews and responds (possibly hours later).
5. A new agent run is started with the same session ID; the agent rehydrates its state from memory and continues.

```python
# ai/memory/persistent.py

def save_pending_confirmation(
    session_id: str,
    workspace_id: int,
    action_description: str,
    pending_tool: str,
    pending_arguments: dict,
) -> None:
    ...  # saves to ai_agent_memory with type="pending_confirmation"


def load_pending_confirmation(session_id: str) -> dict | None:
    ...  # retrieves by session_id and type
```

The agent runner checks for a pending confirmation at the start of each run when the session ID is provided.

---

## HITL in subagents

Subagents do not implement HITL. They return a `FAILED` result when a dangerous operation is required but confirmation has not been received, and state what confirmation is needed.

The orchestrator surfaces this to the user and handles the confirmation flow. Once confirmed, the orchestrator re-runs the subagent with the confirmed action explicitly included in the task description.

```
# Orchestrator re-runs after user confirmation
"Delete the record 'Smith Project' (rec_abc123). 
The user has explicitly confirmed this deletion."
```

The subagent receives the explicit confirmation in the task text and proceeds.

---

## What HITL must NOT do

- Ask the user to confirm read operations.
- Ask multiple clarifying questions in a single turn.
- Infer confirmation from a previous message in the conversation ("you said yes earlier").
- Retry a dangerous operation without re-confirmation after a failure.
- Silently proceed if `confirm_action_tool` was not called.
- Conflate a confirmation gate (safety) with an intent clarification (understanding) — they use different tools and have different resume patterns.
