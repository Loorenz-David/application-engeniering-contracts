from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractRef:
    id: str
    file: str
    reason: str


@dataclass
class Task:
    id: str
    name: str
    intent_patterns: list[str]
    contracts: list[ContractRef]
    implementation_steps: list[str]
    constraints: list[str]


@dataclass
class ResolvedPlan:
    matched_tasks: list[Task]
    contracts: list[ContractRef]
    steps: list[str]
    constraints: list[str]
