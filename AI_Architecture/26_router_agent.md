# 26 — Router Agent Contract

## Definition

The router agent is the single entry point for all free-text user requests. It classifies the user's intent, extracts relevant entities from the message, and dispatches to the correct agent. It does not execute domain logic. It does not call backend tools. It makes one LLM call and routes.

Every application with a free-text AI interface has exactly one router. Fixed-function endpoints (a button that always creates a record) bypass the router and wire directly to their agent.

---

## Architecture position

```
User Message (free-text)
        ↓
  [Intent Router]
        │  classifies intent, extracts entities, checks confidence
        ↓               ↓                    ↓
   Tier 1            Tier 2               Tier 3
 Single Agent    Workflow Orch.       Planning Orch.
                                      (contract 27)
```

The router is not an `AgentRunner`. It is a lightweight dispatcher that makes one structured LLM call and delegates. It returns `AgentResult` so the HTTP layer handles it identically to any other agent call.

---

## Folder structure

```
ai/
├── router/
│   ├── router.py          # IntentRouter — classification + dispatch
│   └── registry.py        # IntentRegistry — intent registration, prompt builder
└── agents/
    └── record_agent/
        ├── agent.py
        ├── intent.py       # IntentDefinition for this agent — co-located with the agent
        └── system_prompt.md
```

Intent definitions live next to their agent. The registry is populated at app startup.

---

## `IntentDefinition`

```python
# ai/router/registry.py
from dataclasses import dataclass, field
from typing import Literal
from my_app.ai.agents.base import AgentConfig


@dataclass
class IntentDefinition:
    name: str
    # Unique identifier. Snake_case. Examples: "create_record", "send_invoice", "generate_report"

    description: str
    # One sentence describing what the user wants to achieve.
    # This sentence appears directly in the classification prompt — write it for the LLM, not for docs.
    # Example: "The user wants to create a new record in the workspace."

    examples: list[str]
    # 3–5 example user messages that should match this intent.
    # Used as few-shot context in the classification prompt.

    agent_config: AgentConfig
    # The agent that handles this intent. For Tier 1: a single agent config.
    # For Tier 2: an orchestrator config.

    tier: Literal[1, 2]
    # Tier 1: single agent. Tier 2: orchestrator.
    # Tier 3 is the router's fallback path — it is not registered as an intent.

    required_entities: list[str] = field(default_factory=list)
    # Entity names the router must extract before dispatching.
    # If any are missing from the user's message, the router asks for them
    # before routing — not the agent.
    # Example: ["record_name"] for create_record intent.
```

### `intent.py` — co-located intent definition

```python
# ai/agents/record_agent/intent.py
from my_app.ai.router.registry import IntentDefinition
from my_app.ai.agents.record_agent import agent as record_agent

CREATE_RECORD_INTENT = IntentDefinition(
    name="create_record",
    description="The user wants to create a new record in the workspace.",
    examples=[
        "Create a record for the Smith project",
        "Add a new record called Q2 Review in category type_a",
        "I need to create a record",
    ],
    agent_config=record_agent.CONFIG,
    tier=1,
    required_entities=["record_name"],
)

LIST_RECORDS_INTENT = IntentDefinition(
    name="list_records",
    description="The user wants to see a list of records in the workspace.",
    examples=[
        "Show me all records",
        "List records in category type_b",
        "What records do we have?",
    ],
    agent_config=record_agent.CONFIG,
    tier=1,
    required_entities=[],
)
```

---

## `IntentRegistry`

```python
# ai/router/registry.py

class IntentRegistry:
    def __init__(self):
        self._intents: dict[str, IntentDefinition] = {}

    def register(self, intent: IntentDefinition) -> None:
        if intent.name in self._intents:
            raise ValueError(f"Intent '{intent.name}' is already registered.")
        self._intents[intent.name] = intent

    def get(self, name: str) -> IntentDefinition | None:
        return self._intents.get(name)

    def all(self) -> list[IntentDefinition]:
        return list(self._intents.values())

    def build_classification_prompt(self) -> str:
        lines = [
            "You are an intent classifier. Given a user message, identify which intent "
            "it matches and extract the required entities.\n\n"
            "## Available intents\n"
        ]
        for intent in self._intents.values():
            lines.append(f"**{intent.name}**: {intent.description}")
            lines.append("Examples:")
            for ex in intent.examples:
                lines.append(f"  - {ex}")
            lines.append("")
        lines.append(
            "If the message does not match any intent, or matches multiple intents with "
            "equal confidence, set intent to null and confidence to 0.0."
        )
        return "\n".join(lines)
```

### Registry population at app startup

