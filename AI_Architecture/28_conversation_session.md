# 28 — Conversation Session Contract

## The problem

Every `AgentRunner.run()` call is stateless. The router receives a fresh message each time. When the user says "change the category to type_b", the system sees those five words with no context — it cannot resolve "the category" or know which record the user means without access to what was said before.

Persistent memory (contract 14) covers long-lived facts and preferences. It does not cover the thread of an active conversation — what was just said, what was just created, what the user is currently working on.

---

## Three layers — which contract owns each

| Layer | What it covers | Contract | TTL |
|---|---|---|---|
| **Conversation history** | The user/agent message exchange across multiple requests | This contract (28) | Hours |
| **Active entity context** | Which specific records, IDs, or objects the user is currently working with | This contract (28) | Hours |
| **Persistent memory** | Preferences, long-lived facts, decisions, workflow state | [14_persistent_memory.md](14_persistent_memory.md) | Days / indefinite |

Do not store conversation history in persistent memory — it is too fine-grained and too short-lived. Do not store user preferences in conversation sessions — they outlive the conversation.

---

## Naming — `conversation_id` vs `session_id`

Existing contracts use `session_id` on `AgentContext` for the per-run agent identifier (one UUID per `AgentRunner.run()` call). This contract uses `conversation_id` for the multi-turn conversation thread to prevent confusion.

| Identifier | Scope | Owned by |
|---|---|---|
| `conversation_id` | One user conversation thread — survives across multiple requests | `ConversationSession` |
| `session_id` | One `AgentRunner.run()` call | `AgentContext` |

---

## Data model

```python
# models/tables/ai/conversation_session.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.sql import func


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    turns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # List of ConversationTurn dicts — user/assistant pairs in order
    active_entities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Structured entities the user has recently worked with — keyed by entity type
    # Example: {"record": {"id": "rec_123", "title": "Smith Project", "action": "created"}}
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Reset on every request — idle TTL, not a hard deadline
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

---

## `ConversationTurn`

```python
# ai/conversation/session.py
from dataclasses import dataclass
from typing import Literal


@dataclass
class ConversationTurn:
    role: Literal["user", "assistant"]
    content: str
    timestamp: str          # ISO 8601
    agent: str | None = None
    # Which agent produced the assistant turn — used for debugging and evaluation
```

A turn is one user message or one agent response. They are stored in pairs — user turn followed by assistant turn. Tool calls and internal reasoning are not stored here; those live in `AgentSessionLog`.

---

## Session lifecycle

### Create or load

```python
# services/commands/ai/get_or_create_conversation_session.py

SESSION_TTL_HOURS = 4

def get_or_create_conversation_session(
    conversation_id: str | None,
    workspace_id: int,
    user_id: int | None,
) -> ConversationSession:
    if conversation_id:
        session = (
            db.session.query(ConversationSession)
            .filter_by(conversation_id=conversation_id, workspace_id=workspace_id)
            .first()
        )
        if session and session.expires_at > datetime.utcnow():
            # Extend TTL on each active request
            session.expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
            session.last_active_at = datetime.utcnow()
            db.session.commit()
            return session
        # Expired or not found — start a fresh session

    new_session = ConversationSession(
        conversation_id=str(uuid4()),
        workspace_id=workspace_id,
        user_id=user_id,
        turns=[],
        active_entities={},
        expires_at=datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
    )
    db.session.add(new_session)
    db.session.commit()
    return new_session
```

An expired session silently starts fresh. The client is notified via the new `conversation_id` in the response.

### Append a turn

```python
# services/commands/ai/append_conversation_turn.py

def append_conversation_turn(
    session: ConversationSession,
    user_message: str,
    agent_response: str,
    agent_name: str,
) -> None:
    now = datetime.utcnow().isoformat()
    session.turns = session.turns + [
        {"role": "user", "content": user_message, "timestamp": now, "agent": None},
        {"role": "assistant", "content": agent_response, "timestamp": now, "agent": agent_name},
    ]
    # Cap history — keep last MAX_TURNS turns to bound token growth
    if len(session.turns) > MAX_TURNS * 2:
        session.turns = session.turns[-(MAX_TURNS * 2):]
    db.session.commit()


