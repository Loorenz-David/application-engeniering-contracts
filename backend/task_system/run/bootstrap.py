#!/usr/bin/env python3
"""CLI wrapper for the contract-derived bootstrap generator.

The implementation lives under `task_system/bootstrap/` so each phase can evolve
next to its templates and helpers as the backend contracts change.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_task_system_on_path() -> None:
    task_system_root = Path(__file__).resolve().parents[1]
    if str(task_system_root) not in sys.path:
        sys.path.insert(0, str(task_system_root))


def main() -> None:
    _ensure_task_system_on_path()
    from bootstrap.runner import cli

    cli()


if __name__ == "__main__":
    main()