from pathlib import Path

import typer

from bootstrap.writer import append_once, touch_file as _touch, write_file as _write


def _phase13_ci_cd(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 13 - CI/CD ------------------------------------------------")

    _touch(root / ".github" / "workflows" / ".gitkeep", force=force)

    _write(root / ".github" / "workflows" / "ci.yml", """\
name: ci

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements-dev.txt
      - run: make lint

  format:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements-dev.txt
      - run: make format

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: app_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres -d app_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
      redis:
        image: redis:7
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
    env:
      ENVIRONMENT: testing
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app_test
      REDIS_URL: redis://127.0.0.1:6379/0
      REDIS_KEY_PREFIX: app_ci
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements-dev.txt
      - run: make test

  docker-validation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose version
      - run: python scripts/validate_bootstrap.py

  migrations-health:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements-dev.txt
      - run: python -m alembic upgrade head
      - run: python scripts/wait_for_services.py
""", force=force)

    _write(root / "scripts" / "ci_validate.py", """\
from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    run(["make", "lint"])
    run(["make", "format"])
    run(["make", "test"])
    run([sys.executable, "scripts/validate_bootstrap.py"])


if __name__ == "__main__":
    main()
""", force=force)

    append_once(
        root / "Makefile",
        "\n# CI and validation\n"
        "lint:\n"
        "\tpython -m ruff check .\n\n"
        "format:\n"
        "\tpython -m ruff format .\n\n"
        "validate:\n"
        "\tpython scripts/ci_validate.py\n",
    )

    _write(root / ".env.validation", """\
ENVIRONMENT=validation
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app_validation
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=app_validation
UVICORN_RELOAD=0
""", force=force)
