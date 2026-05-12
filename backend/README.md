# Backend Umbrella

This folder is the backend-encapsulated entry surface for new applications.

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

## Fresh start workflow (including Git)

Assume a fresh folder named `Manager-app` and no Git history yet.

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
