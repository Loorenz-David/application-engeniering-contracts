# Backend Task System Home

`backend/task_system/` is the backend-local entrypoint layer for resolver and bootstrap workflows.

## Scope

- Backend routing/mapping guides
- Backend resolver entry scripts
- Backend bootstrap tasks and helpers
- Executable run scripts in `run/`

## Rules

- This folder should reference contracts in `../architecture/`.
- Keep backend-first routing here.
- Keep cross-layer routing in a separate location unless explicitly needed in backend workflows.

## Terminology

- Canonical contracts source: authoritative contracts/tooling source used to bootstrap and sync.
- Generated backend runtime: app repo `backend/` directory used by runtime services.
- Local encapsulated copies: synchronized contracts/docs/skills inside generated backend runtime.

## Supported repository modes

### Mode A - External Canonical Contracts Repo (recommended)

```text
/Application_contracts
/Manager-app
```

- Central source serves multiple app repositories.
- Run bootstrap/sync commands from `/Application_contracts/backend/task_system`.
- Best for long-term multi-app scaling.

### Mode B - Self-contained Application Repo

```text
Manager-app/
├── application_contracts/
├── backend/
├── frontend/
└── test/
```

- Contracts/tooling are vendored inside the app repo.
- Run bootstrap/sync commands from `Manager-app/application_contracts/backend/task_system`.
- Useful for isolated projects and early-stage development.

## Minimal starter files

- `backend_contract_goal_mapping_guide.md`
- `resolver_entrypoint.md`
- `run/check_backend_contract_references.py`
- `resolver.py`
- `task_types.py`
- `tasks/*.yaml`

## Source migration note

If you are migrating from this repository layout, base content on:

- `task_system/contract_goal_mapping_guide.md`
- `task_system/resolver.py`

## Local validation

Run backend-only contract reference validation:

```bash
cd backend/task_system
python3 run/check_backend_contract_references.py
```

## Backend resolver usage

Install resolver dependency once:

```bash
cd backend/task_system
python3 -m pip install -r requirements.txt
```

List local backend tasks:

```bash
cd backend/task_system
python3 resolver.py --list-tasks
```

Resolve by explicit task id:

```bash
cd backend/task_system
python3 resolver.py --task worker_driven_backend
```

Resolve by natural-language goal:

```bash
cd backend/task_system
python3 resolver.py "add replay and worker retry diagnostics"
```

## Backend bootstrap usage

Bootstrap the backend umbrella layout itself.

### Complete fresh-start workflow (copy-paste ready)

This is the full sequence from nothing to running app:

```bash
# Step 1: Bootstrap backend umbrella (creates architecture, docs, skills, app scaffold)
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root

# Step 2: Generate Phase 1 (config, models, routers, migrations, docker-compose)
python3 run/bootstrap.py --app-name my_app --target /path/to/new-app-root/backend/app --phase all

# Step 3: Set up Python environment in the generated app
cd /path/to/new-app-root/backend/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Step 4: Copy example env file
cp .env.example .env

# Step 5: Export development profile
export APP_ENV=development

# Step 6: Start Docker services (postgres + redis on auto-detected free ports)
make dev-up

# Step 7: Run database migrations
make db-migrate

# Step 8: Start the FastAPI app (runs on auto-detected free port)
make run

# Step 9: Test health endpoint in another terminal
curl http://localhost:8000/health
# Expected: {"status":"ok","services":{"db":"ok","redis":"ok"}}
```

### Make targets reference

After `cd /path/to/new-app-root/backend/app`:

```bash
make help              # Show all targets
make dev-up           # Start Docker services (postgres + redis)
make dev-down         # Stop Docker services
make dev-logs         # Stream Docker logs
make db-init          # Wait for postgres ready (rarely needed with --wait)
make db-migrate       # Run Alembic migrations
make run              # Start FastAPI app with hot reload
```

### APP_ENV profile selection

When running generated app commands, place `APP_ENV` before the command:

```bash
APP_ENV=development alembic upgrade head
APP_ENV=development python run.py
APP_ENV=validation python scripts/validate_bootstrap.py
```

> **Pick one mode for your repository shape.** If `application_contracts/` lives inside your app repo, use Mode B and ignore Mode A. Mode A applies only when contracts are hosted in a separate external repo.

Mode A (external canonical repo):

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
```

Mode B (self-contained app repo):

```bash
cd /path/to/Manager-app/application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/Manager-app
```

Sync canonical contracts into a local app backend architecture:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-contracts
```

Sync backend docs workflow templates into local backend docs:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-docs
```

Sync backend skills into local backend skills:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-skills
```

Refresh core references in the local mapping guide:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-guide
```

Sync all local backend encapsulation assets at once:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-all --preserve-local --validate
```

### Important bootstrap target warning

- Use repository root for `--output-dir` in normal usage.
- The bootstrap script creates/manages `backend/` under that root.
- Do not point `--output-dir` to `/path/to/new-app-root/backend` unless nested output is intentional.
- If a manually created `backend/` already exists with mismatched structure, nested backend directories can be produced.

### Self-contained mode sync behavior

- Sync commands are fully supported.
- Source and target live in one repository, so naming/ownership discipline is required.
- Keep canonical source edits in `application_contracts/backend/*` and generated runtime edits in `backend/*`.

Bootstrap-system flags:

- `--output-dir`, `-o`: Target repository root where `backend/` is created/updated.
- `--force`, `-f`: Overwrite scaffold files that already exist.
- `--sync-contracts`: Refresh canonical numbered contracts in `backend/architecture/`.
- `--sync-guide`: Refresh core references in `backend_contract_goal_mapping_guide.md`.
- `--sync-docs`: Sync docs workflow templates and README files in `backend/docs/`.
- `--sync-skills`: Sync backend skills files in `backend/skills/`.
- `--sync-all`: Run contracts + guide + docs + skills sync.
- `--preserve-local` / `--no-preserve-local`: Keep or allow overwrite of existing `*_local.md` files.
- `--dry-run`: Print planned operations without writing files.
- `--validate`: Verify required docs/skills files after sync.

Recommended run patterns:

- Initial setup:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
```

- Upstream core contracts changed:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-contracts --sync-guide --preserve-local
```

- Full local encapsulation refresh:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-all --preserve-local --validate
```

That creates:

- `backend/architecture/`
- `backend/task_system/`
- `backend/app/`
- `backend/docs/`
- `backend/skills/`
- `backend/contracts.version`
- `backend/docs.version`
- `backend/skills.version`

List available bootstrap phases:

```bash
cd backend/task_system
python3 run/bootstrap.py --help
```

Run bootstrap phases for a new backend app:

```bash
cd backend/task_system
python3 run/bootstrap.py --app-name my_app --target /tmp --phase 1-4
```

Run full backend bootstrap:

```bash
cd backend/task_system
python3 run/bootstrap.py --app-name my_app --target /tmp --phase all
```

To build app code inside the backend umbrella created by `run/bootstrap_backend_system.py`:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
python3 run/bootstrap.py --app-name my_app --target /path/to/new-app-root/backend/app --phase all
```

See `run/README.md` for script-specific usage.
