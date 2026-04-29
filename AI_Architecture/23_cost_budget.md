# 23 — Cost Budget Contract

## The problem

Every LLM call costs money. A single misbehaving agent run or a workspace that triggers many expensive sessions can generate significant spend with no gate to stop it. Observability (logging costs) is not the same as enforcement (stopping costs). This contract defines how token budgets are set, tracked, and enforced per workspace.

---

## Budget model

```python
# models/tables/ai/workspace_budget.py

class WorkspaceBudget(Base):
    __tablename__ = "workspace_budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), unique=True, nullable=False, index=True)
    monthly_token_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    # Total tokens (input + output) allowed per calendar month
    soft_limit_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # Percentage of monthly_token_limit that triggers a warning (default 80%)
    tokens_used_this_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_month: Mapped[str] = mapped_column(String, nullable=False)
    # Format: "2026-04" — resets on the 1st of each month
    hard_limit_action: Mapped[str] = mapped_column(String, nullable=False, default="reject")
    # "reject": new sessions refused | "warn": allow but log loudly
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())
```

---

## Token cost table

Token costs vary by model and provider. Store a cost table that maps model IDs to input/output cost per 1000 tokens. This table is updated when providers change pricing — it is not hardcoded.

```python
# models/tables/ai/model_cost.py

class ModelCost(Base):
    __tablename__ = "model_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    input_cost_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    output_cost_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

The system stores token counts as integers — not dollar amounts — and calculates cost on read. Token counts never become stale if pricing changes; cost calculations do.

---

## Pre-flight budget check

Before starting any agent session (synchronous or background), check the workspace budget:

```python
# ai/agents/base.py

def _check_budget(workspace_id: int, agent_name: str) -> None:
    budget = (
        db.session.query(WorkspaceBudget)
        .filter_by(workspace_id=workspace_id, budget_month=_current_month())
        .first()
    )
    if budget is None:
        return  # no budget set — allow (opt-in enforcement)

    if budget.tokens_used_this_month >= budget.monthly_token_limit:
        if budget.hard_limit_action == "reject":
            raise BudgetExceededError(
                f"Workspace {workspace_id} has reached its monthly token limit. "
                "No new agent sessions can be started until the budget resets."
            )
        else:
            logger.warning(
                "budget_hard_limit_exceeded",
                extra={
                    "event": "agent.budget.hard_limit",
                    "workspace_id": workspace_id,
                    "agent_name": agent_name,
                    "tokens_used": budget.tokens_used_this_month,
                    "token_limit": budget.monthly_token_limit,
                },
            )

    soft_limit = int(budget.monthly_token_limit * budget.soft_limit_pct / 100)
    if budget.tokens_used_this_month >= soft_limit:
        logger.warning(
            "budget_soft_limit_reached",
            extra={
                "event": "agent.budget.soft_limit",
                "workspace_id": workspace_id,
                "tokens_used": budget.tokens_used_this_month,
                "soft_limit": soft_limit,
                "token_limit": budget.monthly_token_limit,
            },
        )
```

Call `_check_budget` at the start of `AgentRunner.run()` and `AgentRunner.stream()`, before any LLM call.

---

## `BudgetExceededError`

```python
# errors/ai.py
from my_app.errors.base import DomainError

class BudgetExceededError(DomainError):
    code = "BUDGET_EXCEEDED"
    http_status = 429
```

Surfaces to the HTTP router as a 429 response. The response body includes when the budget resets:

```json
{
  "error": "BUDGET_EXCEEDED",
  "message": "Monthly token limit reached. Budget resets on 2026-05-01.",
  "reset_date": "2026-05-01"
}
```

---

## Post-session token accumulation

After every session completes (success, failure, or max_iterations), update the workspace's token count:

```python
# services/commands/ai/accumulate_session_tokens.py

def accumulate_session_tokens(ctx: ServiceContext) -> dict:
    session_id = ctx.incoming_data["session_id"]
    workspace_id = ctx.incoming_data["workspace_id"]
    input_tokens = ctx.incoming_data["input_tokens"]
    output_tokens = ctx.incoming_data["output_tokens"]
    total = input_tokens + output_tokens
    current_month = _current_month()

    budget = (
        db.session.query(WorkspaceBudget)
        .filter_by(workspace_id=workspace_id, budget_month=current_month)
        .with_for_update()
        .first()
    )
    if budget:
        budget.tokens_used_this_month += total
        db.session.commit()

    return {"accumulated_tokens": total}
```

Use `SELECT FOR UPDATE` to prevent race conditions when multiple sessions complete concurrently. See `Backend_architecture/32_concurrency.md`.

---

## Monthly budget reset

A scheduled job runs on the 1st of each month and creates new budget records for the new month:

```python
# services/commands/ai/reset_monthly_budgets.py

def reset_monthly_budgets(ctx: ServiceContext) -> dict:
    new_month = _current_month()
    # For each workspace with a budget record, insert a new record for the new month.
    # Previous month records are retained for audit — never deleted.
    existing = (
        db.session.query(WorkspaceBudget)
        .filter_by(budget_month=_previous_month())
        .all()
    )
    created = 0
    for old in existing:
        new_record = WorkspaceBudget(
            workspace_id=old.workspace_id,
            monthly_token_limit=old.monthly_token_limit,
            soft_limit_pct=old.soft_limit_pct,
            tokens_used_this_month=0,
            budget_month=new_month,
            hard_limit_action=old.hard_limit_action,
        )
        db.session.add(new_record)
        created += 1
    db.session.commit()
    return {"budgets_reset": created}
```

---

## Per-session token cap

In addition to the monthly workspace budget, each individual session has a per-session token cap. This prevents a single runaway agent from consuming the entire monthly budget:

```python
# ai/agents/base.py

@dataclass
class AgentConfig:
    ...
    max_session_tokens: int = 50_000  # hard cap per single run
```

If the session accumulates more than `max_session_tokens` during the loop, the runner stops and returns `AgentResult(status="failed", error="Session token limit exceeded")`.

```python
# In the loop — after each LLM response:
if self._accumulated_input_tokens + self._accumulated_output_tokens > self.config.max_session_tokens:
    return AgentResult(
        status="failed",
        error="Session token limit exceeded.",
        session_id=agent_ctx.session_id,
        ...
    )
```

---

## Budget admin API

Workspace admins can view and update their budget via the backend API:

```
GET  /api/v1/workspace/budget          → current usage and limits
PUT  /api/v1/workspace/budget          → update monthly_token_limit, soft_limit_pct
GET  /api/v1/workspace/budget/history  → previous months' usage
```

Platform admins can set budgets for any workspace. Workspace admins can view but not increase their own limit beyond what the platform admin has set.

---

## What cost budgets must NOT do

- Enforce limits mid-session — the pre-flight check gates session start; in-flight sessions are never killed by budget enforcement (that would corrupt state).
- Use dollar amounts as the stored unit — store token counts, calculate cost on read.
- Apply a global budget across all workspaces — each workspace has its own budget.
- Allow `tokens_used_this_month` to go negative — use `MAX(0, ...)` on read if corrections are needed.
- Block the HTTP response while doing the budget update — accumulate tokens asynchronously via a background command after the session completes.
