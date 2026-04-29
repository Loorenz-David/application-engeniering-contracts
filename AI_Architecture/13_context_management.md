# 13 — Context Management Contract

## The problem

Every LLM has a finite context window. As a conversation grows, messages accumulate and eventually exceed the model's token limit. Beyond cost — long contexts are expensive — exceeding the limit causes the request to fail.

Context management is the strategy for keeping the message history within budget while preserving the information the agent needs to complete its task.

---

## Context window budget

Every agent configuration defines a token budget:

```python
@dataclass
class ContextBudget:
    max_total_tokens: int        # hard cap — the model's context window size
    system_prompt_reserve: int   # tokens reserved for the system prompt (static)
    tool_schema_reserve: int     # tokens reserved for tool definitions
    response_reserve: int        # tokens reserved for the model's response (max_tokens)
    history_budget: int          # computed: max_total - system - tools - response
```

Example for a model with a 128k token window:
```python
ContextBudget(
    max_total_tokens=128_000,
    system_prompt_reserve=2_000,
    tool_schema_reserve=3_000,
    response_reserve=4_096,
    history_budget=118_904,    # what remains for message history
)
```

The `history_budget` is what the context manager works within. When the accumulated message history exceeds this budget, the manager reduces it.

---

## Context manager

```python
# ai/memory/context.py
from my_app.ai.providers.base import Message


class ContextManager:

    def __init__(self, budget: ContextBudget, token_counter: callable):
        self.budget = budget
        self._count_tokens = token_counter   # provider-specific — see below

    def fit(self, messages: list[Message]) -> list[Message]:
        total = sum(self._count_tokens(m.content or "") for m in messages)
        if total <= self.budget.history_budget:
            return messages
        return self._reduce(messages, total)

    def _reduce(self, messages: list[Message], total: int) -> list[Message]:
        # Strategy: summarize oldest messages, keep recent ones intact.
        # Always keep the last N messages unmodified (N = min(10, len(messages))).
        anchor = max(0, len(messages) - 10)
        old_messages = messages[:anchor]
        recent_messages = messages[anchor:]

        summary = self._summarize(old_messages)
        summary_message = Message(
            role="user",
            content=f"[Context summary — earlier conversation]\n{summary}",
        )
        return [summary_message] + recent_messages

    def _summarize(self, messages: list[Message]) -> str:
        # Summarization is a single, cheap LLM call with a small model.
        # The implementation lives in the provider adapter layer.
        raise NotImplementedError
```

---

## Token counting

Token counting is provider-specific. Each provider adapter must implement a `count_tokens(text: str) -> int` method.

When an exact count is expensive (requires an API call), use a token estimator instead:

```python
def estimate_tokens(text: str) -> int:
    # Conservative estimate: 1 token ≈ 3.5 characters for English text.
    return max(1, len(text) // 3)
```

Use the estimator for budget checks during the tool loop. Use the exact counter only when submitting the final request, if needed.

---

## Summarization strategy

When context must be reduced, summarize the oldest portion of the conversation. The summarization call must:

1. Use the smallest capable model — this is an auxiliary call, not the main task.
2. Include a focused prompt: "Summarize the following conversation history in 3–5 bullet points. Focus on decisions made, entities created or modified, and open questions."
3. Replace the summarized messages with a single `user` message containing the summary, prefixed with `[Context summary — earlier conversation]`.
4. Log the summarization event (tokens before, tokens after, session ID).

Summarization is triggered when `fit()` detects the history is over budget. It is not triggered on every turn.

---

## What to always preserve in context

Regardless of summarization, the following messages must never be removed or summarized:

| Message type | Why |
|---|---|
| The original user request | The agent must always know what it is trying to accomplish |
| Tool results from the current iteration | Removing them breaks the tool loop |
| Confirmation records (`confirm_action` results) | Safety — the agent must know what was confirmed |
| `FAILED` subagent results from the current run | The orchestrator needs to know what failed |

These messages are the "anchor" — they stay in the raw message list regardless of budget pressure.

---

## Multi-turn sessions

A multi-turn session is a conversation that spans multiple user messages. Between turns, the agent receives the user's new message and continues the existing message history.

```python
# ai/agents/base.py — multi-turn session

class AgentSession:
    def __init__(self, config: AgentConfig, provider: LLMProvider):
        self.runner = AgentRunner(provider, config)
        self.history: list[Message] = []
        self.context_manager = ContextManager(
            budget=config.context_budget,
            token_counter=provider.count_tokens,
        )

    def send(self, user_message: str, agent_ctx: AgentContext) -> str:
        self.history.append(Message(role="user", content=user_message))
        fitted = self.context_manager.fit(self.history)
        result = self.runner.run_with_history(fitted, agent_ctx)
        self.history.append(Message(role="assistant", content=result))
        return result
```

The session object manages history across turns. The `AgentRunner` is stateless — it only processes the history it is given.

---

## Cross-session context

Context is not automatically carried across sessions. A new session starts with an empty history.

If an agent needs to remember something from a previous session, it must be stored in persistent memory (see [14_persistent_memory.md](14_persistent_memory.md)) and retrieved at the start of the new session.

---

## What context management must NOT do

- Remove tool result messages that the LLM is currently waiting on.
- Summarize a message mid-tool-loop (only summarize between turns).
- Use a large model for summarization — this adds cost and latency to every context reduction.
- Silently drop messages — log every summarization event with tokens before and after.
- Allow the context to grow unbounded between turns — check budget before every LLM call.