```python
# app.py or ai/__init__.py

from my_app.ai.router.registry import IntentRegistry
from my_app.ai.agents.record_agent.intent import CREATE_RECORD_INTENT, LIST_RECORDS_INTENT
from my_app.ai.agents.workflow_orchestrator.intent import CREATE_AND_NOTIFY_INTENT

INTENT_REGISTRY = IntentRegistry()
INTENT_REGISTRY.register(CREATE_RECORD_INTENT)
INTENT_REGISTRY.register(LIST_RECORDS_INTENT)
INTENT_REGISTRY.register(CREATE_AND_NOTIFY_INTENT)
```

---

## Classification — structured LLM call

The router makes one LLM call using a `classify_intent` tool to force structured output. It does not use `AgentRunner` — it calls the provider directly.

```python
# ai/router/router.py

CLASSIFY_INTENT_SCHEMA: dict = {
    "name": "classify_intent",
    "description": "Output the classification result for the user message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": ["string", "null"],
                "description": "The matched intent name, or null if no match.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score between 0.0 and 1.0.",
            },
            "entities": {
                "type": "object",
                "description": "Extracted entities from the user message. Keys match required_entities for the matched intent.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining why this intent was chosen.",
            },
        },
        "required": ["intent", "confidence", "entities", "reasoning"],
    },
}
```

```python
@dataclass
class RouterDecision:
    intent: str | None
    confidence: float
    entities: dict
    reasoning: str
```

```python
CONFIDENCE_THRESHOLD = 0.80
# Configurable. Below this threshold, the router asks for clarification rather than guessing.
```

---

## `IntentRouter`

```python
# ai/router/router.py
from my_app.ai.agents.base import AgentRunner, AgentResult, AgentContext, ClarificationRequest
from my_app.ai.providers.base import LLMProvider, LLMConfig, Message


class IntentRouter:

    def __init__(self, registry: IntentRegistry, provider: LLMProvider):
        self.registry = registry
        self.provider = provider

    def route(
        self,
        user_message: str,
        agent_ctx: AgentContext,
        session: ConversationSession | None = None,
    ) -> AgentResult:
        decision = self._classify(user_message, session)

        # Low confidence or no match — ask user to clarify before routing
        if decision.intent is None or decision.confidence < CONFIDENCE_THRESHOLD:
            return AgentResult(
                status="clarification_needed",
                clarification=ClarificationRequest(
                    question=(
                        "I'm not sure what you'd like to do. "
                        "Could you clarify your request?"
                    ),
                    clarification_type="intent",
                    context_gathered=f"Received message: {user_message}",
                    referenced_data={},
                    suggested_answers=[
                        intent.description
                        for intent in self.registry.all()[:5]
                    ],
                ),
                session_id=agent_ctx.session_id,
            )

        intent_def = self.registry.get(decision.intent)

        # Missing required entities — check session active_entities before asking
        missing = [
            entity for entity in intent_def.required_entities
            if not decision.entities.get(entity)
            and not (session and session.active_entities.get(entity))
        ]
        if missing:
            return AgentResult(
                status="clarification_needed",
                clarification=ClarificationRequest(
                    question=(
                        f"To {intent_def.description.lower().rstrip('.')}, "
                        f"I need: {', '.join(missing)}."
                    ),
                    clarification_type="intent",
                    context_gathered=f"Intent matched: {intent_def.name}. Missing: {missing}.",
                    referenced_data=decision.entities,
                    suggested_answers=[],
                ),
                session_id=agent_ctx.session_id,
            )

        # Merge session active_entities with classifier-extracted entities
        # Classifier-extracted values take precedence (they are from the current message)
        resolved_entities = {
            **(session.active_entities if session else {}),
            **decision.entities,
        }

        # Route to the matched agent — enrich message with all resolved entities
        enriched_message = self._enrich(user_message, resolved_entities, session)
        runner = AgentRunner(self.provider, intent_def.agent_config)
        return runner.run(enriched_message, agent_ctx)

    def _classify(
        self,
        user_message: str,
        session: ConversationSession | None,
    ) -> RouterDecision:
        system_prompt = self.registry.build_classification_prompt()

        # Include conversation context in the classification input — capped at ROUTER_HISTORY_TURNS
        context = build_router_context(session) if session else ""
        full_input = (
            f"{context}\n\n## Current message\n{user_message}"
            if context else user_message
        )

        response = self.provider.chat(
            messages=[Message(role="user", content=full_input)],
            tools=[CLASSIFY_INTENT_SCHEMA],
            config=LLMConfig(
                model=_router_model(),   # use a cheaper/faster model than agent calls
                system_prompt=system_prompt,
                tool_choice="required",  # force the LLM to call classify_intent
            ),
        )
        tool_call = response.tool_calls[0]
        args = tool_call.arguments
        return RouterDecision(
            intent=args.get("intent"),
            confidence=args.get("confidence", 0.0),
            entities=args.get("entities", {}),
            reasoning=args.get("reasoning", ""),
        )

    def _enrich(
        self,
        original_message: str,
        entities: dict,
        session: ConversationSession | None,
    ) -> str:
        lines = [original_message]
        if entities:
            entity_lines = "\n".join(f"  {k}: {v}" for k, v in entities.items())
            lines.append(f"\n[Pre-extracted entities]\n{entity_lines}")
        if session:
            agent_ctx_str = build_agent_context(session)
            if agent_ctx_str:
                lines.append(f"\n{agent_ctx_str}")
        return "\n".join(lines)
```

