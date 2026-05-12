from pathlib import Path

import typer


def write_file(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        typer.echo(f"  skip    {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    typer.echo(f"  create  {path}")


def touch_file(path: Path, *, force: bool) -> None:
    write_file(path, "", force=force)


def mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def append_once(path: Path, content: str, *, label: str | None = None) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if content.strip() in current:
        typer.echo(f"  skip    {label or path} (already updated)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current + content, encoding="utf-8")
    typer.echo(f"  update  {label or path}")


def replace_once(path: Path, old: str, new: str, *, label: str | None = None) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if new in current:
        typer.echo(f"  skip    {label or path} (already updated)")
        return
    if old not in current:
        typer.echo(f"  skip    {label or path} (pattern not found)")
        return
    path.write_text(current.replace(old, new, 1), encoding="utf-8")
    typer.echo(f"  update  {label or path}")
