# 24 — Agent Evaluation Contract

## Purpose

Observability (contract 18) tells you what happened. Evaluation tells you whether it was good. Without evaluation, prompt changes are blind — you cannot know whether version 3 is better than version 2 except by watching production degrade.

Evaluation answers three questions:
1. Are agents completing tasks successfully?
2. Are they doing so efficiently (without excessive iterations or clarification)?
3. Did a prompt change improve or worsen behavior?

---

## Core metrics

These metrics are computed per agent, per prompt version, per time window (daily / weekly / monthly).

| Metric | Definition | Target |
|---|---|---|
| **Task success rate** | `complete` outcomes / total sessions | ≥ 90% |
| **Clarification rate** | `clarification_needed` outcomes / total sessions | ≤ 10% |
| **Failure rate** | `failed` + `max_iterations` outcomes / total sessions | ≤ 5% |
| **Avg iterations per session** | Mean iterations across complete sessions | ≤ 5 |
| **Avg tokens per session** | Mean (input + output) tokens across complete sessions | Baseline × 1.2 |
| **Tool selection distribution** | Count of each tool called per 100 sessions | Tracked, no hard target |
| **P95 session latency** | 95th percentile of session wall-clock time | ≤ 30s (sync), ≤ 10min (background) |

All metrics are computed from `AgentSessionLog` and `agent_task_events` — no separate data pipeline required.

---

## Metrics queries

```python
# services/queries/ai/agent_metrics.py
from my_app.models.tables.ai.agent_session_log import AgentSessionLog
from my_app.models import db
from sqlalchemy import func


def get_agent_metrics(
    agent_name: str,
    prompt_version: str,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    rows = (
        db.session.query(
            AgentSessionLog.outcome,
            func.count().label("count"),
            func.avg(AgentSessionLog.total_iterations).label("avg_iterations"),
            func.avg(
                AgentSessionLog.total_input_tokens + AgentSessionLog.total_output_tokens
            ).label("avg_tokens"),
        )
        .filter(
            AgentSessionLog.agent_name == agent_name,
            AgentSessionLog.prompt_version == prompt_version,
            AgentSessionLog.created_at.between(start_date, end_date),
        )
        .group_by(AgentSessionLog.outcome)
        .all()
    )

    total = sum(r.count for r in rows)
    by_outcome = {r.outcome: r for r in rows}
    complete = by_outcome.get("complete")

    return {
        "agent_name": agent_name,
        "prompt_version": prompt_version,
        "total_sessions": total,
        "success_rate": (by_outcome.get("complete", {}).count or 0) / max(total, 1),
        "clarification_rate": (by_outcome.get("clarification_needed", {}).count or 0) / max(total, 1),
        "failure_rate": (
            (by_outcome.get("failed", {}).count or 0) +
            (by_outcome.get("max_iterations", {}).count or 0)
        ) / max(total, 1),
        "avg_iterations": complete.avg_iterations if complete else None,
        "avg_tokens": complete.avg_tokens if complete else None,
        "window": {"start": start_date.isoformat(), "end": end_date.isoformat()},
    }
```

---

## Golden evaluation suite

The golden evaluation suite is a set of fixed input scenarios with expected outcomes. It runs the agent against a prompt version using the mock LLM (see [16_testing_agents.md](16_testing_agents.md)) and verifies that the tool call sequence and final output match expectations.

```
ai/agents/<agent_name>/
└── evaluation/
    ├── golden_inputs.json      # Input scenarios
    └── run_evaluation.py       # Evaluation runner
```

### Golden input format

```json
[
  {
    "scenario": "create_record_simple",
    "input": "Create a record called Smith Project in category type_a.",
    "expected_tool_calls": ["create_record"],
    "expected_outcome": "complete",
    "expected_phrase": "Smith Project",
    "max_iterations": 3
  },
  {
    "scenario": "ambiguous_category",
    "input": "Create a record for the Smith project.",
    "expected_tool_calls": ["list_categories", "ask_clarification"],
    "expected_outcome": "clarification_needed",
    "expected_clarification_type": "intent",
    "max_iterations": 4
  }
]
```

### Evaluation runner