MAX_TURNS = 20  # 20 user/assistant pairs = 40 stored messages
```

### Update active entities

After each agent run, extract entity references from the agent result and update `active_entities`:

```python
# services/commands/ai/update_session_entities.py

EXTRACT_ENTITIES_SCHEMA: dict = {
    "name": "extract_entities",
    "description": "Extract structured entity references from an agent response.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "object",
                "description": (
                    "Entities mentioned in the response, keyed by entity type. "
                    "Example: {\"record\": {\"id\": \"rec_123\", \"title\": \"Smith Project\", \"action\": \"created\"}}"
                ),
            },
        },
        "required": ["entities"],
    },
}


def update_session_entities(
    session: ConversationSession,
    agent_result_content: str,
    provider: LLMProvider,
) -> None:
    if not agent_result_content:
        return

    response = provider.chat(
        messages=[Message(role="user", content=agent_result_content)],
        tools=[EXTRACT_ENTITIES_SCHEMA],
        config=LLMConfig(
            model=_router_model(),   # cheap/fast model — same as router classification
            system_prompt=(
                "Extract any entity references (records, invoices, reports, users, etc.) "
                "with their IDs, titles, and the action performed (created, updated, deleted, retrieved). "
                "If no entities are mentioned, return an empty object."
            ),
            tool_choice="required",
        ),
    )

    extracted = response.tool_calls[0].arguments.get("entities", {})
    if extracted:
        session.active_entities = {**session.active_entities, **extracted}
        db.session.commit()
```

---

## Context injection

### Building the context string

```python
# ai/conversation/session.py

ROUTER_HISTORY_TURNS = 5   # turns injected into the router classification prompt
AGENT_HISTORY_TURNS = 10   # turns injected into the agent message


def build_router_context(session: ConversationSession) -> str:
    """Compact context for the intent classifier — recent turns + active entities."""
    if not session.turns and not session.active_entities:
        return ""

    lines = ["## Recent conversation"]
    for turn in session.turns[-(ROUTER_HISTORY_TURNS * 2):]:
        lines.append(f"{turn['role'].capitalize()}: {turn['content']}")

    if session.active_entities:
        lines.append("\n## Currently working with")
        for entity_type, data in session.active_entities.items():
            lines.append(f"- {entity_type}: {data}")

    return "\n".join(lines)


def build_agent_context(session: ConversationSession) -> str:
    """Fuller context for the agent — more turns, same active entities."""
    if not session.turns and not session.active_entities:
        return ""

    lines = ["## Conversation history"]
    for turn in session.turns[-(AGENT_HISTORY_TURNS * 2):]:
        lines.append(f"{turn['role'].capitalize()}: {turn['content']}")

    if session.active_entities:
        lines.append("\n## Active entities (do not re-fetch unless the task requires it)")
        for entity_type, data in session.active_entities.items():
            lines.append(f"- {entity_type}: {data}")

    return "\n".join(lines)
```

### Injecting into the router

The router's classification call includes the conversation context:

```python
# ai/router/router.py

def _classify(self, user_message: str, session: ConversationSession | None) -> RouterDecision:
    context = build_router_context(session) if session else ""
    full_input = f"{context}\n\n## Current message\n{user_message}" if context else user_message

    response = self.provider.chat(
        messages=[Message(role="user", content=full_input)],
        tools=[CLASSIFY_INTENT_SCHEMA],
        config=LLMConfig(model=_router_model(), system_prompt=..., tool_choice="required"),
    )
    ...
```

With context, "change the category to type_b" classifies as `update_record` (not an unrecognised message) because the classifier sees that a record was just created.

### Injecting into the agent

The agent receives the user's message prefixed with the conversation context:

```python
# ai/router/router.py — in IntentRouter.route()

