# Run Scripts

This folder contains executable entrypoints for backend task-system workflows.

## Scripts

- `bootstrap_backend_system.py`: Initialize the backend umbrella layout in a target repository.
- `bootstrap.py`: Generate a backend application from the contract bootstrap phases.
- `check_backend_contract_references.py`: Validate guide references to backend contracts.

## Usage

Run from `backend/task_system` so paths remain predictable.

Initialize backend umbrella structure in a fresh repo root:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
```

Sync canonical contracts into an existing local app backend:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-contracts
```

Sync backend docs workflow templates into local app backend docs:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-docs
```

Sync backend skills system into local app backend skills:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-skills
```

Refresh the local guide core references section:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-guide
```

Run both syncs and keep local companion docs untouched:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-contracts --sync-guide --preserve-local
```

Sync everything for a fully encapsulated local backend (contracts + guide + docs + skills):

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-all --preserve-local --validate
```

### bootstrap_backend_system flags

- `--output-dir`, `-o`: Target repository root where `backend/` will be created or updated.
- `--force`, `-f`: Overwrite scaffold files that already exist.
- `--sync-contracts`: Copy canonical numbered contracts into `backend/architecture/`.
- `--sync-guide`: Refresh the core-contract section inside `backend/task_system/backend_contract_goal_mapping_guide.md`.
- `--sync-docs`: Sync docs workflow templates and README files into `backend/docs/`.
- `--sync-skills`: Sync backend skill files into `backend/skills/`.
- `--sync-all`: Shortcut to run contracts + guide + docs + skills sync.
- `--preserve-local` / `--no-preserve-local`: Keep or allow overwrite of existing `*_local.md` files when stubs are scaffolded.
- `--dry-run`: Print planned file operations without writing.
- `--validate`: Validate required docs/skills files after sync.

Every non-dry-run run writes a timestamped audit artifact:

```
backend/sync_reports/SYNC_REPORT_<YYYYMMDD_HHMMSS>.md
```

The report includes: run metadata (root, target, flags), file counts (contracts, docs, skills, stubs, version files), a full list of every path written, and validation result when `--validate` was passed.

### When to run which mode

- First-time setup (new repo):

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
```

- Core contracts changed upstream:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-contracts --sync-guide --preserve-local
```

- Full local encapsulation sync (recommended before testing in local app):

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --sync-all --preserve-local --validate
```

- Rebuild scaffolding aggressively:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root --force --sync-contracts --sync-guide --no-preserve-local
```

List available app bootstrap options:

```bash
cd backend/task_system
python3 run/bootstrap.py --help
```

Run selected bootstrap phases:

```bash
cd backend/task_system
python3 run/bootstrap.py --app-name my_app --target /tmp --phase 1-4
```

Validate backend contract references in guides:

```bash
cd backend/task_system
python3 run/check_backend_contract_references.py
```

Run full bootstrap:

```bash
cd backend/task_system
python3 run/bootstrap.py --app-name my_app --target /tmp --phase all
```

## Parallel layout flow (backend system + app build)

Use the same repo root as target for the system bootstrap, then point app bootstrap
to the `backend/app` subfolder created by that step:

```bash
cd backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
python3 run/bootstrap.py --app-name my_app --target /path/to/new-app-root/backend/app --phase all
```

`--target` and `--output-dir` are equivalent for `run/bootstrap.py`.

For `run/bootstrap_backend_system.py`, `--sync-contracts` updates canonical contract files only.
Local companion docs (`*_local.md`) are preserved by default.