---

## Model selection for classification

The router's classification call should use a cheaper, faster model than the downstream agents. This is a single structured output call — it does not need reasoning depth.

```python
# config/default.py
ROUTER_MODEL = "claude-haiku-4-5"   # or gpt-4o-mini — fast, cheap, sufficient for classification
```

```python
def _router_model() -> str:
    return current_app.config.get("ROUTER_MODEL", "claude-haiku-4-5")
```

---

## HTTP entry point

The router replaces direct agent calls at the HTTP layer for free-text endpoints. The session is loaded before routing and updated after each complete turn. See [28_conversation_session.md](28_conversation_session.md) for session lifecycle details.

```python
# routers/api_v1/agent.py

@bp.route("/agents/chat", methods=["POST"])
def chat():
    user_message = request.json["message"]
    conversation_id = request.json.get("conversation_id")
    agent_ctx = build_agent_context_from_request(request)

    session = get_or_create_conversation_session(
        conversation_id=conversation_id,
        workspace_id=agent_ctx.workspace_id,
        user_id=agent_ctx.user_id,
    )

    result = IntentRouter(INTENT_REGISTRY, get_provider()).route(
        user_message=user_message,
        agent_ctx=agent_ctx,
        session=session,
    )

    if result.status == "complete":
        append_conversation_turn(
            session=session,
            user_message=user_message,
            agent_response=result.content,
            agent_name=result.agent_name,
        )
        update_session_entities(session, result.content, get_provider())
        return jsonify({
            "conversation_id": session.conversation_id,
            "reply": result.content,
        }), 200

    if result.status == "clarification_needed":
        cl = result.clarification
        return jsonify({
            "conversation_id": session.conversation_id,
            "status": "clarification_needed",
            "session_id": result.session_id,
            "question": cl.question,
            "context": cl.context_gathered,
            "referenced_data": cl.referenced_data,
            "options": cl.suggested_answers,
        }), 200

    if result.status == "failed":
        return jsonify({"error": result.error}), 500

    if result.status == "max_iterations":
        return jsonify({"reply": result.content}), 200
```

Fixed-function endpoints (buttons, form submissions) bypass the router entirely:

```python
# Correct — specific action, bypass router
@bp.route("/agents/records/create", methods=["POST"])
def create_record_direct():
    runner = AgentRunner(get_provider(), record_agent.CONFIG)
    result = runner.run(build_task_from_request(request), agent_ctx)
    ...
```

---

## Tier 3 fallback

When no intent matches (or confidence is below threshold and the user confirms they want a complex task), the router dispatches to the planning orchestrator (see contract 27).

```python
# In IntentRouter.route() — after low-confidence clarification is answered

if decision.intent is None:
    # No known intent — route to planning orchestrator
    from my_app.ai.agents.planning_orchestrator import agent as planning_agent
    runner = AgentRunner(self.provider, planning_agent.CONFIG)
    return runner.run(user_message, agent_ctx)
```

Until contract 27 is implemented, a `None` intent returns `clarification_needed` with the registered intent descriptions as options (the default low-confidence path above handles this automatically).

---

## What the router must NOT do

- Call backend commands or queries — it has no tools and no domain knowledge.
- Guess intent when confidence is below threshold — always ask the user to clarify.
- Cache classification results across users or sessions — every message is classified fresh.
- Use a slow, expensive model — classification is a cheap, structured task.
- Register Tier 3 as a named intent — Tier 3 is the fallback, not a classified path.
- Pass more than `ROUTER_HISTORY_TURNS` turns to the classifier — cap it to keep the classification call cheap.
- Route to an agent that is disabled for the workspace — check `resolve_agent_config` (see contract 25) before dispatching.
- Make more than one LLM call — classify in a single call and route.
