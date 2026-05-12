#!/usr/bin/env python3
"""Bootstrap — create a new FastAPI application from the contract foundation.

Phases
------
    1   Base scaffold        FastAPI factory, config, errors, Alembic, health check
    2   Identity             IdentityMixin, generate_id(), User, HistoryRecord, UserAppViewRecord
    3   Service layer        ServiceContext, run_service(), WorkContext, identity resolution
    4   Container runtime    Dockerfile, docker-compose, .dockerignore, Makefile
    5   Auth/RBAC            JWT, RBAC, workspace membership
    6   Realtime             Socket wiring and realtime infrastructure
    7   Execution            Background execution registry and task wiring
    8   Presence             Presence tracking and view activity handlers
    9   Notifications        Notification models and handlers
    10  Observability        Structured logging, correlation IDs, request middleware
    11  Testing              pytest scaffolding, fixtures, test isolation, test commands
    12  Worker runtime       Queue/worker runtime, retry/dead-letter scaffolding
    13  CI/CD                GitHub Actions workflows and CI validation hooks
    14  Replayability        Replay helpers, replay metadata, event-store interfaces
    15  Operational CLI      Typer operational commands and make targets

Usage
-----
    python run/bootstrap.py --app-name my_app
    python run/bootstrap.py --app-name my_app --output-dir ~/Developer
    python run/bootstrap.py --app-name my_app --phase 1
    python run/bootstrap.py --app-name my_app --phase 1-3
        python run/bootstrap.py --app-name my_app --phase 4
        python run/bootstrap.py --app-name my_app --phase all
        python run/bootstrap.py --app-name my_app --legacy-phase-numbering --phase 4-8
    python run/bootstrap.py --app-name my_app --force
"""
from __future__ import annotations

import keyword
from pathlib import Path

import typer

from bootstrap.phases.phase_01_base import _phase1
from bootstrap.phases.phase_02_identity import _phase2
from bootstrap.phases.phase_03_service_layer import _phase3
from bootstrap.phases.phase_04_auth import _phase4 as _phase5_auth
from bootstrap.phases.phase_04_container_runtime import _phase4_container_runtime
from bootstrap.phases.phase_05_realtime import _phase5 as _phase6_realtime
from bootstrap.phases.phase_06_execution import _phase6 as _phase7_execution
from bootstrap.phases.phase_07_presence import _phase7 as _phase8_presence
from bootstrap.phases.phase_08_notifications import _phase8 as _phase9_notifications
from bootstrap.phases.phase_10_observability import _phase10_observability
from bootstrap.phases.phase_11_testing import _phase11_testing
from bootstrap.phases.phase_12_worker_runtime import _phase12_worker_runtime
from bootstrap.phases.phase_13_ci_cd import _phase13_ci_cd
from bootstrap.phases.phase_14_replayability import _phase14_replayability
from bootstrap.phases.phase_15_operational_cli import _phase15_operational_cli

cli = typer.Typer(add_completion=False, help=__doc__)


def _run_phases(phases: list[int], root: Path, a: str, force: bool) -> None:
    dispatch = {
        1: lambda: _phase1(root, a, force),
        2: lambda: _phase2(root, a, force),
        3: lambda: _phase3(root, a, force),
        4: lambda: _phase4_container_runtime(root, a, force),
        5: lambda: _phase5_auth(root, a, force),
        6: lambda: _phase6_realtime(root, a, force),
        7: lambda: _phase7_execution(root, a, force),
        8: lambda: _phase8_presence(root, a, force),
        9: lambda: _phase9_notifications(root, a, force),
        10: lambda: _phase10_observability(root, a, force),
        11: lambda: _phase11_testing(root, a, force),
        12: lambda: _phase12_worker_runtime(root, a, force),
        13: lambda: _phase13_ci_cd(root, a, force),
        14: lambda: _phase14_replayability(root, a, force),
        15: lambda: _phase15_operational_cli(root, a, force),
    }
    for n in phases:
        if n not in dispatch:
            typer.echo(f"  [error] Phase {n} does not exist (valid: 1-15)", err=True)
            raise typer.Exit(1)
        dispatch[n]()


def _parse_phase_arg(phase_str: str) -> list[int]:
    """Parse '1', '1-3', or 'all' into a sorted list of phase numbers."""
    s = phase_str.strip().lower()
    if s == "all":
        return list(range(1, 16))
    if "-" in s:
        parts = s.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            typer.echo(f"[error] Invalid phase range '{phase_str}'. Use '1', '1-3', or 'all'.", err=True)
            raise typer.Exit(1)
        if start > end:
            typer.echo(f"[error] Invalid phase range '{phase_str}'. Start must be <= end.", err=True)
            raise typer.Exit(1)
        return list(range(start, end + 1))
    try:
        return [int(s)]
    except ValueError:
        typer.echo(f"[error] Invalid phase '{phase_str}'. Use '1', '1-3', or 'all'.", err=True)
        raise typer.Exit(1)


