# 25 — Workspace Agent Configuration Contract

## Purpose

The `AgentConfig` in `agent.py` defines the default behavior for an agent across all workspaces. Workspace agent config allows a workspace admin or platform admin to override specific settings — model, prompt version, token limits — for a particular workspace, without touching the shared agent code.

This enables canary prompt rollouts, per-workspace model selection, and disabling specific agents for specific workspaces.

---

## Configuration model

```python
# models/tables/ai/workspace_agent_config.py

class WorkspaceAgentConfig(Base):
    __tablename__ = "workspace_agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model_id_override: Mapped[str | None] = mapped_column(String, nullable=True)
    # If set, overrides AgentConfig.llm_config.model for this workspace
    prompt_version_override: Mapped[str | None] = mapped_column(String, nullable=True)
    # If set, overrides AgentConfig.prompt_version (and loads that version's text)
    max_session_tokens_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_iterations_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_prompt_suffix: Mapped[str | None] = mapped_column(String, nullable=True)
    # Appended to the end of the active system prompt — for workspace-specific instructions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_name"),
    )
```

One row per (workspace, agent) pair. Missing row means the workspace uses all defaults.

---

## Config resolution

When an agent session starts, the runner resolves the effective config by merging the base `AgentConfig` with any workspace-level override:

```python
# ai/agents/base.py

def resolve_agent_config(
    base_config: AgentConfig,
    workspace_id: int,
) -> AgentConfig:
    override = (
        db.session.query(WorkspaceAgentConfig)
        .filter_by(workspace_id=workspace_id, agent_name=base_config.name)
        .first()
    )

    if override is None:
        return base_config

    if not override.is_enabled:
        raise AgentDisabledError(
            f"Agent '{base_config.name}' is not enabled for this workspace."
        )

    # Build effective LLM config
    effective_llm_config = base_config.llm_config or _default_llm_config()
    if override.model_id_override:
        effective_llm_config = LLMConfig(
            **{**vars(effective_llm_config), "model": override.model_id_override}
        )

    # Resolve prompt version
    effective_version = override.prompt_version_override or base_config.prompt_version
    effective_prompt = PromptRegistry.get(base_config.name, effective_version)
    if override.system_prompt_suffix:
        effective_prompt = effective_prompt + "\n\n" + override.system_prompt_suffix

    return AgentConfig(
        name=base_config.name,
        system_prompt=effective_prompt,
        prompt_version=effective_version,
        tools=base_config.tools,
        max_iterations=override.max_iterations_override or base_config.max_iterations,
        max_session_tokens=override.max_session_tokens_override or base_config.max_session_tokens,
        max_research_depth_before_clarification=base_config.max_research_depth_before_clarification,
        llm_config=effective_llm_config,
        context_budget=base_config.context_budget,
    )
```

Call `resolve_agent_config` at session start, before the first LLM call:

```python
# In AgentRunner.run() and AgentRunner.stream():
effective_config = resolve_agent_config(self.config, agent_ctx.workspace_id)
# Use effective_config for llm_config, system_prompt, max_iterations, etc.
```

---

## `AgentDisabledError`

```python
# errors/ai.py
class AgentDisabledError(DomainError):
    code = "AGENT_DISABLED"
    http_status = 403
```

The HTTP router surfaces this as a 403 with a message explaining the agent is not available for this workspace.

---

## What can be overridden

| Field | Who can set | Effect |
|---|---|---|
| `is_enabled` | Platform admin | Disables the agent entirely for this workspace |
| `model_id_override` | Platform admin | Different LLM model for this workspace (must be in approved list) |
| `prompt_version_override` | Platform admin | Use a specific prompt version (canary rollout) |
| `max_session_tokens_override` | Platform admin | Lower token cap for cost-sensitive workspaces |
| `max_iterations_override` | Platform admin | Fewer iterations for faster, cheaper sessions |
| `system_prompt_suffix` | Workspace admin | Append workspace-specific context to the prompt |

Workspace admins may only set `system_prompt_suffix`. All other overrides require platform admin access. This prevents a workspace from bypassing cost controls by overriding its own token limits.

---

## `system_prompt_suffix` rules

The suffix is appended after the base prompt. It must not:
- Contradict safety rules in the base prompt.
- Grant permissions that the agent's tool scope does not support.
- Contain user-supplied text — it is set by a workspace admin, not by end users.

The suffix is loaded and stored in the DB; it does not live in the file system. It is treated as workspace-specific configuration, not as versioned code.

Suffix length limit: 500 characters. Long suffixes indicate a design problem — the agent's base prompt should cover the use case, or a new agent should be defined.

---

## Model approval list

`model_id_override` must be a model ID from the approved list. This list is maintained by the platform admin and stored in application config (not in the database):

```python
# config/default.py
APPROVED_AI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]
```

Validation at config write time:

```python
if override.model_id_override not in current_app.config["APPROVED_AI_MODELS"]:
    raise ValidationError(
        f"Model '{override.model_id_override}' is not in the approved list."
    )
```

---

## Admin API

```
GET  /api/v1/admin/workspaces/{id}/agent-configs        → list all agent configs for workspace
GET  /api/v1/admin/workspaces/{id}/agent-configs/{name} → get config for specific agent
PUT  /api/v1/admin/workspaces/{id}/agent-configs/{name} → create or update (platform admin only)
DELETE /api/v1/admin/workspaces/{id}/agent-configs/{name} → remove override, revert to defaults

GET  /api/v1/workspace/agent-configs                    → workspace admin: view own configs
PATCH /api/v1/workspace/agent-configs/{name}            → workspace admin: update suffix only
```

---

## Canary rollout pattern

To roll out `record_agent` prompt v3 to 10% of workspaces:

1. Select canary workspace IDs.
2. Insert `WorkspaceAgentConfig` records with `prompt_version_override = "v3"` for those workspaces.
3. Monitor evaluation metrics for canary vs. control (see [24_agent_evaluation.md](24_agent_evaluation.md)).
4. Promote: remove overrides (reverts to base config, update base to v3) or rollback (delete overrides).

Canary records are temporary. Remove them after the rollout decision is made.

---

## What workspace agent config must NOT do

- Allow workspace admins to override `is_enabled`, `model_id_override`, or `max_session_tokens_override` — those are platform-admin-only fields.
- Cache the resolved config across sessions — resolve fresh at each session start (config can change without restart).
- Allow `system_prompt_suffix` to inject instruction-like text that could override base prompt safety rules — the base prompt's non-negotiable rules always take precedence.
- Use `model_id_override` to set a model not in the approved list.
- Store prompt text in the DB — only version identifiers and suffixes. Full prompts live in the file system.
