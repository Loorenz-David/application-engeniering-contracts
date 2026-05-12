from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer


def _assert_make_targets(root: Path, required_targets: list[str]) -> None:
    makefile = root / "Makefile"
    if not makefile.exists():
        raise RuntimeError(f"Missing Makefile: {makefile}")
    content = makefile.read_text(encoding="utf-8")
    missing = [target for target in required_targets if f"{target}:" not in content]
    if missing:
        raise RuntimeError(f"Missing Makefile targets: {', '.join(missing)}")


def _assert_ci_workflows(root: Path) -> None:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.exists():
        raise RuntimeError(f"Missing CI workflows directory: {workflows_dir}")
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    if not workflow_files:
        raise RuntimeError("No CI workflow files found in .github/workflows")


def validate_generated_app(root: Path, app_name: str, *, phases: list[int] | None = None) -> None:
    script = root / "scripts" / "validate_bootstrap.py"
    if not script.exists():
        typer.echo(f"[error] Validation script not found: {script}", err=True)
        raise typer.Exit(1)

    typer.echo("\n-- Bootstrap validation ---------------------------------------------")
    typer.echo("Starting Docker Compose services, running migrations, and checking /health.")

    try:
        subprocess.run([sys.executable, str(script)], cwd=root, check=True)
        if phases:
            _assert_make_targets(
                root,
                required_targets=[
                    "dev-up",
                    "dev-down",
                    "test",
                    "lint",
                    "format",
                    "validate",
                    "worker",
                    "seed",
                    "logs",
                    "replay",
                    "inspect",
                    "reset-db",
                ],
            )
            if 13 in phases:
                _assert_ci_workflows(root)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"[error] Bootstrap validation failed with exit code {exc.returncode}.", err=True)
        raise typer.Exit(exc.returncode) from exc
    except RuntimeError as exc:
        typer.echo(f"[error] Bootstrap validation failed: {exc}", err=True)
        raise typer.Exit(1) from exc
