# 22 — Background Tasks Contract

## The problem

The synchronous `AgentRunner.run()` path ties the agent's entire execution to the lifetime of an HTTP request. For long-running orchestrations — multi-step workflows, large report generation, batch processing — the HTTP connection times out and the client gets no result. Even if it completes within the timeout, the user has no visibility into progress.

Background tasks decouple agent execution from HTTP connections. The client starts a task and gets a task ID. It polls (or subscribes via SSE) for progress events. The agent runs in the background, emitting events and storing state, until it completes, fails, or needs clarification.

---

## Background task model

```python
# models/tables/ai/agent_task.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.sql import func


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The conversation session this task belongs to (see contract 28).
    # When set, the task result is appended to the session on completion.
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Status values: pending | running | clarification_needed | complete | failed | cancelled | timed_out
    input_message: Mapped[str] = mapped_column(String, nullable=False)
    result_content: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    clarification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Stores ClarificationRequest fields when status == clarification_needed
    conversation_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Stored for resume when status == clarification_needed
    total_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

---

## Progress event model

Progress events are stored in the database so clients can retrieve the full history, not just the current status.

```python
# models/tables/ai/agent_task_event.py

class AgentTaskEvent(Base):
    __tablename__ = "agent_task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("agent_tasks.task_id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # Same types as StreamEvent: iteration_start, tool_call_start, tool_complete, etc.
    event_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

One row per event, in insertion order. Clients replay the event log to reconstruct progress.

---

## Task lifecycle

```
POST /agents/tasks        → status: pending
      │
      ▼ (worker picks up)
   status: running
      │
      ├── tool_call_start, tool_complete events written to agent_task_events
      │
      ├── status: clarification_needed (agent called ask_clarification)
      │         │
      │         └── POST /agents/tasks/{id}/answer  → status: running again
      │
      ├── status: complete   (agent returned end_turn result)
      ├── status: failed     (agent returned failed result)
      ├── status: cancelled  (client sent DELETE /agents/tasks/{id})
      └── status: timed_out  (timeout_at exceeded before completion)
```

---

## HTTP API

### Start a task

```
POST /api/v1/agents/tasks
Content-Type: application/json

{
  "agent": "record_agent",
  "message": "Create a record for the Smith project...",
  "conversation_id": "conv_xyz789"
}
```

`conversation_id` is optional. When provided, the task is linked to the conversation session and its result is appended to the session on completion (see [28_conversation_session.md](28_conversation_session.md)).

Response:
```json
{
  "task_id": "task_abc123",
  "status": "pending",
  "session_id": "f3a1-...",
  "conversation_id": "conv_xyz789",
  "poll_url": "/api/v1/agents/tasks/task_abc123",
  "events_url": "/api/v1/agents/tasks/task_abc123/events"
}
```

### Poll task status

```
GET /api/v1/agents/tasks/{task_id}
```

Response (running):
```json
{
  "task_id": "task_abc123",
  "status": "running",
  "events_since": 4
}
```

Response (clarification_needed):
```json
{
  "task_id": "task_abc123",
  "status": "clarification_needed",
  "clarification": {
    "question": "Which category should the record use?",
    "clarification_type": "intent",
    "referenced_data": {"record_draft": "Smith Project"},
    "options": ["type_a", "type_b", "Leave uncategorised"]
  }
}
```

Response (complete):
```json
{
  "task_id": "task_abc123",
  "status": "complete",
  "result": "Record 'Smith Project' created successfully. ID: rec_abc123.",
  "total_iterations": 3,
  "total_input_tokens": 1240,
  "total_output_tokens": 180
}
```

### Answer a clarification

```
POST /api/v1/agents/tasks/{task_id}/answer
Content-Type: application/json

{
  "answer": "type_a"
}
```

Response:
```json
{
  "task_id": "task_abc123",
  "status": "running"
}
```

### Get event log

```
GET /api/v1/agents/tasks/{task_id}/events?after=0
```

Response:
```json
{
  "events": [
    {"id": 1, "type": "iteration_start", "data": {"iteration": 1}, "created_at": "..."},
    {"id": 2, "type": "tool_call_start", "data": {"tool_name": "create_record", "arguments": {...}}, "created_at": "..."},
    {"id": 3, "type": "tool_complete", "data": {"tool_name": "create_record", "success": true}, "created_at": "..."}
  ],
  "next_after": 3
}
```

Use `?after=N` for incremental polling. The client remembers the last event ID and requests only new events.

### Cancel a task

```
DELETE /api/v1/agents/tasks/{task_id}
```

Marks the task `cancelled`. If the worker is currently executing a tool call, the tool call completes but the agent loop exits on the next iteration check.

---

## Worker implementation

Background tasks run via the existing RQ worker infrastructure (see `Backend_architecture/16_background_jobs.md`). The job function uses `AgentRunner.stream()` and writes each event to `agent_task_events`.

