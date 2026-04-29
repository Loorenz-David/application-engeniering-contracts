# 11 — Subagent Contract

## Definition

A subagent is a single agent with a narrowly bounded tool set, designed to receive a complete, self-contained task from an orchestrator and return a structured result.

A subagent is architecturally identical to a single agent (see [09_single_agent.md](09_single_agent.md)). The distinction is operational: a subagent is always called by an orchestrator, never directly by a user.

---

## Bounded scope

Every subagent is scoped to one domain. It has access only to the tools required to operate within that domain.

| Subagent | Allowed tools |
|---|---|
| `record_subagent` | `create_record`, `get_record`, `list_records`, `update_record` |
| `notification_subagent` | `send_email`, `send_sms` |
| `report_subagent` | `generate_report`, `export_report` |

A subagent never has access to tools from outside its domain. If a task requires cross-domain work, that is the orchestrator's responsibility.

---

## Input contract

Subagents receive a single text message — the task description written by the orchestrator. The task must be:

- **Complete**: contains all the information the subagent needs to act (entity IDs, field values, target state).
- **Self-contained**: does not refer to "the previous step" or "what we discussed earlier" — the subagent has no conversation history.
- **Unambiguous**: specifies the exact operation, not a vague goal.

```
# Good task — complete, self-contained, unambiguous
"Create a new record titled 'Smith Project' in category 'type_a' with notes 'Initial intake'. 
Return the created record's client_id."

# Bad task — refers to context the subagent does not have
"Create the record we just discussed and use the values from before."
```

---

## Output contract

Subagents return an `AgentResult`. The orchestrator reads `result.status` before proceeding. Three valid statuses for a subagent:

| Status | Meaning | Orchestrator action |
|---|---|---|
| `complete` | Task finished successfully | Parse `result.content`, extract key data, continue |
| `failed` | Task could not be completed | Report failure to user, stop dependent subtasks |
| `clarification_needed` | Subagent hit an uncertainty it cannot self-resolve | See [10_orchestrator.md — knowledge resolver](10_orchestrator.md) |

When `status == "complete"`, `result.content` must follow this structured format so the orchestrator can parse it reliably:

```
# Successful result — always start with SUCCESS
"SUCCESS. client_id: rec_abc123, title: Smith Project, status: active."

# Failed result — always start with FAILED
"FAILED [NOT_FOUND]: No record found with client_id rec_xyz999. Nothing was modified."
```

When `status == "clarification_needed"`, `result.clarification` carries the question, the uncertainty type, what was gathered so far, and the conversation history needed to resume.

---

## System prompt structure for a subagent

A subagent's system prompt needs four sections:

### 1. Identity and scope

```markdown
You are the Record Subagent. You manage records within a single workspace.
You have access to: create_record, get_record, list_records, update_record, ask_clarification.
You do not send notifications, generate reports, or access other domains.
```

### 2. Behavioral rules

```markdown
- Read the task carefully before calling any tool.
- If required information is missing from the task description, do not guess —
  try to find it with your tools first, then escalate if still missing.
- Call only the minimum tools necessary to complete the task.
- Do not perform extra operations that were not requested.
```

### 3. Clarification rules

```markdown
If you cannot proceed because you are missing information:
1. Try to resolve it using your own tools first.
2. If you still cannot proceed, call ask_clarification.
   - Type "cross_domain" if the answer is in another domain (state which one in domain_needed).
   - Type "intent" if only the user can clarify what they want.
3. Always fill context_gathered with a summary of what you have done so far.
Do NOT guess. Do NOT contact other agents directly.
```

### 4. Output format

```markdown
Your response must always begin with either "SUCCESS" or "FAILED".
Include the client_id of any entity you created or modified.
Include the error code if a tool returns an error.
Keep your response under 3 sentences.
```

---

## Clarification — upward only, never to peers

Subagents do not talk directly to the user. They do not call other subagents. All uncertainty escalation goes upward to the orchestrator via `AgentResult(status="clarification_needed")`.

The orchestrator decides what to do with the question: answer it directly, spawn a research subagent, or propagate it further up the chain. The subagent's only job is to accurately classify and describe the uncertainty.

A subagent escalates using the `ask_clarification` tool, which triggers a `clarification_needed` result. Before calling it, the subagent must walk the decision tree (see [19_clarification_protocol.md](19_clarification_protocol.md)):

1. Can I resolve this with my own tools? → research first, escalate only if still stuck.
2. Is this about intent? → escalate as `intent`.
3. Is this about data in another domain? → escalate as `cross_domain`, name the domain.

```markdown
# Subagent system prompt — clarification section
If you cannot proceed because you are missing information:
1. Try to resolve it using your own tools first.
2. If you still cannot proceed, call ask_clarification.
   - Type "cross_domain" if the answer is in another domain (state which one).
   - Type "intent" if only the user can clarify what they want.
3. Always summarize what you have done so far in context_gathered.
Do NOT guess. Do NOT contact other agents directly.
```

---

## Context isolation

Subagents do not share memory or state with the orchestrator or with other subagents. Each `AgentRunner.run()` call is stateless.

The orchestrator is responsible for any cross-subagent data passing. The subagent receives what it needs in the task message.

---

## Subagent `AgentContext`

The subagent receives the same `AgentContext` as the orchestrator. It does not get its own context. The session ID, user ID, and workspace ID are consistent across the full orchestration run.

The subagent's tool scope is a subset of the orchestrator session's scopes. The orchestrator does not expand scopes when delegating to subagents.

---

## Standalone subagent vs shared agent

A subagent that is only used by one orchestrator lives inside that orchestrator's folder:

```
ai/agents/workflow_orchestrator/subagents/record_subagent/
```

A subagent that is reused across multiple orchestrators lives at the top-level agents folder:

```
ai/agents/record_subagent/
```

The structure is identical in both cases. Only the location differs.

---

## What subagents must NOT do

- Contact the user directly — all escalation goes through `ask_clarification` → orchestrator.
- Call tools outside their declared scope.
- Call other subagents directly — peer-to-peer communication is forbidden.
- Call `ask_clarification` without first attempting to resolve with their own tools.
- Return raw JSON tool output — always narrate the result in structured plain text.
- Continue past `max_iterations` — return a `FAILED` result explaining the loop limit was hit.
- Assume success without checking the tool return value.
