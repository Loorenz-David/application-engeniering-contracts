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

## Working mode

- [ ] Open backend repo as its own VS Code workspace
- [ ] Use backend-local mapping guide as default entrypoint
- [ ] Use cross-layer routing only for explicitly fullstack tasks
