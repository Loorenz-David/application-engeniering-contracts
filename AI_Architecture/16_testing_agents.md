# 16 — Testing Agents Contract

## Testing philosophy

Agent tests verify two independent things:
1. **Tool correctness** — does the tool function correctly call the backend and return the expected result? (No LLM involved.)
2. **Agent behavior** — given a scripted LLM response sequence, does the agent call the right tools in the right order and produce the right final output? (LLM is mocked.)

Do not use a live LLM in unit or integration tests. LLM responses are non-deterministic and billed — use them only in manual or acceptance tests.

---

## Test pyramid

```
         ┌──────────────┐
         │  E2E / Manual │  Live LLM, real DB — run before release
         └──────┬───────┘
         ┌──────┴───────┐
         │  Integration  │  Real DB, mocked LLM — run in CI
         └──────┬───────┘
         ┌──────┴───────┐
         │     Unit      │  In-memory, fully mocked — fast, run always
         └──────────────┘
```

---

## Unit tests — tool functions

Test tool functions in isolation. Provide a fake `AgentContext` and mock the backend service call.

```python
# tests/ai/tools/record/test_create_record_tool.py
from unittest.mock import patch, MagicMock
from my_app.ai.tools.record.create_record_tool import create_record_tool
from my_app.ai.agents.base import AgentContext
import pytest
import uuid

AGENT_CTX = AgentContext(
    user_id=1,
    service_account_id="test_agent",
    workspace_id=7,
    session_id=str(uuid.uuid4()),
    scopes=frozenset(["create_record"]),
)


def test_create_record_tool_success():
    expected = {"id": "rec_abc123", "title": "Test Record", "status": "active"}

    with patch("my_app.ai.tools.record.create_record_tool.create_record") as mock_cmd:
        mock_cmd.return_value = expected
        result = create_record_tool(
            {"title": "Test Record", "category": "type_a"},
            AGENT_CTX,
        )

    assert result == expected
    mock_cmd.assert_called_once()
    ctx_used = mock_cmd.call_args[0][0]
    assert ctx_used.incoming_data["title"] == "Test Record"
    assert ctx_used.workspace_id == 7


def test_create_record_tool_missing_title():
    from my_app.errors.validation import ValidationError
    with pytest.raises(ValidationError, match="title is required"):
        create_record_tool({"category": "type_a"}, AGENT_CTX)


def test_create_record_tool_invalid_category():
    from my_app.errors.validation import ValidationError
    with pytest.raises(ValidationError, match="category must be one of"):
        create_record_tool({"title": "Test", "category": "invalid"}, AGENT_CTX)
```

Every tool must have tests for:
- The happy path (valid input, successful backend call).
- All required-field validation failures.
- All enum/format validation failures.
- Domain errors propagated from the backend (e.g., `NotFoundError`, `ConflictError`).

---

## Mock LLM provider

```python
# tests/ai/conftest.py
from my_app.ai.providers.base import LLMProvider, LLMResponse, Message, ToolCall
from dataclasses import dataclass, field


@dataclass
class MockLLMProvider:
    responses: list[LLMResponse] = field(default_factory=list)
    _call_count: int = field(default=0, init=False)

    def chat(self, messages: list[Message], tools: list[dict], config) -> LLMResponse:
        if self._call_count >= len(self.responses):
            raise ValueError("MockLLMProvider ran out of scripted responses")
        response = self.responses[self._call_count]
        self._call_count += 1
        return response

    def stream(self, messages, tools, config):
        raise NotImplementedError("Streaming not mocked")
```

---

## Integration tests — agent tool loop

Test the agent loop end-to-end with a mocked LLM and a real database. The test scripts the LLM's response sequence and verifies that the agent calls the correct tools and returns the expected final message.

```python
# tests/ai/agents/test_record_agent.py
from my_app.ai.agents.record_agent.agent import CONFIG
from my_app.ai.agents.base import AgentRunner, AgentContext
from my_app.ai.providers.base import LLMResponse, ToolCall
from tests.ai.conftest import MockLLMProvider
import uuid

AGENT_CTX = AgentContext(
    user_id=1,
    service_account_id="test_agent",
    workspace_id=7,
    session_id=str(uuid.uuid4()),
    scopes=frozenset(["create_record", "get_record"]),
)


def test_agent_creates_record_and_confirms(db_session):
    # Script the LLM:
    # Turn 1: LLM decides to call create_record
    # Turn 2: LLM receives the tool result and returns a final message

    provider = MockLLMProvider(responses=[
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(
                id="tc_001",
                name="create_record",
                arguments={"title": "Smith Project", "category": "type_a"},
            )],
            input_tokens=100,
            output_tokens=30,
            stop_reason="tool_use",
        ),
        LLMResponse(
            content="I've created the Smith Project record for you.",
            tool_calls=[],
            input_tokens=150,
            output_tokens=20,
            stop_reason="end_turn",
        ),
    ])

    runner = AgentRunner(provider, CONFIG)
    result = runner.run("Create a record called Smith Project.", AGENT_CTX)

    assert "Smith Project" in result
    assert provider._call_count == 2
```

---

## Golden-input tests

Golden-input tests pair a fixed user message with an expected sequence of tool calls. They verify that the agent's behavior does not regress when the system prompt or tools change.

```python
# tests/ai/agents/golden/test_record_agent_golden.py
import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "fixtures"


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text())


def test_golden_create_record(db_session):
    fixture = load_golden("create_record_flow")
    provider = MockLLMProvider(responses=_build_responses(fixture["llm_sequence"]))
    runner = AgentRunner(provider, CONFIG)
    result = runner.run(fixture["user_message"], AGENT_CTX)

    assert fixture["expected_tool_calls"] == _extract_tool_calls(provider)
    assert fixture["expected_phrase"] in result
```

Golden fixture format:
```json
{
  "user_message": "Create a record called Smith Project in category type_a.",
  "llm_sequence": [
    {"stop_reason": "tool_use", "tool_name": "create_record", "arguments": {"title": "Smith Project", "category": "type_a"}},
    {"stop_reason": "end_turn", "content": "I've created the Smith Project record."}
  ],
  "expected_tool_calls": ["create_record"],
  "expected_phrase": "Smith Project"
}
```

Store fixtures in `tests/ai/agents/golden/fixtures/`. Update fixtures intentionally when agent behavior changes.

---

## What must be tested

| Component | Required tests |
|---|---|
| Every tool function | Happy path, all validation failures, domain errors from backend |
| Every agent | At least one golden-input test per major intent the agent handles |
| MCP tool handlers | Error mapping (DomainError → TextContent), unknown tool name |
| AgentRunner | max_iterations cap, scope violation, tool dispatch |
| ContextManager | `fit()` with history under budget, over budget, boundary case |
| `authenticate_api_key` | Valid key, invalid key, expired key |
| `resolve_agent_context` | Called outside context (raises RuntimeError) |

---

## What must NOT be in automated tests

- Live LLM API calls (non-deterministic, billed).
- Real embedding calls in unit or integration tests (use a fake embedder that returns a fixed vector).
- Tests that depend on LLM model version behavior.
- Tests that write to a shared/staging database.
