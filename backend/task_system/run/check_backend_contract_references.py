#!/usr/bin/env python3
"""Validate backend contract references for backend-local guides.

This validator is scoped to the backend-encapsulated layout:
- contracts live in ../architecture/
- guides live in backend/task_system/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TASK_SYSTEM_DIR = Path(__file__).resolve().parents[1]
BACKEND_CONTRACT_DIR = TASK_SYSTEM_DIR.parent / "architecture"

CONTRACT_REF_RE = re.compile(r"\b(?:\.\./architecture/)?([0-9]{2}_[a-z0-9_]+\.md)\b")
CONTRACT_BASENAME_RE = re.compile(r"^[0-9]{2}_[a-z0-9_]+\.md$")


def collect_contracts() -> set[str]:
    names: set[str] = set()
    for path in BACKEND_CONTRACT_DIR.glob("*.md"):
        if CONTRACT_BASENAME_RE.match(path.name):
            names.add(path.name)
    return names


def default_guides() -> list[Path]:
    candidates = [
        TASK_SYSTEM_DIR / "backend_contract_goal_mapping_guide.md",
    ]
    return [p for p in candidates if p.exists()]


def scan_guide(path: Path, known_contracts: set[str]) -> list[str]:
    missing: list[str] = []
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in CONTRACT_REF_RE.finditer(line):
            token = match.group(1)
            # Local companion files are optional app-specific extensions and may
            # not exist yet in early setups.
            if token.endswith("_local.md"):
                continue
            if token not in known_contracts:
                missing.append(f"{path}:{line_no}: missing backend contract {token}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guides", nargs="*", default=None, help="Optional guide paths to validate")
    args = parser.parse_args()

    guides = [Path(g) for g in args.guides] if args.guides else default_guides()
    if not guides:
        print("No backend guides found to validate.")
        return 1

    missing_paths = [g for g in guides if not g.exists()]
    if missing_paths:
        print("Guide file(s) not found:")
        for path in missing_paths:
            print(f"- {path}")
        return 1

    known_contracts = collect_contracts()
    if not known_contracts:
        print(f"No backend contracts found in {BACKEND_CONTRACT_DIR}")
        return 1

    all_missing: list[str] = []
    for guide in guides:
        all_missing.extend(scan_guide(guide, known_contracts))

    if all_missing:
        print("Missing backend contract references detected:")
        for item in all_missing:
            print(f"- {item}")
        return 1

    print(f"OK: validated {len(guides)} backend guide file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
