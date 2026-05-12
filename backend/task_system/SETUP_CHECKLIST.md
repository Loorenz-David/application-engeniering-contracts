# Backend Encapsulation Setup Checklist

Use this checklist when bootstrapping a new app repo with backend encapsulation.

## Folder layout

- [ ] Create `backend/architecture/`
- [ ] Create `backend/task_system/`
- [ ] Create `backend/app/`
- [ ] Create `backend/docs/`

## Contract placement

- [ ] Place canonical backend contracts in `backend/architecture/`
- [ ] Ensure contract IDs remain stable (`01_...`, `02_...`, ...)
- [ ] Keep `backend/architecture/` contracts-only (no tooling scripts)

## Tooling placement

- [ ] Place resolver/bootstrap tooling in `backend/task_system/`
- [ ] Add backend-local goal mapping guide
- [ ] Ensure resolver points to `../architecture/`

## Version governance

- [ ] Pin contract version/tag in backend repo docs or config
- [ ] Define upgrade process for contract-version bumps
- [ ] Add contract-reference validation to CI

## Bootstrap sync — first-time setup

Run this once after creating the new app repo:

```bash
cd /path/to/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py --output-dir /path/to/new-app-root
```

- [ ] Confirm `backend/architecture/` contains numbered contract files
- [ ] Confirm `backend/task_system/backend_contract_goal_mapping_guide.md` was created
- [ ] Confirm `backend/docs/` contains lifecycle folder tree and templates
- [ ] Confirm `backend/skills/` contains domain + cross-cutting skill files and `skill_router.md`
- [ ] Confirm `backend/skills/local/` placeholder folder exists (for app-specific skills)

## Bootstrap sync — full encapsulation sync

Run `--sync-all` to propagate all upstream changes (contracts, guide, docs, skills) into the local app:

```bash
cd /path/to/Application_contracts/backend/task_system
python3 run/bootstrap_backend_system.py \
  --output-dir /path/to/new-app-root \
  --sync-all \
  --preserve-local \
  --validate
```

- [ ] Run before starting any multi-agent session on the local app
- [ ] Check terminal output: "Validation OK" or fix reported missing files
- [ ] Find the new `backend/sync_reports/SYNC_REPORT_<YYYYMMDD_HHMMSS>.md` and confirm counts look correct
- [ ] Commit the sync report alongside any changed contracts/skills

## Bootstrap sync — targeted updates

Use targeted flags when only one layer changed upstream:

| What changed upstream | Flag(s) to use |
|---|---|
| Contract file(s) only | `--sync-contracts` |
| Guide core-contract section | `--sync-guide` |
| Docs templates / README | `--sync-docs` |
| Skill files | `--sync-skills` |
| Everything | `--sync-all` |

- [ ] After any targeted sync, run `--validate` to confirm required files are present
- [ ] Review the generated sync report in `backend/sync_reports/` before committing

## Working mode

- [ ] Open backend repo as its own VS Code workspace
- [ ] Use backend-local mapping guide as default entrypoint
- [ ] Use cross-layer routing only for explicitly fullstack tasks
