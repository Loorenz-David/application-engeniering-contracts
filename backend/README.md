# Backend Umbrella

This folder is the backend-encapsulated entry surface for new applications.

## Dev quick-start — running the full stack

From `backend/app/` — one terminal per process:

```bash
# 1. API server
make run

# 2. Task router  (routes tasks via Postgres LISTEN/NOTIFY)
make task-router

# 3. Worker       (executes tasks from the queue)
make worker-dev

# 4. Delayed scheduler   (one-shot scheduled tasks)
make delayed-scheduler

# 5. Recurring scheduler (cron-style tasks)
make recurring-scheduler
```

Minimum for basic development: terminals 1–3.  
Terminals 4–5 only matter when testing scheduled task features.

Run the bootstrap test suite (from `backend/`):

```bash
bash tests/bootstrap_tests/run_all.sh
```

`run_all.sh` starts all processes automatically if they are not already running.

---

## Terminology

Use these terms consistently:

- Canonical contracts source: the authoritative contracts and tooling source (this `Application_contracts` backend workspace).
- Generated backend runtime: the app repo's `backend/` folder used to run the application.
- Local encapsulated copies: synchronized copies of contracts/docs/skills inside the generated backend runtime.

## Supported repository modes

### Mode A - External Canonical Contracts Repo (recommended)

Structure:

```text
/Application_contracts
/Manager-app
```

How it works:

- Contracts and tooling are maintained centrally in `/Application_contracts`.
- Multiple app repositories can consume the same canonical source.
- Bootstrap and sync commands execute from the external contracts repo.
- Best choice for long-term scaling and multi-app governance.

### Mode B - Self-contained Application Repo

Structure:

```text
Manager-app/
├── application_contracts/
├── backend/
├── frontend/
└── test/
```

How it works:

- Contracts and tooling are vendored into the app repo under `application_contracts/`.
- Useful for isolated projects, pilots, or early-stage development.
- Bootstrap and sync commands execute from the embedded local contracts path.

## Prerequisites

The bootstrap script requires `typer` (its only third-party dependency). All other imports are Python stdlib.

Install it once into a venv before running any bootstrap or sync command:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install typer
```

Then activate that venv before every run:

```bash
source /path/to/.venv/bin/activate
python3 run/bootstrap_backend_system.py ...
```

The script does **not** auto-install dependencies. `python3` in all commands below refers to whichever interpreter is active in your shell.

Generated app config uses `SettingsConfigDict(..., extra="ignore")` so runtime-only env vars in `.env*` files do not fail startup validation.

Generated app config also uses explicit `Field(..., alias="ENV_VAR")` mappings for settings keys so Pydantic v2 resolves environment variables deterministically across app runtime, Alembic, workers, and CI.

Generated app config supports `APP_ENV` profile selection (`development`, `testing`, `validation`, `production`) so commands can load only the intended `.env*` profile and avoid cross-file overrides.

Use `APP_ENV` as a prefix before app runtime commands:

```bash
APP_ENV=development alembic upgrade head
APP_ENV=development python run.py
APP_ENV=validation python scripts/validate_bootstrap.py
```

## Fresh start workflow (including Git)

Assume a fresh folder named `Manager-app` and no Git history yet.

For a complete copy-paste-ready walkthrough, see **[backend/task_system/README.md — Complete fresh-start workflow](task_system/README.md#complete-fresh-start-workflow-copy-paste-ready)**.

Otherwise, follow the steps below:

### 1) Create the repo folder and initialize Git

```bash
mkdir -p /path/to/Manager-app
cd /path/to/Manager-app
git init
```

### 2) Bootstrap backend system layout

> **Pick one mode for this repo shape.** If your app repo already contains `application_contracts/` (Mode B), skip the Mode A command entirely. Mode A is only for setups where contracts live in a separate `/Application_contracts` repo.

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app
```

This creates or manages:

- `backend/architecture/`
- `backend/task_system/`
- `backend/app/`
- `backend/docs/`
- `backend/skills/`
- `backend/contracts.version`
- `backend/docs.version`
- `backend/skills.version`

### Important bootstrap target warning

- Set `--output-dir` to the repository root in normal usage.
- The bootstrap script creates and manages `backend/` for you.
- Avoid targeting `/path/to/Manager-app/backend` directly unless you intentionally want a nested layout.
- If a manually created `backend/` already exists with conflicting structure, nested backend folders can be created accidentally.
- Recommended practice: let the bootstrap script own backend structure creation from repo root.