```python
# services/commands/ai/run_agent_task.py

def run_agent_task(task_id: str) -> None:
    task = AgentTask.query.filter_by(task_id=task_id).first()
    task.status = "running"
    task.started_at = datetime.utcnow()
    db.session.commit()

    agent_ctx = rebuild_agent_context(task)
    config = load_agent_config(task.agent_name, task.prompt_version)
    runner = AgentRunner(get_provider(), config)

    for event in runner.stream(task.input_message, agent_ctx):
        # Check for cancellation on each event
        db.session.refresh(task)
        if task.status == "cancelled":
            return

        _write_task_event(task.task_id, event)

        if event.type == "clarification":
            task.status = "clarification_needed"
            task.clarification = {
                "question": event.data["question"],
                "clarification_type": event.data["clarification_type"],
                "referenced_data": event.data["referenced_data"],
                "options": event.data["options"],
            }
            task.conversation_history = [m.__dict__ for m in event.data["conversation_history"]]
            db.session.commit()
            return  # job exits; worker resumes when answer arrives

        if event.type == "session_complete":
            task.status = "complete"
            task.result_content = event.data["content"]
            task.total_iterations = event.data["total_iterations"]
            task.total_input_tokens = event.data["total_input_tokens"]
            task.total_output_tokens = event.data["total_output_tokens"]
            task.completed_at = datetime.utcnow()
            db.session.commit()

            # Update the conversation session so the next user message has context
            # about what the background task accomplished (see contract 28)
            if task.conversation_id and task.result_content:
                _update_conversation_session(task)
            return

        if event.type in ("session_failed", "session_max_iter"):
            task.status = "failed"
            task.error = event.data.get("error", "Max iterations reached")
            task.completed_at = datetime.utcnow()
            db.session.commit()
            return

    db.session.commit()
```

### Resuming after clarification

When the client sends `POST /agents/tasks/{id}/answer`, a new RQ job is enqueued:

```python
def resume_agent_task(task_id: str, answer: str) -> None:
    task = AgentTask.query.filter_by(task_id=task_id).first()
    history = [Message(**m) for m in task.conversation_history]
    config = load_agent_config(task.agent_name, task.prompt_version)
    runner = AgentRunner(get_provider(), config)

    task.status = "running"
    task.clarification = None
    task.conversation_history = None
    db.session.commit()

    for event in runner.stream_with_history(history, answer, agent_ctx):
        ...  # same event loop as above
```

The resumed job always uses the same `prompt_version` as the original task.

---

## Timeout policy

| Agent type | Default timeout |
|---|---|
| Single agent | 5 minutes |
| Orchestrator | 30 minutes |
| Research subagent | 2 minutes |

Set `timeout_at = datetime.utcnow() + timedelta(minutes=N)` at task creation. A scheduled job (see `Backend_architecture/37_scheduled_jobs.md`) runs every 5 minutes to mark timed-out tasks:

```python
# services/commands/ai/timeout_stale_agent_tasks.py
def timeout_stale_agent_tasks(ctx: ServiceContext) -> dict:
    count = (
        db.session.query(AgentTask)
        .filter(
            AgentTask.status.in_(["pending", "running"]),
            AgentTask.timeout_at < datetime.utcnow(),
        )
        .update({"status": "timed_out", "completed_at": datetime.utcnow()})
    )
    db.session.commit()
    return {"timed_out_count": count}
```

---

## Conversation session update on completion

When `task.conversation_id` is set, the worker appends the task result to the conversation session after `session_complete`. The next user message in that conversation will have full context of what the background task accomplished.

```python
# services/commands/ai/run_agent_task.py

def _update_conversation_session(task: AgentTask) -> None:
    from my_app.ai.conversation.session import append_conversation_turn, update_session_entities
    from my_app.models.tables.ai.conversation_session import ConversationSession

    session = (
        db.session.query(ConversationSession)
        .filter_by(conversation_id=task.conversation_id)
        .first()
    )
    if session is None or session.expires_at < datetime.utcnow():
        return  # session expired while task was running — nothing to update

    append_conversation_turn(
        session=session,
        user_message=task.input_message,
        agent_response=task.result_content,
        agent_name=task.agent_name,
    )
    update_session_entities(session, task.result_content, get_provider())
```

The session update runs inside the worker, not the HTTP process. It uses its own DB session — the same isolation rule that applies to all worker operations (see "What background tasks must NOT do").

---

## What background tasks must NOT do

- Share a DB session between the web process and the worker — workers use their own session.
- Silently drop events when the worker crashes — use RQ's built-in retry for the job, not for individual events.
- Resume a cancelled task — once cancelled, a task is terminal.
- Store raw conversation history in a cookie or URL — it goes in the DB, retrieved by task_id.
- Block the HTTP thread while waiting for the worker — the router returns immediately with the task ID.