```python
# ai/agents/record_agent/evaluation/run_evaluation.py

def run_evaluation(prompt_version: str) -> dict:
    scenarios = load_golden_inputs()
    config = load_config_for_version(prompt_version)
    results = []

    for scenario in scenarios:
        provider = build_mock_provider_for_scenario(scenario)
        runner = AgentRunner(provider, config)
        result = runner.run(scenario["input"], _test_agent_ctx())

        passed = (
            result.status == scenario["expected_outcome"] and
            _tool_calls_match(provider, scenario["expected_tool_calls"]) and
            (scenario.get("expected_phrase", "") in (result.content or ""))
        )
        results.append({
            "scenario": scenario["scenario"],
            "passed": passed,
            "actual_outcome": result.status,
            "actual_tool_calls": _extract_tool_calls(provider),
        })

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "prompt_version": prompt_version,
        "pass_rate": passed / max(total, 1),
        "passed": passed,
        "total": total,
        "failures": [r for r in results if not r["passed"]],
    }
```

**Gate rule**: A prompt version may not be deployed if `pass_rate < 1.0` on the golden suite. All golden scenarios must pass. New behavior scenarios are added to the suite before deploying a version that introduces new behavior.

---

## A/B prompt comparison

To compare two prompt versions head-to-head, run the golden suite against both and compare production metrics from the canary rollout (see [21_prompt_versioning.md](21_prompt_versioning.md) and [25_workspace_agent_config.md](25_workspace_agent_config.md)).

```python
# Comparison report
def compare_versions(
    agent_name: str,
    version_a: str,
    version_b: str,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    metrics_a = get_agent_metrics(agent_name, version_a, start_date, end_date)
    metrics_b = get_agent_metrics(agent_name, version_b, start_date, end_date)

    return {
        "agent_name": agent_name,
        "version_a": metrics_a,
        "version_b": metrics_b,
        "delta": {
            "success_rate": metrics_b["success_rate"] - metrics_a["success_rate"],
            "clarification_rate": metrics_b["clarification_rate"] - metrics_a["clarification_rate"],
            "avg_tokens": (metrics_b["avg_tokens"] or 0) - (metrics_a["avg_tokens"] or 0),
        },
        "recommendation": _recommend(metrics_a, metrics_b),
    }


def _recommend(a: dict, b: dict) -> str:
    if b["success_rate"] < a["success_rate"] - 0.02:
        return "ROLLBACK: version_b success rate is more than 2% below version_a"
    if b["clarification_rate"] > a["clarification_rate"] + 0.05:
        return "CAUTION: version_b clarification rate is more than 5% above version_a"
    if b["success_rate"] >= a["success_rate"] and b["clarification_rate"] <= a["clarification_rate"]:
        return "PROMOTE: version_b meets all quality thresholds"
    return "MONITOR: version_b shows mixed signals — extend canary period"
```

---

## Deployment quality gates

Before promoting a prompt version to full rollout:

| Gate | Threshold | Failure action |
|---|---|---|
| Golden suite pass rate | 100% | Block deployment |
| Production success rate (canary, 24h) | ≥ previous version | Rollback |
| Production clarification rate (canary, 24h) | ≤ previous version + 5% | Caution / extend canary |
| Production failure rate (canary, 24h) | ≤ 5% | Rollback if above 8% |

These gates are checked manually by the engineer deploying the prompt version. Future automation can run them via a CLI command.

---

## Evaluation API

```
GET /api/v1/admin/agents/{agent_name}/metrics?version=v2&days=7
GET /api/v1/admin/agents/{agent_name}/compare?version_a=v2&version_b=v3&days=7
POST /api/v1/admin/agents/{agent_name}/evaluate?version=v3   → runs golden suite, returns report
```

Admin-only endpoints. Workspace users do not have access to cross-workspace metrics.

---

## What evaluation must NOT do

- Use live LLM calls in the golden suite — mock the LLM (see [16_testing_agents.md](16_testing_agents.md)).
- Compare versions using different time windows — A/B comparisons must use the same window.
- Promote a version that fails any golden scenario — pass rate must be 100%.
- Delete old session logs — they are the source of truth for all historical metrics.
- Treat a small sample (< 50 sessions) as statistically meaningful for production metrics — note sample size in all reports.