def route(
    self,
    user_message: str,
    agent_ctx: AgentContext,
    session: ConversationSession | None = None,
) -> AgentResult:
    decision = self._classify(user_message, session)
    ...
    agent_context_prefix = build_agent_context(session) if session else ""
    enriched_message = (
        f"{agent_context_prefix}\n\n## Current request\n{self._enrich(user_message, decision.entities)}"
        if agent_context_prefix
        else self._enrich(user_message, decision.entities)
    )

    runner = AgentRunner(self.provider, intent_def.agent_config)
    return runner.run(enriched_message, agent_ctx)
```

The agent sees a complete picture: what was discussed before, which entities are active, and what the user wants now. It does not need to re-fetch entities it already has in `active_entities` unless the task requires fresh data.

---

## HTTP endpoint — updated

```python
# routers/api_v1/agent.py

@bp.route("/agents/chat", methods=["POST"])
def chat():
    user_message = request.json["message"]
    conversation_id = request.json.get("conversation_id")   # optional — client tracks this
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
            agent_name=result.agent_name,    # add agent_name to AgentResult
        )
        update_session_entities(session, result.content, get_provider())
        return jsonify({
            "conversation_id": session.conversation_id,   # always returned — client stores this
            "reply": result.content,
        }), 200

    if result.status == "clarification_needed":
        # Do not append — the conversation is paused, not complete
        return jsonify({
            "conversation_id": session.conversation_id,
            "status": "clarification_needed",
            "question": result.clarification.question,
            "referenced_data": result.clarification.referenced_data,
            "options": result.clarification.suggested_answers,
        }), 200
    ...
```

The client stores `conversation_id` and sends it with every subsequent request. The server owns the conversation state — the client owns nothing but the ID.

---

## Session expiry and cleanup

A scheduled job (see `Backend_architecture/37_scheduled_jobs.md`) runs daily to delete expired sessions:

```python
# services/commands/ai/cleanup_expired_sessions.py

def cleanup_expired_sessions(ctx: ServiceContext) -> dict:
    count = (
        db.session.query(ConversationSession)
        .filter(ConversationSession.expires_at < datetime.utcnow())
        .delete()
    )
    db.session.commit()
    return {"deleted_sessions": count}
```

Expired sessions are deleted, not archived. They contain no data that is not already in `AgentSessionLog` or persistent memory.

---

## Interaction with persistent memory

Conversation sessions and persistent memory serve different time horizons and different purposes. They are complementary:

```
Request 1: "Create a record for the Smith project"
  → Session: turns=[...], active_entities={"record": {"id": "rec_123", "title": "Smith Project"}}
  → Persistent memory: (nothing — this is transient work, not a durable preference)

Request 2: "Change the category to type_b"
  → Session context injected → router classifies as update_record → entity resolved to rec_123
  → Agent updates rec_123 without asking "which record?"

Request 3 (next day, session expired): "Show me the Smith project"
  → No session context → router classifies as get_record
  → Agent calls list_records or get_record by name — re-fetches from the backend
  → Persistent memory: if the agent had stored "user often works on Smith Project" as a preference fact,
    it can surface that as a suggestion — but it cannot resolve the record ID without a tool call
```

The session handles the within-conversation reference resolution. Persistent memory handles the across-conversation knowledge that does not expire with the session.

---

## What conversation sessions must NOT do

- Store internal agent message history (tool calls, tool results) — that belongs in `AgentSessionLog`.
- Replace persistent memory for long-lived facts — sessions expire; preferences do not.
- Grow without bound — cap at `MAX_TURNS` pairs and enforce it on every append.
- Share sessions across workspaces — `workspace_id` is always part of the lookup.
- Allow the client to write session state — the client sends only the `conversation_id`; the server owns all session data.
- Keep expired sessions — delete them; they are not an audit trail.
- Inject the full session history into the classification prompt — use `ROUTER_HISTORY_TURNS` to keep the classifier call cheap.