### 3) Generate backend app code inside backend/app

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap.py --app-name manager_app --target /path/to/Manager-app/backend/app --phase all
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap.py --app-name manager_app --target /path/to/Manager-app/backend/app --phase all
```

### 4) Validate guide references in the generated repo

```bash
cd /path/to/Manager-app/backend/task_system
python3 run/check_backend_contract_references.py
```

### 5) Create first commit in Manager-app

```bash
cd /path/to/Manager-app
git add .
git commit -m "Bootstrap backend umbrella and app scaffold"
```

### 6) Connect GitHub remote and push

```bash
cd /path/to/Manager-app
git branch -M main
git remote add origin git@github.com:<your-org-or-user>/Manager-app.git
git push -u origin main
```

## Post-generation app setup and launch

After regenerating Phase 1 with an updated bootstrap generator (or after a fresh `--phase all` generation), follow these steps to setup and run the generated application.

### Regenerate Phase 1 (if updating generator code)

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap.py --app-name manager_app --target /path/to/Manager-app/backend/app --phase 1 --force
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap.py --app-name manager_app --target /path/to/Manager-app/backend/app --phase 1 --force
```

### Setup environment profile

Copy the example environment file to the active profile:

```bash
cd /path/to/Manager-app/backend/app
cp .env.example .env
```

The `.env` file is loaded when `APP_ENV=development` is set. For other profiles (`testing`, `validation`, `production`), corresponding `.env.testing`, `.env.validation`, `.env.production` files are available.

### Start Docker services

Start PostgreSQL 17 and Redis 7 containers:

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make dev-up
```

Wait for services to be healthy. Check logs:

```bash
APP_ENV=development make dev-logs
```

### Initialize database

Wait for postgres to be ready:

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make db-init
```

### Run database migrations

Apply all pending Alembic migrations:

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make db-migrate
```

### Apply Postgres triggers

Install the LISTEN/NOTIFY trigger (run once after migrations, re-run after schema re-creation):

```bash
APP_ENV=development make db-triggers
```

### Start the application

Launch the FastAPI app (port auto-detected, defaults to 8000):

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make run
```

Verify it's running:

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","services":{"db":"ok","redis":"ok"}}
```

### Complete workflow

Or combine all steps in one go:

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make dev-up && make db-init && make db-migrate && make db-triggers && make run
```

### Production deployment

Before every deploy to production, run migrations and triggers:

```bash
make pre-deploy   # alembic upgrade head + apply_db_triggers.py
```

Then start all processes. The generated `Procfile` at the app root defines all five:

```
web:                  python run.py
worker:               python worker.py
task-router:          python my_app/workers/task_router_process.py
delayed-scheduler:    python my_app/workers/delayed_scheduler_runner.py
recurring-scheduler:  python my_app/workers/recurring_scheduler_runner.py
```

Drive it with `supervisord`, `honcho`, or any Procfile-compatible process manager.

Hot reload is **off by default** (`UVICORN_RELOAD=0`). For local development, `.env.local` sets `UVICORN_RELOAD=1` automatically.

### Stop services (when done)

To stop and clean up Docker containers:

```bash
cd /path/to/Manager-app/backend/app
APP_ENV=development make dev-down
```

### View available Makefile targets

See all available development commands:

```bash
cd /path/to/Manager-app/backend/app
make help
```

## Ongoing sync when core contracts change

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-contracts --sync-guide --preserve-local
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-contracts --sync-guide --preserve-local
```

This updates canonical contract files and guide core references while preserving local `*_local.md` companion files by default.

## Ongoing sync for full local encapsulation (contracts + docs + skills)

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-all --preserve-local --validate
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-all --preserve-local --validate
```

Use `--dry-run` first to preview changes without writing.

## Sync behavior in self-contained mode

- Sync commands still work in the same way.
- Source and target now live in the same repository.
- Keep a clear boundary between canonical contracts source (`application_contracts/backend/architecture/`) and generated backend runtime (`backend/architecture/`).
- Avoid editing both copies in one change without an explicit migration intent, or ownership becomes ambiguous.

## Additional run docs

Detailed command references are in:

- `task_system/run/README.md`
- `task_system/README.md`

## Skills system

Backend skill definitions live in:

- `skills/README.md`

Use skills for recurring intents and keep mapping-guide routing as fallback for new or ambiguous tasks.
