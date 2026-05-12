# Backend Umbrella

This folder is the backend-encapsulated entry surface for new applications.

## Intended structure

- `architecture/` -> backend contracts only
- `task_system/` -> backend resolver/bootstrap tooling only
- `app/` -> runtime backend codebase (created per app)
- `docs/` -> backend app docs (created per app)

## Separation rule

- `architecture/` stays contracts-only.
- `task_system/` consumes contracts and guides, but does not define canonical contracts.

## Fresh start workflow (including Git)

Assume a fresh folder named `Manager-app` and no Git history yet.

### 1) Create the repo folder and initialize Git

```bash
mkdir -p /path/to/Manager-app
cd /path/to/Manager-app
git init
```

### 2) Bootstrap backend system layout

Run from this contracts workspace so the scripts can stamp your new repo:

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
/Users/davidloorenz/Desktop/Developer/Application_contracts/.venv/bin/python run/bootstrap_backend_system.py --output-dir /path/to/Manager-app
```

This creates:

- `backend/architecture/`
- `backend/task_system/`
- `backend/app/`
- `backend/docs/`
- `backend/skills/`
- `backend/contracts.version`
- `backend/docs.version`
- `backend/skills.version`

### 3) Generate backend app code inside backend/app

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
/Users/davidloorenz/Desktop/Developer/Application_contracts/.venv/bin/python run/bootstrap.py --app-name manager_app --target /path/to/Manager-app/backend/app --phase all
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

When canonical contracts change upstream, refresh your local app safely:

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
/Users/davidloorenz/Desktop/Developer/Application_contracts/.venv/bin/python run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-contracts --sync-guide --preserve-local
```

This updates canonical contract files and guide core references while preserving local `*_local.md` companion files by default.

## Ongoing sync for full local encapsulation (contracts + docs + skills)

To keep local app backend docs architecture and skills in sync with this source repo:

```bash
cd /Users/davidloorenz/Desktop/Developer/Application_contracts/backend/task_system
/Users/davidloorenz/Desktop/Developer/Application_contracts/.venv/bin/python run/bootstrap_backend_system.py --output-dir /path/to/Manager-app --sync-all --preserve-local --validate
```

Use `--dry-run` first to preview changes without writing.

## Additional run docs

Detailed command references are in:

- `task_system/run/README.md`
- `task_system/README.md`

## Skills system

Backend skill definitions live in:

- `skills/README.md`

Use skills for recurring intents and keep mapping-guide routing as fallback for
new or ambiguous tasks.
