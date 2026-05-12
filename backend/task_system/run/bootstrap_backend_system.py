#!/usr/bin/env python3
"""Bootstrap the backend umbrella layout.

This script is for the backend repository itself. It creates the standard
backend/ folder architecture so the backend repo can be initialized from a
fresh pull without manually creating directories.

It is intentionally separate from bootstrap.py, which generates an application
from backend contracts.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import typer

cli = typer.Typer(add_completion=False, help=__doc__)

SOURCE_TASK_SYSTEM_DIR = Path(__file__).resolve().parents[1]
SOURCE_BACKEND_DIR = Path(__file__).resolve().parents[2]
SOURCE_ARCHITECTURE_DIR = SOURCE_TASK_SYSTEM_DIR.parent / "architecture"
SOURCE_GUIDE_PATH = SOURCE_TASK_SYSTEM_DIR / "backend_contract_goal_mapping_guide.md"
SOURCE_DOCS_DIR = SOURCE_BACKEND_DIR / "docs"
SOURCE_SKILLS_DIR = SOURCE_BACKEND_DIR / "skills"

CORE_CONTRACT_FILES: list[str] = [
    "01_architecture.md",
    "04_context.md",
    "05_errors.md",
    "06_commands.md",
    "07_queries.md",
    "09_routers.md",
    "21_naming_conventions.md",
    "40_identity.md",
    "41_user.md",
    "42_event.md",
    "48_presence.md",
]

# Domain contracts that benefit most from app-specific local extension stubs.
# Key: canonical filename stem, Value: (display name, short description)
_DOMAIN_STUBS: dict[str, tuple[str, str]] = {
    "40_identity": ("Identity", "identity resolution, public ID strategy"),
    "41_user": ("User", "user model fields, roles, soft-delete behaviour"),
    "42_event": ("Event", "event types, lifecycle hooks, retention rules"),
    "43_image": ("Image", "image storage, variants, CDN policy"),
    "44_case": ("Case", "case workflow, states, assignment rules"),
    "45_content": ("Content", "content types, versioning, publishing"),
    "46_serialization": ("Serialization", "serialization overrides, field exclusions"),
    "47_notifications": ("Notifications", "notification channels, templates, preferences"),
    "48_presence": ("Presence", "presence heartbeat interval, TTL, custom status"),
}

REQUIRED_DOCS_FILES: list[str] = [
    "README.md",
    "architecture/under_construction/TEMPLATE_PLAN.md",
    "architecture/implemented_summaries/TEMPLATE_SUMMARY.md",
    "architecture/archives/TEMPLATE_ARCHIVE_RECORD.md",
    "debugging/TEMPLATE_DEBUG_PLAN.md",
    "handoff/to_frontend/TEMPLATE_HANDOFF_TO_FRONTEND.md",
    "handoff/from_frontend/TEMPLATE_HANDOFF_FROM_FRONTEND.md",
]

REQUIRED_SKILLS_FILES: list[str] = [
    "README.md",
    "skill_router.md",
    "_shared/contracts_index.md",
    "_shared/output_format.md",
    "_shared/quality_gate.md",
    "_shared/plan_lifecycle_contract.md",
    "cross_cutting/plan_lifecycle_orchestrator/SKILL.md",
    "cross_cutting/debugging_nested_plan_loop/SKILL.md",
]


def _write_version_file(
    path: Path,
    content: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    action_log: list[str] | None = None,
) -> None:
    if path.exists() and not force:
        return
    if dry_run:
        typer.echo(f"  [dry-run] Would write {path}")
        return
    path.write_text(content, encoding="utf-8")
    if action_log is not None:
        action_log.append(str(path))


def _copy_file(
    source_path: Path,
    target_path: Path,
    *,
    dry_run: bool = False,
    action_log: list[str] | None = None,
) -> None:
    if dry_run:
        typer.echo(f"  [dry-run] Would copy {source_path} -> {target_path}")
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    if action_log is not None:
        action_log.append(str(target_path))


def _iter_markdown_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def _is_docs_template_or_readme(relative_path: Path) -> bool:
    name = relative_path.name
    return name == "README.md" or name.startswith("TEMPLATE_")


def _sync_docs(docs_dir: Path, *, dry_run: bool = False, action_log: list[str] | None = None) -> int:
    """Sync docs workflow READMEs and templates to local backend/docs/."""
    synced = 0
    for source_path in _iter_markdown_files(SOURCE_DOCS_DIR):
        rel = source_path.relative_to(SOURCE_DOCS_DIR)
        if not _is_docs_template_or_readme(rel):
            continue
        target_path = docs_dir / rel
        _copy_file(source_path, target_path, dry_run=dry_run, action_log=action_log)
        synced += 1
    typer.echo(f"  Synced {synced} docs template/readme file(s) to {docs_dir}")
    return synced


def _sync_skills(skills_dir: Path, *, dry_run: bool = False, action_log: list[str] | None = None) -> int:
    """Sync canonical skills set to local backend/skills/."""
    synced = 0
    for source_path in _iter_markdown_files(SOURCE_SKILLS_DIR):
        rel = source_path.relative_to(SOURCE_SKILLS_DIR)
        target_path = skills_dir / rel
        _copy_file(source_path, target_path, dry_run=dry_run, action_log=action_log)
        synced += 1

    # Local custom skills bucket that is never part of canonical sync source.
    local_dir = skills_dir / "local"
    if dry_run:
        typer.echo(f"  [dry-run] Would ensure local custom skills dir exists: {local_dir}")
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        if action_log is not None:
            action_log.append(str(local_dir))

    typer.echo(f"  Synced {synced} skill markdown file(s) to {skills_dir}")
    return synced


def _validate_sync(backend_dir: Path) -> int:
    """Validate required synced files exist under backend/docs and backend/skills."""
    errors: list[str] = []
    docs_dir = backend_dir / "docs"
    skills_dir = backend_dir / "skills"

    for rel in REQUIRED_DOCS_FILES:
        path = docs_dir / rel
        if not path.exists():
            errors.append(f"missing docs file: {path}")

    for rel in REQUIRED_SKILLS_FILES:
        path = skills_dir / rel
        if not path.exists():
            errors.append(f"missing skills file: {path}")

    if errors:
        typer.echo("Validation failed:", err=True)
        for item in errors:
            typer.echo(f"- {item}", err=True)
        return 1

    typer.echo(f"Validation OK: docs/skills sync layout verified under {backend_dir}")
    return 0


def _write_sync_report(
    backend_dir: Path,
    *,
    root: Path,
    flags: list[str],
    counts: dict[str, int],
    validation_ran: bool,
    validation_status: str,
    action_log: list[str],
) -> Path:
    report_dir = backend_dir / "sync_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    report_path = report_dir / f"SYNC_REPORT_{timestamp.strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        f"# Sync Report {timestamp.strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Metadata",
        "",
        f"- Root: `{root}`",
        f"- Backend target: `{backend_dir}`",
        f"- Flags: `{', '.join(flags) if flags else 'none'}`",
        f"- Validation: `{validation_status}`",
        "",
        "## Counts",
        "",
        f"- Contracts synced: `{counts['contracts']}`",
        f"- Docs synced: `{counts['docs']}`",
        f"- Skills synced: `{counts['skills']}`",
        f"- Local stubs scaffolded: `{counts['stubs']}`",
        f"- Version files written: `{counts['version_files']}`",
        "",
        "## Paths written or updated",
        "",
    ]
    for item in action_log:
        lines.append(f"- `{item}`")
    if not action_log:
        lines.append("- No file writes recorded")
    if validation_ran:
        lines.extend(["", "## Validation", "", f"- Result: `{validation_status}`"])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _scaffold_local_stubs(
    architecture_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    action_log: list[str] | None = None,
) -> int:
    """Create *_local.md companion stubs for domain contracts."""
    created = 0
    for stem, (name, description) in _DOMAIN_STUBS.items():
        stub_path = architecture_dir / f"{stem}_local.md"
        if stub_path.exists() and not force:
            continue
        canonical_file = f"{stem}.md"
        if dry_run:
            typer.echo(f"  [dry-run] Would scaffold local stub {stub_path}")
            continue
        stub_path.write_text(
            f"# {name} - Local Extensions\n"
            f"> Extends: {canonical_file}\n"
            f"\n"
            f"<!-- Scope: {description} -->\n"
            f"<!-- Add app-specific fields, overrides, and decisions below. -->\n"
            f"<!-- Do NOT modify the canonical {canonical_file} directly. -->\n"
            f"\n"
            f"## Added Fields\n"
            f"\n"
            f"<!-- Example:\n"
            f"- `field_name: Type` - purpose and nullability\n"
            f"-->\n"
            f"\n"
            f"## Overridden Behaviour\n"
            f"\n"
            f"<!-- Document any behaviour that differs from the canonical contract. -->\n"
            f"\n"
            f"## Local Decisions\n"
            f"\n"
            f"<!-- Document app-specific design choices and the reasoning behind them. -->\n",
            encoding="utf-8",
        )
        created += 1
        if action_log is not None:
            action_log.append(str(stub_path))
    typer.echo(f"  Scaffolded local extension stubs in {architecture_dir}")
    return created


def _sync_contracts(architecture_dir: Path, *, dry_run: bool = False, action_log: list[str] | None = None) -> int:
    """Sync canonical contracts from this repository into target architecture."""
    copied = 0
    for source_path in sorted(SOURCE_ARCHITECTURE_DIR.glob("[0-9][0-9]_*.md")):
        target_path = architecture_dir / source_path.name
        _copy_file(source_path, target_path, dry_run=dry_run, action_log=action_log)
        copied += 1
    typer.echo(f"  Synced {copied} canonical contract file(s) to {architecture_dir}")
    return copied


def _refresh_core_contracts_section(guide_text: str) -> str:
    """Refresh the core contract block in the mapping guide."""
    start_header = "## Core contracts (always include)"
    end_header = "## Goal bundles (starter)"

    start = guide_text.find(start_header)
    end = guide_text.find(end_header)
    if start == -1 or end == -1 or end <= start:
        return guide_text

    core_lines = [start_header, ""]
    for file_name in CORE_CONTRACT_FILES:
        core_lines.append(f"- `../architecture/{file_name}`")
    core_lines.append("")
    core_block = "\n".join(core_lines)

    return f"{guide_text[:start]}{core_block}{guide_text[end:]}"


def _sync_guide(task_system_dir: Path, *, dry_run: bool = False, action_log: list[str] | None = None) -> None:
    """Ensure local mapping guide exists and refresh core references section."""
    target_guide = task_system_dir / "backend_contract_goal_mapping_guide.md"

    if not target_guide.exists() and SOURCE_GUIDE_PATH.exists():
        _copy_file(SOURCE_GUIDE_PATH, target_guide, dry_run=dry_run, action_log=action_log)
        typer.echo(f"  Created guide from template: {target_guide}")

    if not target_guide.exists():
        typer.echo(f"  Skipped guide sync; guide not found at {target_guide}")
        return

    original = target_guide.read_text(encoding="utf-8")
    refreshed = _refresh_core_contracts_section(original)
    if refreshed != original:
        if dry_run:
            typer.echo(f"  [dry-run] Would refresh core contract references in {target_guide}")
        else:
            target_guide.write_text(refreshed, encoding="utf-8")
            if action_log is not None:
                action_log.append(str(target_guide))
        typer.echo(f"  Refreshed core contract references in {target_guide}")
    else:
        typer.echo(f"  Guide core section unchanged: {target_guide}")


@cli.command()
def main(
    output_dir: Path = typer.Option(Path("."), "--output-dir", "-o", help="Directory that will receive the backend/ layout"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files"),
    sync_contracts: bool = typer.Option(
        False,
        "--sync-contracts",
        help="Refresh canonical contracts into backend/architecture/",
    ),
    sync_guide: bool = typer.Option(
        False,
        "--sync-guide",
        help="Refresh backend_contract_goal_mapping_guide.md core references",
    ),
    sync_docs: bool = typer.Option(
        False,
        "--sync-docs",
        help="Sync backend docs workflow templates and README files",
    ),
    sync_skills: bool = typer.Option(
        False,
        "--sync-skills",
        help="Sync backend skills system files",
    ),
    sync_all: bool = typer.Option(
        False,
        "--sync-all",
        help="Run contracts + guide + docs + skills sync in one command",
    ),
    preserve_local: bool = typer.Option(
        True,
        "--preserve-local/--no-preserve-local",
        help="Keep existing *_local.md files unchanged",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show planned actions without writing files",
    ),
    validate: bool = typer.Option(
        False,
        "--validate",
        help="Validate required docs/skills sync files after sync",
    ),
) -> None:
    root = output_dir.resolve()
    backend_dir = root / "backend"
    architecture_dir = backend_dir / "architecture"
    task_system_dir = backend_dir / "task_system"
    app_dir = backend_dir / "app"
    docs_dir = backend_dir / "docs"
    skills_dir = backend_dir / "skills"
    action_log: list[str] = []
    counts = {
        "contracts": 0,
        "docs": 0,
        "skills": 0,
        "stubs": 0,
        "version_files": 0,
    }
    selected_flags = [
        name
        for enabled, name in [
            (sync_contracts, "--sync-contracts"),
            (sync_guide, "--sync-guide"),
            (sync_docs, "--sync-docs"),
            (sync_skills, "--sync-skills"),
            (sync_all, "--sync-all"),
            (preserve_local, "--preserve-local"),
            (force, "--force"),
            (dry_run, "--dry-run"),
            (validate, "--validate"),
        ]
        if enabled
    ]

    for path in [backend_dir, architecture_dir, task_system_dir, app_dir, docs_dir, skills_dir]:
        if dry_run:
            typer.echo(f"  [dry-run] Would ensure directory {path}")
        else:
            path.mkdir(parents=True, exist_ok=True)

    readme = backend_dir / "README.md"
    if not readme.exists() or force:
        if dry_run:
            typer.echo(f"  [dry-run] Would write {readme}")
        else:
            readme.write_text(
                "# Backend\n\nThis folder holds the backend-encapsulated architecture, tooling, application code, and docs.\n",
                encoding="utf-8",
            )

    contracts_readme = architecture_dir / "README.md"
    if not contracts_readme.exists() or force:
        if dry_run:
            typer.echo(f"  [dry-run] Would write {contracts_readme}")
        else:
            contracts_readme.write_text(
                "# Backend Contracts\n\nCanonical backend contracts live here.\n",
                encoding="utf-8",
            )

    task_system_readme = task_system_dir / "README.md"
    if not task_system_readme.exists() or force:
        if dry_run:
            typer.echo(f"  [dry-run] Would write {task_system_readme}")
        else:
            task_system_readme.write_text(
                "# Backend Task System\n\nBackend-local resolver/bootstrap tooling lives here.\n",
                encoding="utf-8",
            )

    contracts_version = backend_dir / "contracts.version"
    version_action_log: list[str] = []
    _write_version_file(
        contracts_version,
        "application_contracts/backend@unversioned\n",
        force=force,
        dry_run=dry_run,
        action_log=version_action_log,
    )
    _write_version_file(
        backend_dir / "docs.version",
        "application_contracts/backend/docs@unversioned\n",
        force=force,
        dry_run=dry_run,
        action_log=version_action_log,
    )
    _write_version_file(
        backend_dir / "skills.version",
        "application_contracts/backend/skills@unversioned\n",
        force=force,
        dry_run=dry_run,
        action_log=version_action_log,
    )
    counts["version_files"] = len(version_action_log)
    action_log.extend(version_action_log)

    if sync_all:
        sync_contracts = True
        sync_guide = True
        sync_docs = True
        sync_skills = True
        if "--sync-all" not in selected_flags:
            selected_flags.append("--sync-all")

    if sync_contracts:
        counts["contracts"] = _sync_contracts(architecture_dir, dry_run=dry_run, action_log=action_log)

    # Scaffold *_local.md stubs so the extension pattern is pre-wired.
    # In preserve-local mode, existing local companion docs are never overwritten.
    counts["stubs"] = _scaffold_local_stubs(
        architecture_dir,
        force=(force and not preserve_local),
        dry_run=dry_run,
        action_log=action_log,
    )

    if sync_guide:
        _sync_guide(task_system_dir, dry_run=dry_run, action_log=action_log)

    if sync_docs:
        counts["docs"] = _sync_docs(docs_dir, dry_run=dry_run, action_log=action_log)

    if sync_skills:
        counts["skills"] = _sync_skills(skills_dir, dry_run=dry_run, action_log=action_log)

    validation_status = "not_run"
    if validate:
        if dry_run:
            typer.echo("  [dry-run] Validation skipped (no files written)")
            validation_status = "skipped_dry_run"
        else:
            status = _validate_sync(backend_dir)
            validation_status = "passed" if status == 0 else "failed"
            if status != 0:
                raise typer.Exit(status)

    if not dry_run:
        report_path = _write_sync_report(
            backend_dir,
            root=root,
            flags=selected_flags,
            counts=counts,
            validation_ran=validate,
            validation_status=validation_status,
            action_log=action_log,
        )
        typer.echo(f"  Wrote sync report: {report_path}")

    typer.echo(f"Initialized backend umbrella at {backend_dir}")


if __name__ == "__main__":
    cli()