def _migrate_legacy_phase_numbers(phases: list[int]) -> list[int]:
    """Map legacy pre-container phase numbering to the current sequence.

    Legacy mapping:
      1->1, 2->2, 3->3, 4->5, 5->6, 6->7, 7->8, 8->9
    Current 9-15 values pass through unchanged.
    """
    mapping = {
        1: 1,
        2: 2,
        3: 3,
        4: 5,
        5: 6,
        6: 7,
        7: 8,
        8: 9,
        9: 9,
        10: 10,
        11: 11,
        12: 12,
        13: 13,
        14: 14,
        15: 15,
    }
    migrated: list[int] = []
    for phase in phases:
        if phase not in mapping:
            typer.echo(
                f"[error] Legacy phase {phase} is invalid (valid legacy range: 1-15).",
                err=True,
            )
            raise typer.Exit(1)
        target = mapping[phase]
        if target not in migrated:
            migrated.append(target)
    return sorted(migrated)


def _validate_app_name(app_name: str) -> None:
    if not app_name.isidentifier() or keyword.iskeyword(app_name):
        typer.echo("[error] --app-name must be a valid, non-keyword Python identifier.", err=True)
        raise typer.Exit(1)


def _validate_phase_prerequisites(phases: list[int], root: Path, app_name: str) -> None:
    if not phases:
        typer.echo("[error] No phases selected.", err=True)
        raise typer.Exit(1)
    if 1 not in phases and not (root / app_name / "__init__.py").exists():
        typer.echo(
            "[error] Phase 1 scaffold is missing. Run --phase 1 first, or include phase 1 in this run.",
            err=True,
        )
        raise typer.Exit(1)


@cli.command()
def main(
    app_name: str = typer.Option(..., "--app-name", "-n", help="Python module name (e.g. my_app)"),
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        "--target",
        "-o",
        help="Target directory to create the app in",
    ),
    phase: str = typer.Option("1-3", "--phase", "-p", help="Phases to run: 1 | 1-3 | all"),
    legacy_phase_numbering: bool = typer.Option(
        False,
        "--legacy-phase-numbering",
        help="Interpret --phase with legacy remap (4->5, 5->6, 6->7, 7->8, 8->9); 9-15 unchanged.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files"),
    validate: bool = typer.Option(False, "--validate", help="Run Docker-backed bootstrap validation after generation"),
) -> None:
    """Bootstrap a new FastAPI application from the contract foundation."""
    _validate_app_name(app_name)

    root = output_dir.resolve()
    phases = _parse_phase_arg(phase)
    if legacy_phase_numbering:
        phases = _migrate_legacy_phase_numbers(phases)
    _validate_phase_prerequisites(phases, root, app_name)

    typer.echo(f"\nBootstrapping '{app_name}' in {root}")
    typer.echo(f"Phases: {phases}")
    typer.echo(f"Force:  {force}")

    _run_phases(phases, root, app_name, force)

    if validate:
        from bootstrap.validation import validate_generated_app

        validate_generated_app(root, app_name, phases=phases)

    typer.echo("\n── Done ─────────────────────────────────────────────────────────────────")
    typer.echo("\nNext steps:")

    if 1 in phases:
        typer.echo(f"  1. cd {root}")
        typer.echo("  2. python -m venv .venv && source .venv/bin/activate")
        typer.echo("  3. pip install -r requirements.txt")
        typer.echo("  4. cp .env.example .env")
        typer.echo("  5. make dev-up            # hybrid: postgres+redis in Docker")
        typer.echo("  6. alembic upgrade head    # no migrations yet — run after Phase 2")
        typer.echo("  7. python run.py           # starts on http://localhost:5000")
        typer.echo("  8. curl http://localhost:5000/health")
        typer.echo("  9. make dev-down")

    if 4 in phases:
        typer.echo("\nContainer runtime guidance:")
        typer.echo("  - Hybrid local mode: make dev-up / python run.py / make dev-down")
        typer.echo("  - Full container mode: docker compose --profile app up -d")
        typer.echo("  - Full container shutdown: docker compose down")
        typer.echo("  - Validation uses dynamic ports intentionally to avoid host port collisions.")
        typer.echo("  - Validation runtime is isolated from host-installed DB/Redis services.")

    if any(phase_id >= 10 for phase_id in phases):
        typer.echo("\nOperational maturity guidance:")
        typer.echo("  - make lint && make format")
        typer.echo("  - make test")
        typer.echo("  - make worker-dev")
        typer.echo("  - make inspect")
        typer.echo("  - make validate")

    if max(phases) < 15:
        remaining = list(range(max(phases) + 1, 16))
        typer.echo(f"\nTo add more phases ({', '.join(str(n) for n in remaining)}):")
        typer.echo(f"  python run/bootstrap.py --app-name {app_name} --output-dir {root} --phase {max(phases)+1}-15")
        typer.echo("\nOr review the phase spec before building:")
        typer.echo(f"  python resolver.py --bootstrap {max(phases)+1}")


if __name__ == "__main__":
    cli()
