from pathlib import Path

import typer

from bootstrap.writer import write_file as _write


def _phase4_container_runtime(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 4 - Container Runtime --------------------------------------")

    # Container runtime contract for a modular-monolith deployment model.
    # This remains single-app architecture: one backend app process + one worker.
    _write(root / "Dockerfile", """\
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

WORKDIR /app

# Keep image lean and deterministic.
RUN apt-get update \\
    && apt-get install -y --no-install-recommends build-essential curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The runtime defaults to the backend process. Worker uses the same image with
# a different command in docker-compose.yml.
CMD ["python", "run.py"]
""", force=force)

    _write(root / ".dockerignore", """\
# VCS
.git
.gitignore

# Python
__pycache__/
*.py[cod]
*.so
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Virtual env and local tooling
.venv/
venv/
.python-version

# Local/dev files
.env
.env.*
*.log
.DS_Store

# Build outputs
dist/
build/
""", force=force)

    _write(root / "docker-compose.yml", f"""\
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: {a}
    ports:
      - "${{POSTGRES_PORT:-5432}}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d {a}"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "${{REDIS_PORT:-6379}}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - redis_data:/data

  backend:
    build:
      context: .
      dockerfile: Dockerfile
    command: python run.py
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/{a}
      REDIS_URL: redis://redis:6379/0
      UVICORN_RELOAD: "0"
    ports:
      - "${{PORT:-5000}}:5000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2)"]
      interval: 10s
      timeout: 5s
      retries: 12
    profiles:
      - app

  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: python worker.py
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/{a}
      REDIS_URL: redis://redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    profiles:
      - app

volumes:
  postgres_data:
  redis_data:
""", force=force)

    _write(root / "Makefile", """\
.PHONY: dev-up dev-up-full dev-down dev-logs

# Hybrid mode: run app locally, infra in Docker.
dev-up:
\tdocker compose up -d postgres redis

# Full containerized mode: backend + worker + infra.
dev-up-full:
\tdocker compose --profile app up -d

dev-down:
\tdocker compose down

dev-logs:
\tdocker compose logs -f
""", force=force)

    _write(root / "README.md", f"""\
# {a}

## Runtime Modes

### Hybrid local mode (default)

Run backend locally; run infra in Docker:

```bash
make dev-up
cp .env.example .env
alembic upgrade head
python run.py
```

### Full containerized mode

Run backend + worker + PostgreSQL + Redis in Docker Compose:

```bash
make dev-up-full
```

Or directly:

```bash
docker compose --profile app up -d
```

### Validation mode (isolated)

Bootstrap validation intentionally uses dynamic host ports so it does not collide
with services already running on a developer machine.

```bash
python scripts/validate_bootstrap.py
```

### Shutdown

```bash
make dev-down
```

Or directly:

```bash
docker compose down
```

## Why dynamic validation ports exist

- Developer machines often already use 5432/6379/5000.
- Validation must run headless and deterministic in CI and local AI-agent loops.
- Isolation prevents accidental coupling to host-installed services.

## Deterministic runtime principles

- Health checks gate startup order for PostgreSQL and Redis.
- Services fail loudly when dependencies are unavailable.
- Environment contracts are explicit via `.env` and compose overrides.
- Runtime topology is modular-monolith-first: one backend app + one worker.
""", force=force)
