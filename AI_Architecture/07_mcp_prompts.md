# 07 — MCP Prompts Contract

## What MCP prompts are

MCP prompts are reusable, parameterized message templates stored in the MCP server. A client requests a prompt by name, passes arguments, and receives a ready-to-send list of messages that can be injected into a conversation.

Prompts are not system prompts for agents. They are building blocks that clients can compose — a way to standardize the starting context for common workflows without baking it into every client.

Use prompts when:
- A workflow has a standard opening context that multiple clients will reuse (onboarding a new workspace, generating a report, reviewing a set of records).
- The framing of a task requires domain knowledge that should not live inside the LLM client.
- You want to version and centralize how your application describes its own capabilities.

Do not use prompts for:
- One-off, highly specific tasks (build a standalone agent instead).
- Runtime data injection (use resources or tools for that — prompt arguments should be IDs or labels, not raw data blobs).

---

## Prompt definition and registration

```python
# ai/mcp/prompts/record_workflows.py
from mcp.server import Server
import mcp.types as types


def register(server: Server) -> None:

    @server.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        return [
            types.Prompt(
                name="review_records",
                description=(
                    "Prepares the assistant to review all active records in the workspace "
                    "and surface any that require attention."
                ),
                arguments=[
                    types.PromptArgument(
                        name="focus",
                        description="Optional. A category to focus the review on (e.g. 'type_a').",
                        required=False,
                    ),
                ],
            ),
            types.Prompt(
                name="create_record_guided",
                description="Guides the user through creating a new record step by step.",
                arguments=[],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(
        name: str, arguments: dict[str, str] | None
    ) -> types.GetPromptResult:
        if name == "review_records":
            return _review_records_prompt(arguments or {})
        if name == "create_record_guided":
            return _create_record_guided_prompt()
        raise ValueError(f"Unknown prompt: {name}")


def _review_records_prompt(arguments: dict) -> types.GetPromptResult:
    focus = arguments.get("focus", "all categories")
    return types.GetPromptResult(
        description="Review active workspace records",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        f"Please review the active records in this workspace, "
                        f"focusing on {focus}. "
                        "Use the list_records tool to fetch them. "
                        "Flag any records that appear incomplete, overdue, or need follow-up. "
                        "Present a concise summary grouped by status."
                    ),
                ),
            ),
        ],
    )


def _create_record_guided_prompt() -> types.GetPromptResult:
    return types.GetPromptResult(
        description="Guided record creation",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        "I'd like to create a new record. "
                        "Please ask me for the required information one step at a time, "
                        "then use the create_record tool to submit it once you have everything."
                    ),
                ),
            ),
        ],
    )
```

---

## Prompt naming

| Rule | Example |
|---|---|
| Snake case | `review_records`, `create_record_guided` |
| Verb + noun or noun + verb phrase | `review_records`, `generate_report`, `onboard_workspace` |
| Name describes the workflow, not the tool it uses | `review_records`, NOT `call_list_records` |
| Unique across the server | No two prompts share a name |

---

## Argument rules

- Keep arguments to IDs, labels, and short option values. Arguments are not data — they are selectors.
- Mark arguments `required=True` only when the prompt cannot produce a useful result without them.
- Document what each argument controls in the `description` field.
- Do not accept raw user text as a prompt argument — that belongs in the conversation, not the template.

```python
# Good — argument is a selector
types.PromptArgument(
    name="category",
    description="Filter the review to this category. One of: type_a, type_b, type_c.",
    required=False,
)

# Bad — argument is raw user input
types.PromptArgument(
    name="user_request",
    description="What the user typed.",
    required=True,
)
```

---

## Message structure rules

A prompt returns a list of `PromptMessage` objects. Each message has a `role` (`user` or `assistant`) and a `content` block.

- Most prompts return a single `user` message that sets the task framing.
- Multi-turn prompts (that pre-fill an assistant response to guide the conversation) must be used sparingly — they constrain the LLM heavily.
- Do not inject real data into the prompt template. Reference resources or instruct the LLM to call a tool to fetch data.

```python
# Correct — instructs the LLM to fetch data via tool
"Use the list_records tool to fetch the current records."

# Wrong — embeds real data in the template (stale, hard to maintain)
f"Here are the records: {json.dumps(real_records)}"
```

---

## Versioning

When a prompt's messages change in a way that alters the expected behavior, treat it as a breaking change. Rename the prompt (`review_records_v2`) rather than mutating the existing one. Clients that depend on the old prompt continue to work.

Deprecate old prompts by updating their `description` to indicate the replacement: `"Deprecated. Use review_records_v2 instead."` Remove them only after confirming no active clients reference them.

---

## What prompts must NOT do

- Execute queries or commands.
- Contain business logic or branching.
- Embed credentials, workspace IDs, or user-specific data.
- Return more than 5 messages — prompts are starting points, not full conversations.
