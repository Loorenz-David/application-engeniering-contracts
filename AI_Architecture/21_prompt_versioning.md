# 21 — Prompt Versioning Contract

## The problem

System prompts are the most operationally sensitive part of the AI layer. A change to `system_prompt.md` that degrades agent behavior affects every new session immediately, with no rollback path and no visibility into what changed. Meanwhile, in-flight sessions continue using whatever prompt was loaded at their start — creating a split-brain situation where different sessions behave differently with no traceability.

This contract defines how to version, deploy, evaluate, and roll back system prompts safely.

---

## Prompt version file structure

System prompts are versioned files, not a single mutable file:

```
ai/agents/<agent_name>/
├── agent.py
├── prompts/
│   ├── v1.md          # initial version
│   ├── v2.md          # updated version
│   └── v3.md          # current candidate
└── system_prompt.md   # symlink or alias — points to the active version
```

The active version is declared in `agent.py`, not inferred from the filename:

```python
# ai/agents/record_agent/agent.py
from pathlib import Path
from my_app.ai.agents.base import AgentConfig

PROMPT_VERSION = "v2"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text()

CONFIG = AgentConfig(
    name="record_agent",
    prompt_version=PROMPT_VERSION,
    system_prompt=SYSTEM_PROMPT,
    ...
)
```

`system_prompt.md` in the folder root is kept as a human-readable alias pointing to the active version — it is never loaded by the code directly.

---

## `AgentConfig` — prompt version field

```python
# ai/agents/base.py

@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    prompt_version: str           # e.g. "v2" — stored in session log for traceability
    tools: list[tuple[dict, callable]]
    max_iterations: int = 10
    max_research_depth_before_clarification: int = 5
    llm_config: LLMConfig | None = None
    context_budget: ContextBudget | None = None
```

`prompt_version` is recorded in `AgentSessionLog` (see [18_observability.md](18_observability.md)) for every session. This makes it possible to group session metrics by prompt version and compare quality across versions.

---

## `PromptRegistry`

The `PromptRegistry` is a module-level singleton that holds all versioned prompts for every agent. It is populated at app startup from the file system.

```python
# ai/agents/prompt_registry.py
from pathlib import Path
from typing import ClassVar


class PromptRegistry:
    _prompts: ClassVar[dict[str, dict[str, str]]] = {}
    # Structure: {agent_name: {version: prompt_text}}

    @classmethod
    def register(cls, agent_name: str, version: str, text: str) -> None:
        cls._prompts.setdefault(agent_name, {})[version] = text

    @classmethod
    def get(cls, agent_name: str, version: str) -> str:
        try:
            return cls._prompts[agent_name][version]
        except KeyError:
            raise ValueError(f"No prompt registered for {agent_name}/{version}")

    @classmethod
    def load_from_directory(cls, agent_name: str, prompts_dir: Path) -> None:
        for path in sorted(prompts_dir.glob("v*.md")):
            version = path.stem   # "v1", "v2", etc.
            cls.register(agent_name, version, path.read_text())
```

Load at app startup:

```python
# ai/__init__.py
from my_app.ai.agents.prompt_registry import PromptRegistry
from pathlib import Path

def register_all_prompts() -> None:
    agents_dir = Path(__file__).parent / "agents"
    for agent_dir in agents_dir.iterdir():
        prompts_dir = agent_dir / "prompts"
        if prompts_dir.exists():
            PromptRegistry.load_from_directory(agent_dir.name, prompts_dir)
```

---

## In-flight session isolation

When a session starts, the prompt version is locked for that session's lifetime. If the active version changes mid-deployment, sessions started before the change continue using their version.

This is enforced by the session log: `AgentSessionLog.prompt_version` records which version was used. For async sessions (background tasks, HITL resume), the version is stored and reloaded:

```python
# When resuming a session (background task or HITL):
session_record = load_session_log(session_id)
prompt_text = PromptRegistry.get(agent_name, session_record.prompt_version)

config = AgentConfig(
    name=agent_name,
    prompt_version=session_record.prompt_version,
    system_prompt=prompt_text,
    ...
)
runner = AgentRunner(provider, config)
```

A resumed session always uses the prompt version it was started with, not the current active version.

---

## Deployment process for a new prompt version

Follow this sequence. Do not skip steps.

```
1. Write the new prompt → prompts/v3.md
2. Add golden-input tests that cover the new behavior (see 16_testing_agents.md)
3. Run the full golden test suite for the agent against v3
   → all existing tests must pass
   → new behavior tests must pass
4. Run the evaluation suite (see 24_agent_evaluation.md):
   → success rate ≥ previous version's rate
   → clarification rate ≤ previous version's rate + 5%
5. Update PROMPT_VERSION = "v3" in agent.py
6. Deploy — new sessions use v3, in-flight sessions continue on their version
7. Monitor evaluation metrics for 24 hours post-deploy
8. If metrics degrade → rollback (step below)
```

Do not deploy a new prompt version without passing step 3 and step 4.

---

## Rollback

Rollback is a code change, not a database operation:

```python
# agent.py — revert to previous version
PROMPT_VERSION = "v2"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "v2.md").read_text()
```

Deploy. New sessions immediately use v2. Sessions already running on v3 continue until they complete. There is no forced session migration.

---

## Canary rollout (workspace-level)

To test a new prompt version on a subset of workspaces before full rollout, use workspace-level agent config (see [25_workspace_agent_config.md](25_workspace_agent_config.md)):

```python
# WorkspaceAgentConfig for canary workspaces
{
    "agent_name": "record_agent",
    "prompt_version_override": "v3",
    "workspace_ids": [7, 14, 22]  # canary group
}
```

Canary sessions use v3; all other workspaces use the current active version. Compare evaluation metrics between canary and control before full rollout.

---

## Prompt authoring rules

| Rule | Reason |
|---|---|
| Each version is a complete, standalone file | Never reference "see v2 for the rest" |
| Diffs between versions are reviewed before merge | Like code review — prompt changes are code changes |
| Version numbers are sequential integers | `v1`, `v2`, `v3` — no `v2b`, `v2-fix`, `v2-final` |
| Old versions are never deleted | Needed for session resume and audit |
| Changes are described in a comment at the top of the file | One-line summary of what changed and why |

---

## What prompt versioning must NOT do

- Mutate a deployed prompt file in place — write a new version file.
- Deploy a new version without running the golden test suite first.
- Delete old prompt versions — active sessions may still reference them.
- Store the active prompt text in the database — the file system is the source of truth; the DB stores only the version identifier.
- Allow a resumed session to silently upgrade to the current prompt — always reload the session's original version.
