# Backend Resolver Entrypoint

Use this as the default backend contract-selection entrypoint in a backend-only VS Code session.

## Primary guide

- `backend_contract_goal_mapping_guide.md`

## Contract location

- `../architecture/`

## Bootstrap entrypoint

- `run/bootstrap.py`

## Validation command

```bash
cd backend/task_system
python3 run/check_backend_contract_references.py
```

## Operating rule

- Select backend contracts here first.
- Add cross-layer routing only when the goal is explicitly fullstack.
