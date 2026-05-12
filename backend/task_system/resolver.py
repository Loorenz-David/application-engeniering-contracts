#!/usr/bin/env python3
"""Backend Contract Resolver (local)

Resolves backend goals to backend contracts from ../architecture/. It is scoped to
backend-only work and independent from the repository root task_system.

Usage:
  python3 resolver.py --list-tasks
  python3 resolver.py --task crud_realtime_backend
  python3 resolver.py "add worker retries and dead letter diagnostics"
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from task_types import ContractRef, ResolvedPlan, Task

BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "tasks"
ARCH_DIR = BASE_DIR.parent / "architecture"

CORE_CONTRACTS: list[ContractRef] = [
    ContractRef("01", "01_architecture.md", "Core layer and dependency boundaries"),
    ContractRef("04", "04_context.md", "Service context contract"),
    ContractRef("05", "05_errors.md", "Domain error contract"),
    ContractRef("06", "06_commands.md", "Write-path orchestration"),
    ContractRef("07", "07_queries.md", "Read-path contract"),
    ContractRef("09", "09_routers.md", "Router boundary contract"),
    ContractRef("21", "21_naming_conventions.md", "Naming conventions"),
    ContractRef("40", "40_identity.md", "Identity and public IDs"),
    ContractRef("41", "41_user.md", "User model baseline"),
    ContractRef("42", "42_event.md", "Event lifecycle baseline"),
    ContractRef("48", "48_presence.md", "Presence baseline"),
]


def _load_contracts(raw_list: list[dict]) -> list[ContractRef]:
    return [ContractRef(id=item["id"], file=item["file"], reason=item["reason"]) for item in (raw_list or [])]


def load_tasks() -> list[Task]:
    tasks: list[Task] = []
    for path in sorted(TASKS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        tasks.append(
            Task(
                id=raw["id"],
                name=raw["name"],
                intent_patterns=raw.get("intent_patterns", []),
                contracts=_load_contracts(raw.get("contracts", [])),
                implementation_steps=raw.get("implementation_steps", []),
                constraints=raw.get("constraints", []),
            )
        )
    return tasks


def match_tasks(intent: str, tasks: list[Task]) -> list[Task]:
    text = intent.lower()
    return [task for task in tasks if any(pattern.lower() in text for pattern in task.intent_patterns)]


def select_task(task_id: str, tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if task.id == task_id]


def _merge_contracts(tasks: list[Task]) -> list[ContractRef]:
    seen: set[str] = set()
    merged: list[ContractRef] = []

    for item in CORE_CONTRACTS:
        if item.id not in seen:
            seen.add(item.id)
            merged.append(item)

    for task in tasks:
        for ref in task.contracts:
            if ref.id not in seen:
                seen.add(ref.id)
                merged.append(ref)

    return sorted(merged, key=lambda c: c.id)


def _merge_unique(values: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in values:
        for item in group:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def resolve_from_tasks(tasks: list[Task]) -> ResolvedPlan:
    return ResolvedPlan(
        matched_tasks=tasks,
        contracts=_merge_contracts(tasks),
        steps=_merge_unique([task.implementation_steps for task in tasks]),
        constraints=_merge_unique([task.constraints for task in tasks]),
    )


def _contract_path(file_name: str) -> Path:
    return ARCH_DIR / file_name


def _local_companion(file_name: str) -> Path | None:
    """Return the *_local.md companion path if it exists, else None."""
    stem = Path(file_name).stem  # e.g. "41_user"
    companion = ARCH_DIR / f"{stem}_local.md"
    return companion if companion.exists() else None


def render_plan(plan: ResolvedPlan) -> str:
    lines: list[str] = ["# Backend Execution Plan", ""]

    lines.append("## Tasks Selected")
    for task in plan.matched_tasks:
        lines.append(f"- **{task.name}** `{task.id}`")
    lines.append("")

    lines.append("## Contracts to Load")
    for ref in plan.contracts:
        path = _contract_path(ref.file)
        status = "ok" if path.exists() else "missing"
        lines.append(f"- `{ref.file}` ({status}) — {ref.reason}")
        companion = _local_companion(ref.file)
        if companion is not None:
            lines.append(f"  - `{companion.name}` (local extension)")
    lines.append("")

    if plan.steps:
        lines.append("## Implementation Steps")
        for idx, step in enumerate(plan.steps, start=1):
            lines.append(f"{idx}. {step}")
        lines.append("")

    if plan.constraints:
        lines.append("## Constraints")
        for item in plan.constraints:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def render_task_list(tasks: list[Task]) -> str:
    lines = ["# Backend Tasks", ""]
    for task in tasks:
        lines.append(f"- `{task.id}`: {task.name}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    tasks = load_tasks()

    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return

    if args[0] == "--list-tasks":
        print(render_task_list(tasks))
        return

    if args[0] == "--task":
        if len(args) < 2:
            raise SystemExit("Error: --task requires a task id")
        matched = select_task(args[1], tasks)
        if not matched:
            available = ", ".join(task.id for task in tasks)
            raise SystemExit(f"Error: task '{args[1]}' not found. Available: {available}")
        print(render_plan(resolve_from_tasks(matched)))
        return

    intent = " ".join(args)
    matched = match_tasks(intent, tasks)
    if not matched:
        available = ", ".join(task.id for task in tasks)
        raise SystemExit(f"Error: no task matched intent. Available tasks: {available}")

    print(render_plan(resolve_from_tasks(matched)))


if __name__ == "__main__":
    main()
